"""Provider-agnostic error taxonomy and result types for the LLM layer. gemini_client.py maps
google-genai exceptions into these so provider_chain.py / interpreter.py never touch SDK types.
"""

from dataclasses import dataclass
from typing import Protocol

from app.schemas.llm_output import NoteInterpretationLLM


class LLMError(Exception):
    """Base for all LLM-call failures."""


class LLMTransientError(LLMError):
    """429 / 5xx / timeout / connection — retry on the same or next model."""


class LLMAuthError(LLMError):
    """401/403/404 — this model/key combination is broken; circuit-break it."""


class LLMInvalidRequestError(LLMError):
    """400 — our request was malformed (should not normally happen); circuit-break it too."""


class LLMSafetyBlockedError(LLMError):
    """finish_reason in {SAFETY, PROHIBITED_CONTENT, RECITATION, OTHER} — treat as a failed attempt."""


class LLMTruncatedError(LLMError):
    """finish_reason == MAX_TOKENS."""


class LLMSchemaError(LLMError):
    """Response text did not parse into NoteInterpretationLLM."""


@dataclass
class LLMResult:
    parsed: NoteInterpretationLLM
    raw_text: str
    model: str
    latency_s: float


class LLMClient(Protocol):
    async def generate(
        self,
        model: str,
        system_prompt: str,
        contents: list[dict],
        timeout_s: float,
        thinking_budget: int | None,
    ) -> LLMResult: ...
