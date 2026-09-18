"""Thin wrapper around google-genai. Translates Gemini-specific behavior (finish_reason, safety
blocks, thinking budget, ClientError/ServerError) into the provider-agnostic taxonomy in base.py.

Confirmed against a real key (planning.md §4.3): which models exist and how they handle
`thinking_budget=0` shifts under the `-latest` aliases — e.g. `gemini-flash-lite-latest` rejected
`thinking_budget=0` with 400 INVALID_ARGUMENT at one point, while `gemini-flash-latest` accepted it.
Rather than let one model's current thinking-budget quirk permanently circuit-break it, a 400 that
occurs specifically when a thinking budget was requested gets one retry with no thinking_config at
all before being treated as a real invalid-request failure.
"""

import time

from pydantic import ValidationError

from app.llm.base import (
    LLMAuthError,
    LLMInvalidRequestError,
    LLMResult,
    LLMSafetyBlockedError,
    LLMSchemaError,
    LLMTransientError,
    LLMTruncatedError,
)
from app.schemas.llm_output import NoteInterpretationLLM

_OK_FINISH_REASONS = {"STOP"}
_TRUNCATED_FINISH_REASONS = {"MAX_TOKENS"}


class GeminiClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def _call(
        self,
        model: str,
        system_prompt: str,
        contents: list[dict],
        timeout_s: float,
        thinking_budget: int | None,
    ):
        from google.genai import errors as genai_errors
        from google.genai import types

        client = self._get_client()

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=NoteInterpretationLLM,
            temperature=0,
            max_output_tokens=4096,
            thinking_config=(types.ThinkingConfig(thinking_budget=thinking_budget) if thinking_budget is not None else None),
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

        try:
            return await client.aio.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.ClientError as e:
            code = getattr(e, "code", None)
            if code in (401, 403, 404):
                raise LLMAuthError(str(e)) from e
            if code == 429:
                raise LLMTransientError(str(e)) from e
            raise LLMInvalidRequestError(str(e)) from e
        except genai_errors.ServerError as e:
            raise LLMTransientError(str(e)) from e
        except TimeoutError as e:
            raise LLMTransientError(str(e)) from e
        except Exception as e:
            raise LLMTransientError(str(e)) from e

    async def generate(
        self,
        model: str,
        system_prompt: str,
        contents: list[dict],
        timeout_s: float,
        thinking_budget: int = 0,
    ) -> LLMResult:
        start = time.monotonic()
        try:
            response = await self._call(model, system_prompt, contents, timeout_s, thinking_budget)
        except LLMInvalidRequestError:
            response = await self._call(model, system_prompt, contents, timeout_s, thinking_budget=None)

        latency_s = time.monotonic() - start

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason_obj = getattr(candidates[0], "finish_reason", None)
            # finish_reason is an enum (e.g. types.FinishReason.STOP); str() on it yields
            # "FinishReason.STOP", not "STOP", so pull .name explicitly rather than str()-ing it.
            finish_reason = (getattr(finish_reason_obj, "name", None) or str(finish_reason_obj or "")).upper()
            if finish_reason in _TRUNCATED_FINISH_REASONS:
                raise LLMTruncatedError(f"finish_reason={finish_reason}")
            if finish_reason and finish_reason not in _OK_FINISH_REASONS:
                raise LLMSafetyBlockedError(f"finish_reason={finish_reason}")

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, NoteInterpretationLLM):
            return LLMResult(parsed=parsed, raw_text=response.text or "", model=model, latency_s=latency_s)

        raw_text = getattr(response, "text", None)
        if not raw_text:
            raise LLMSchemaError("empty response body")
        try:
            parsed_model = NoteInterpretationLLM.model_validate_json(raw_text)
        except ValidationError as e:
            raise LLMSchemaError(str(e)) from e
        return LLMResult(parsed=parsed_model, raw_text=raw_text, model=model, latency_s=latency_s)
