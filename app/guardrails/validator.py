"""Section-08 guardrail validator (G1-G13, planning.md §4.6). Runs on one note's candidate
interpretation after channel-A normalization. Returns a list of issues; the interpreter decides
whether to repair, clamp/log, or accept based on issue.repairable and how many rounds are left.
"""

import math
from dataclasses import dataclass

from app.guardrails.normalizer import NormalizedNote
from app.guardrails.quantities import is_grounded
from app.schemas.directives import DIRECTIVE_TYPES
from app.schemas.llm_output import NoteInterpretationLLM

_TOL = 1e-6


@dataclass
class GuardrailIssue:
    code: str
    message: str
    repairable: bool = True


def validate(
    llm_out: NoteInterpretationLLM,
    normalized: NormalizedNote,
    note_text: str,
    capacity_kwh: float,
) -> list[GuardrailIssue]:
    issues: list[GuardrailIssue] = []
    dt = llm_out.directive_type

    # G1
    if dt not in DIRECTIVE_TYPES:
        issues.append(GuardrailIssue("G1", f"unknown directive_type {dt}"))
        return issues  # nothing else is safe to check

    # G5: non-no_op must have non-empty, valid hours
    if dt != "no_op":
        if not normalized.hours:
            issues.append(GuardrailIssue("G5", "non-no_op directive has no hours after normalization"))
        elif any(not (0 <= h <= 23) for h in normalized.hours):
            issues.append(GuardrailIssue("G5", "hours out of 0..23 range"))
        elif normalized.hours != sorted(set(normalized.hours)):
            issues.append(GuardrailIssue("G5", "hours not unique/ascending"))

    # G6: factor
    if dt == "solar_reduction":
        f = normalized.factor
        if f is None or not math.isfinite(f):
            issues.append(GuardrailIssue("G6", "factor missing or non-finite"))
        elif not (0.0 <= f <= 1.0):
            issues.append(GuardrailIssue("G6", f"factor {f} outside [0,1]"))

    # G7: minimum_energy_kwh
    if dt == "minimum_battery_reserve":
        m = normalized.minimum_energy_kwh
        if m is None or not math.isfinite(m):
            issues.append(GuardrailIssue("G7", "minimum_energy_kwh missing or non-finite"))
        elif not (0.0 <= m <= capacity_kwh + _TOL):
            issues.append(GuardrailIssue("G7", f"minimum_energy_kwh {m} outside [0, capacity]"))

    # G8: max_grid_kwh
    if dt == "max_grid_window":
        g = normalized.max_grid_kwh
        if g is None or not math.isfinite(g):
            issues.append(GuardrailIssue("G8", "max_grid_kwh missing or non-finite"))
        elif g < 0:
            issues.append(GuardrailIssue("G8", f"max_grid_kwh {g} is negative"))

    # G9: required quantity present
    if dt in ("solar_reduction", "minimum_battery_reserve", "max_grid_window") and llm_out.quantity is None:
        issues.append(GuardrailIssue("G9", f"{dt} requires a quantity"))

    # G10: dual-channel agreement (channel A normalized vs channel B llm_out.hours/values)
    if dt != "no_op" and not issues:
        if set(normalized.hours) != set(llm_out.hours):
            issues.append(GuardrailIssue("G10", "channel A/B hours disagree"))
        if dt == "solar_reduction" and normalized.factor is not None and llm_out.factor is not None:
            if abs(normalized.factor - llm_out.factor) > 1e-3:
                issues.append(GuardrailIssue("G10", "channel A/B factor disagree"))
        if dt == "minimum_battery_reserve" and normalized.minimum_energy_kwh is not None and llm_out.minimum_energy_kwh is not None:
            if abs(normalized.minimum_energy_kwh - llm_out.minimum_energy_kwh) > 1e-3:
                issues.append(GuardrailIssue("G10", "channel A/B minimum_energy_kwh disagree"))
        if dt == "max_grid_window" and normalized.max_grid_kwh is not None and llm_out.max_grid_kwh is not None:
            if abs(normalized.max_grid_kwh - llm_out.max_grid_kwh) > 1e-3:
                issues.append(GuardrailIssue("G10", "channel A/B max_grid_kwh disagree"))

    # G11: grounding (soft — logged rather than blocking after the first repair attempt)
    if dt != "no_op" and llm_out.quantity is not None and not is_grounded(llm_out.quantity, note_text):
        issues.append(GuardrailIssue("G11", "quantity value not found in note text", repairable=True))

    # G12: relevance consistency
    if llm_out.affects_schedule == (dt == "no_op"):
        issues.append(GuardrailIssue("G12", "affects_schedule disagrees with directive_type"))

    return issues
