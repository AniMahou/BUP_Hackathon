"""Thin wrapper around google-genai. Translates Gemini-specific behavior (finish_reason, safety
blocks, thinking budget, ClientError/ServerError) into the provider-agnostic taxonomy in base.py.

Resilience features (all learned from live runs against real keys):
- Several API keys can be configured (GEMINI_API_KEYS=k1,k2). Each key/project has its own quota,
  so a 429 on one key is retried immediately on the next key before the model is given up on.
  A key that returned 429 is parked until the server-suggested retryDelay has passed.
- Some models reject `thinking_budget=0` with 400 INVALID_ARGUMENT. The first time that happens we
  remember it for that model and never send a thinking config to it again (no double call per note).
- 5xx / 503 "high demand" / 504 deadline errors are transient → the caller moves to the next model.
"""

import asyncio
import re
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
_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)
# Gemini rejects any request whose HTTP deadline is < 10 s with "400 INVALID_ARGUMENT: Manually set
# deadline ... is too short". So the HTTP deadline sent to Google is always >= 11 s, and our own
# (shorter) per-attempt budget is enforced client-side with asyncio.wait_for instead.
_MIN_PROVIDER_DEADLINE_S = 11.0


class LLMRateLimitedError(LLMTransientError):
    """Every configured key is currently rate-limited for this model."""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _short(e: Exception) -> str:
    """Provider error text trimmed for logs/traces (never contains the key)."""
    return str(e).split("{", 1)[0].strip()[:160] or type(e).__name__


class GeminiClient:
    def __init__(self, api_keys: list[str] | str):
        if isinstance(api_keys, str):
            api_keys = [api_keys] if api_keys else []
        self._keys = [k.strip() for k in api_keys if k and k.strip()]
        self._clients: dict[str, object] = {}
        self._key_parked_until: dict[tuple[str, str], float] = {}  # (key, model) -> monotonic time
        self._no_thinking_models: set[str] = set()
        self._rr = 0  # round-robin start index so load spreads across keys

    @property
    def configured(self) -> bool:
        return bool(self._keys)

    def _client_for(self, key: str):
        if key not in self._clients:
            from google import genai

            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    async def _call_once(self, key: str, model: str, system_prompt: str, contents: list[dict], timeout_s: float, thinking_budget: int | None):
        from google.genai import errors as genai_errors
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=NoteInterpretationLLM,
            temperature=0,
            max_output_tokens=4096,
            thinking_config=(types.ThinkingConfig(thinking_budget=thinking_budget) if thinking_budget is not None else None),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=int(max(_MIN_PROVIDER_DEADLINE_S, timeout_s) * 1000)),
        )
        try:
            return await asyncio.wait_for(
                self._client_for(key).aio.models.generate_content(model=model, contents=contents, config=config),
                timeout=max(0.5, timeout_s),
            )
        except genai_errors.ClientError as e:
            code = getattr(e, "code", None)
            if code == 429:
                m = _RETRY_DELAY_RE.search(str(e))
                raise LLMRateLimitedError(_short(e), float(m.group(1)) if m else None) from e
            if code in (401, 403, 404):
                raise LLMAuthError(_short(e)) from e
            raise LLMInvalidRequestError(_short(e)) from e
        except genai_errors.ServerError as e:
            raise LLMTransientError(_short(e)) from e
        except Exception as e:  # timeouts, connection resets, SDK quirks
            raise LLMTransientError(_short(e)) from e

    async def _call(self, model: str, system_prompt: str, contents: list[dict], timeout_s: float, thinking_budget: int | None):
        if not self._keys:
            raise LLMAuthError("no Gemini API key configured")
        if model in self._no_thinking_models:
            thinking_budget = None

        now = time.monotonic()
        n = len(self._keys)
        order = [self._keys[(self._rr + i) % n] for i in range(n)]
        self._rr = (self._rr + 1) % n
        available = [k for k in order if self._key_parked_until.get((k, model), 0.0) <= now]
        if not available:
            wait = min(self._key_parked_until[(k, model)] for k in order) - now
            raise LLMRateLimitedError(f"all keys rate-limited for {model}", max(0.0, wait))

        last_rl: LLMRateLimitedError | None = None
        for key in available:
            try:
                try:
                    return await self._call_once(key, model, system_prompt, contents, timeout_s, thinking_budget)
                except LLMInvalidRequestError:
                    if thinking_budget is None:
                        raise
                    # This model rejects the thinking config: remember and retry once without it.
                    self._no_thinking_models.add(model)
                    return await self._call_once(key, model, system_prompt, contents, timeout_s, None)
            except LLMRateLimitedError as e:
                self._key_parked_until[(key, model)] = time.monotonic() + (e.retry_after_s or 10.0)
                last_rl = e
                continue
        assert last_rl is not None
        raise last_rl

    async def generate(
        self,
        model: str,
        system_prompt: str,
        contents: list[dict],
        timeout_s: float,
        thinking_budget: int | None = None,
    ) -> LLMResult:
        start = time.monotonic()
        response = await self._call(model, system_prompt, contents, timeout_s, thinking_budget)
        latency_s = time.monotonic() - start

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason_obj = getattr(candidates[0], "finish_reason", None)
            # finish_reason is an enum; str() gives "FinishReason.STOP", so use .name.
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
            raise LLMSchemaError(str(e)[:300]) from e
        return LLMResult(parsed=parsed_model, raw_text=raw_text, model=model, latency_s=latency_s)
