"""Scripted LLM client for offline tests (§10.1). Matches on the exact note text embedded in the
last user turn interpreter.py builds, so tests can hand it exactly the notes they're using."""

from app.llm.base import LLMResult, LLMTransientError
from app.schemas.llm_output import NoteInterpretationLLM


class FakeGeminiClient:
    def __init__(
        self,
        responses: dict[str, NoteInterpretationLLM] | None = None,
        fail_models: set[str] | None = None,
        default: NoteInterpretationLLM | None = None,
    ):
        self.responses = dict(responses or {})
        self.fail_models = set(fail_models or set())
        self.default = default
        self.calls: list[str] = []

    async def generate(
        self, model: str, system_prompt: str, contents: list[dict], timeout_s: float, thinking_budget: int = 0
    ) -> LLMResult:
        self.calls.append(model)
        if model in self.fail_models:
            raise LLMTransientError(f"simulated failure for {model}")

        note_text = None
        for turn in reversed(contents):
            text = turn["parts"][0]["text"]
            if "Interpret note" in text:
                note_text = text.rsplit("Interpret note", 1)[-1].split(":", 1)[-1].strip()
                break
        if note_text is None:
            note_text = contents[-1]["parts"][0]["text"].strip()

        parsed = self.responses.get(note_text, self.default)
        if parsed is None:
            raise LLMTransientError(f"no scripted response for note: {note_text!r}")
        return LLMResult(parsed=parsed, raw_text=parsed.model_dump_json(), model=model, latency_s=0.001)
