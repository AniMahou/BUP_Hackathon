"""Per-model circuit breaker + timeout derivation (§4.3). The interpreter drives the actual
model-by-model, repair-by-repair loop (§4.8, §4.10) — this class just answers "is this model
worth trying right now, and for how long" and records auth/invalid-request failures so a broken
model/key combination isn't retried for 5 minutes.
"""

import time

from app.llm.base import LLMAuthError, LLMClient, LLMInvalidRequestError, LLMResult
from app.pipeline.deadline import Deadline

_AUTH_BREAKER_S = 300.0  # 401/403/404: model/key genuinely unusable


class ProviderChain:
    def __init__(self, client: LLMClient, models: list[str], primary_timeout_s: float, fallback_timeout_s: float):
        self.client = client
        self.models = models
        self.primary_timeout_s = primary_timeout_s
        self.fallback_timeout_s = fallback_timeout_s
        self._disabled_until: dict[str, float] = {}

    def is_disabled(self, model: str) -> bool:
        until = self._disabled_until.get(model)
        return until is not None and time.monotonic() < until

    def budget_for(self, model_index: int) -> float:
        return self.primary_timeout_s if model_index == 0 else self.fallback_timeout_s

    async def call_model(
        self, model: str, system_prompt: str, contents: list[dict], deadline: Deadline, budget_s: float, thinking_budget: int | None
    ) -> LLMResult:
        timeout_s = deadline.timeout_for(budget_s)
        if timeout_s <= 0.1:
            raise TimeoutError("request deadline exhausted before this model could be attempted")
        try:
            return await self.client.generate(
                model=model,
                system_prompt=system_prompt,
                contents=contents,
                timeout_s=timeout_s,
                thinking_budget=thinking_budget,
            )
        except LLMAuthError:
            self._disabled_until[model] = time.monotonic() + _AUTH_BREAKER_S
            raise
        except LLMInvalidRequestError:
            # A 400 is specific to this request (e.g. a model rejecting one config). Move on to the
            # next model for THIS call, but never switch the model off for other requests.
            raise
