"""Orchestrates §4 end to end for one request: builds per-note prompts, runs the model chain with
repairs (§4.8), falls back to the rule-based interpreter or no_op on total failure (§4.10), and
assembles the reported + applied directives (§4.9). One note = one independent async task; all
notes for a request run in parallel via asyncio.gather.
"""

import asyncio
import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.guardrails.assembler import NoteAssembly, assemble_note
from app.guardrails.cross_check import disagrees
from app.guardrails.normalizer import NormalizationError, normalize
from app.guardrails.rule_interpreter import interpret as rule_interpret
from app.guardrails.validator import validate
from app.llm.base import LLMError, LLMResult
from app.llm.cache import InterpretationCache, cache_key
from app.llm.gemini_client import GeminiClient
from app.llm.provider_chain import ProviderChain
from app.pipeline.deadline import Deadline
from app.schemas.directives import AppliedDirective, structured_adjustment_for
from app.schemas.request import BatteryInput, OptimizeRequest
from app.schemas.response import DirectiveInterpretation

_PROMPT_DIR = Path(__file__).parent / "prompts"


@functools.lru_cache(maxsize=1)
def load_system_prompt() -> str:
    return (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def load_repair_template() -> str:
    return (_PROMPT_DIR / "repair_prompt.md").read_text(encoding="utf-8")


def _battery_context_text(battery: BatteryInput) -> str:
    return (
        f"Battery: capacity={battery.capacity_kwh} kWh, base minimum={battery.minimum_energy_kwh} kWh, "
        f"initial={battery.initial_energy_kwh} kWh, max_charge={battery.max_charge_kwh_per_hour} kWh/h, "
        f"max_discharge={battery.max_discharge_kwh_per_hour} kWh/h."
    )


def _notes_context_text(all_notes: list[str], target_index: int) -> str:
    lines = [f"[{i}] {n}" + ("   <-- TARGET NOTE" if i == target_index else "") for i, n in enumerate(all_notes)]
    return "All operator notes for this scenario:\n" + "\n".join(lines)


def _user_turn_text(battery: BatteryInput, all_notes: list[str], target_index: int) -> str:
    return (
        _battery_context_text(battery)
        + "\n\n"
        + _notes_context_text(all_notes, target_index)
        + f"\n\nInterpret note [{target_index}]: {all_notes[target_index]}"
    )


@functools.lru_cache(maxsize=1)
def _few_shot_raw() -> dict:
    return json.loads((_PROMPT_DIR / "few_shot_examples.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def build_few_shot_contents() -> list[dict]:
    data = _few_shot_raw()
    default_battery = BatteryInput(**data["default_battery"])
    contents: list[dict] = []
    for ex in data["examples"]:
        battery = default_battery
        overrides = ex.get("battery_overrides")
        if overrides:
            battery = default_battery.model_copy(update=overrides)
        user_text = _user_turn_text(battery, [ex["note"]], 0)
        model_text = json.dumps(ex["expected"], separators=(",", ":"))
        contents.append({"role": "user", "parts": [{"text": user_text}]})
        contents.append({"role": "model", "parts": [{"text": model_text}]})
    return contents


@dataclass
class NoteTrace:
    note_index: int
    note_text: str
    stages: list[dict] = field(default_factory=list)

    def add(self, stage: str, **detail):
        self.stages.append({"stage": stage, **detail})


@dataclass
class InterpretationResult:
    entries: list[DirectiveInterpretation]
    applied: list[AppliedDirective]
    meta: dict
    traces: list[NoteTrace]
    assemblies: list[NoteAssembly]


_GUARDRAIL_RULE_REMINDERS = {
    "G5": "Non-no_op directives must have at least one hour, all in 0..23.",
    "G6": "solar_reduction's factor must be between 0 and 1 (the remaining usable fraction).",
    "G7": "minimum_battery_reserve's minimum_energy_kwh must be between 0 and the battery's capacity_kwh.",
    "G8": "max_grid_window's max_grid_kwh must be >= 0.",
    "G9": "solar_reduction, minimum_battery_reserve and max_grid_window all require a quantity.",
    "G10": "Your time_windows/quantity (evidence) and your final hours/factor/etc. must agree with each other.",
    "G11": "The number you extracted should actually appear in the note (as a digit, word, or percentage).",
    "G12": "affects_schedule must be false if and only if directive_type is no_op.",
}


class NoteInterpreter:
    def __init__(
        self,
        client: GeminiClient,
        model_chain: list[str],
        primary_timeout_s: float,
        fallback_timeout_s: float,
        max_repairs: int,
        thinking_budget: int,
        cache: InterpretationCache,
        prompt_version: str,
        enable_hedging: bool,
        enable_rule_fallback: bool,
    ):
        self.chain = ProviderChain(client, model_chain, primary_timeout_s, fallback_timeout_s)
        self.max_repairs = max_repairs
        self.thinking_budget = thinking_budget
        self.cache = cache
        self.prompt_version = prompt_version
        self.enable_hedging = enable_hedging
        self.enable_rule_fallback = enable_rule_fallback
        self.model_chain = model_chain

    async def interpret_request(self, req: OptimizeRequest, deadline: Deadline) -> InterpretationResult:
        tasks = [self._interpret_one(req, i, deadline) for i in range(len(req.operator_notes))]
        results = await asyncio.gather(*tasks)

        assemblies = [r[0] for r in results]
        entries = [a.entry for a in assemblies]
        applied: list[AppliedDirective] = []
        for a in assemblies:
            applied.extend(a.applied)
        traces = [r[1] for r in results]
        meta = {"per_note_model": [r[2] for r in results], "degraded": any(a.degraded for a in assemblies)}
        return InterpretationResult(entries=entries, applied=applied, meta=meta, traces=traces, assemblies=assemblies)

    async def correct_note(
        self,
        req: OptimizeRequest,
        note_index: int,
        deadline: Deadline,
        conflict_description: str,
        previous_assembly: NoteAssembly,
    ) -> tuple[NoteAssembly, NoteTrace]:
        """§4.8 constraint-correction loop: one extra targeted round for a note implicated in an
        LP infeasibility, using the same repair machinery but a different prompt. Falls back to
        the previous (uncorrected) assembly if this call fails — correction is best-effort."""
        trace = NoteTrace(note_index=note_index, note_text=req.operator_notes[note_index])
        battery = req.battery
        contents = build_few_shot_contents() + [
            {"role": "user", "parts": [{"text": _user_turn_text(battery, req.operator_notes, note_index)}]}
        ]
        template = (_PROMPT_DIR / "correction_prompt.md").read_text(encoding="utf-8")
        prompt_text = template.format(conflict_description=conflict_description)

        assembly, _used_model, ok = await self._call_and_validate(
            req, note_index, contents, deadline, trace, prompt_text_override=prompt_text
        )
        if not ok:
            trace.add("correction_failed_keeping_previous")
            return previous_assembly, trace
        return assembly, trace

    async def _interpret_one(
        self, req: OptimizeRequest, note_index: int, deadline: Deadline
    ) -> tuple[NoteAssembly, NoteTrace, str]:
        note_text = req.operator_notes[note_index]
        trace = NoteTrace(note_index=note_index, note_text=note_text)
        battery = req.battery

        key = cache_key(
            self.prompt_version,
            self.model_chain,
            battery.capacity_kwh,
            battery.initial_energy_kwh,
            battery.minimum_energy_kwh,
            battery.max_charge_kwh_per_hour,
            battery.max_discharge_kwh_per_hour,
            list(req.operator_notes),
            note_index,
        )
        cached = self.cache.get(key)
        if cached is not None:
            trace.add("cache_hit")
            return cached, trace, "cache"

        contents = build_few_shot_contents() + [
            {"role": "user", "parts": [{"text": _user_turn_text(battery, req.operator_notes, note_index)}]}
        ]

        assembly, used_model, ok = await self._call_and_validate(req, note_index, contents, deadline, trace)

        if not ok:
            if self.enable_rule_fallback:
                rule_result = rule_interpret(note_text, battery.capacity_kwh, battery.minimum_energy_kwh)
                if rule_result is not None:
                    trace.add("degraded_rule_interpreter", directive_type=rule_result.directive_type)
                    structured = structured_adjustment_for(
                        rule_result.directive_type,
                        rule_result.hours,
                        rule_result.factor,
                        rule_result.minimum_energy_kwh,
                        rule_result.max_grid_kwh,
                    )
                    entry = DirectiveInterpretation(
                        note_index=note_index,
                        applies=True,
                        directive_type=rule_result.directive_type,
                        structured_adjustment=structured,
                        explanation=rule_result.explanation,
                    )
                    applied_directive = AppliedDirective(
                        note_index=note_index,
                        type=rule_result.directive_type,
                        hours=rule_result.hours,
                        factor=rule_result.factor,
                        minimum_energy_kwh=rule_result.minimum_energy_kwh,
                        max_grid_kwh=rule_result.max_grid_kwh,
                        is_hedge=False,
                    )
                    assembly = NoteAssembly(entry=entry, applied=[applied_directive], degraded=True)
                    used_model = "rule_interpreter"
                    ok = True

        if not ok:
            trace.add("safe_fallback_no_op")
            entry = DirectiveInterpretation(
                note_index=note_index,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation="Note could not be interpreted safely; no adjustment applied.",
            )
            assembly = NoteAssembly(entry=entry, applied=[], degraded=True)
            used_model = "none"

        self.cache.put(key, assembly)
        return assembly, trace, used_model

    async def _call_and_validate(
        self,
        req: OptimizeRequest,
        note_index: int,
        base_contents: list[dict],
        deadline: Deadline,
        trace: NoteTrace,
        prompt_text_override: str | None = None,
    ) -> tuple[NoteAssembly, str, bool]:
        note_text = req.operator_notes[note_index]
        battery = req.battery
        system_prompt = load_system_prompt()

        for model_index, model in enumerate(self.chain.models):
            if self.chain.is_disabled(model):
                trace.add("model_skipped", model=model, reason="circuit_open")
                continue
            if deadline.expired():
                break

            contents = list(base_contents)
            if prompt_text_override:
                contents = contents[:-1] + [
                    {"role": "user", "parts": [{"text": contents[-1]["parts"][0]["text"] + "\n\n" + prompt_text_override}]}
                ]

            budget = self.chain.budget_for(model_index)
            repairs_left = self.max_repairs

            while True:
                try:
                    result: LLMResult = await self.chain.call_model(
                        model, system_prompt, contents, deadline, budget, self.thinking_budget
                    )
                except LLMError as e:
                    trace.add("llm_error", model=model, error=type(e).__name__, message=str(e))
                    break
                except Exception as e:
                    trace.add("llm_error", model=model, error=type(e).__name__, message=str(e))
                    break

                trace.add(
                    "llm_response",
                    model=model,
                    analysis=result.parsed.analysis,
                    directive_type=result.parsed.directive_type,
                    affects_schedule=result.parsed.affects_schedule,
                )

                try:
                    normalized = normalize(result.parsed, battery.capacity_kwh, battery.minimum_energy_kwh)
                except NormalizationError as e:
                    trace.add("normalization_error", code=e.code, message=e.message)
                    if repairs_left > 0:
                        contents = self._append_repair_turn(contents, result.raw_text, [f"{e.code}: {e.message}"])
                        repairs_left -= 1
                        continue
                    break

                issues = validate(result.parsed, normalized, note_text, battery.capacity_kwh)
                blocking = [i for i in issues if i.code in ("G1", "G5", "G9")]
                soft = [i for i in issues if i.code not in ("G1", "G5", "G9")]

                if issues:
                    trace.add("guardrail_issues", codes=[i.code for i in issues])

                if (blocking or soft) and repairs_left > 0:
                    error_lines = [f"{i.code}: {i.message}" for i in issues]
                    reminders = sorted({_GUARDRAIL_RULE_REMINDERS.get(i.code, "") for i in issues} - {""})
                    contents = self._append_repair_turn(contents, result.raw_text, error_lines, reminders)
                    repairs_left -= 1
                    trace.add("repair_requested", codes=[i.code for i in issues])
                    continue

                if blocking:
                    trace.add("guardrail_blocking_after_repairs", codes=[i.code for i in blocking])
                    break  # move to next model

                if soft:
                    trace.add("guardrail_soft_accept_with_fallback", codes=[i.code for i in soft])
                    normalized = self._apply_soft_fallback(normalized, soft, battery.capacity_kwh)

                regex_alt = disagrees(note_text, normalized.hours) if self.enable_hedging else None
                assembly = assemble_note(
                    note_index,
                    result.parsed,
                    normalized,
                    regex_alt,
                    self.enable_hedging,
                    battery.capacity_kwh,
                    battery.minimum_energy_kwh,
                )
                trace.add("assembled", directive_type=assembly.entry.directive_type, hours=(assembly.applied[0].hours if assembly.applied else []))
                return assembly, model, True

        return NoteAssembly(entry=None, applied=[]), "none", False  # type: ignore[arg-type]

    @staticmethod
    def _append_repair_turn(
        contents: list[dict], raw_model_text: str, error_lines: list[str], reminders: list[str] | None = None
    ) -> list[dict]:
        template = load_repair_template()
        repair_text = template.format(
            errors="\n".join(f"- {line}" for line in error_lines),
            rule_reminder="; ".join(reminders) if reminders else "re-read the directive rules in the system prompt.",
        )
        return contents + [
            {"role": "model", "parts": [{"text": raw_model_text}]},
            {"role": "user", "parts": [{"text": repair_text}]},
        ]

    @staticmethod
    def _apply_soft_fallback(normalized, soft_issues, capacity_kwh: float):
        for issue in soft_issues:
            if issue.code == "G6" and normalized.factor is not None:
                normalized.factor = min(1.0, max(0.0, normalized.factor))
            if issue.code == "G7" and normalized.minimum_energy_kwh is not None:
                normalized.minimum_energy_kwh = min(capacity_kwh, max(0.0, normalized.minimum_energy_kwh))
        return normalized
