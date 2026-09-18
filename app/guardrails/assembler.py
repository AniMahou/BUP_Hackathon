"""Builds, for one note: the reported DirectiveInterpretation entry (scored by Category 1) and the
candidate AppliedDirective list (hard constraints fed to the optimizer, §5). Values come from
channel A (normalizer output) — the authoritative, arithmetic-checked reading — never raw LLM
channel-B numbers, per G10's "if it persists, use channel A" rule.
"""

from dataclasses import dataclass

from app.guardrails.normalizer import NormalizedNote
from app.schemas.directives import AppliedDirective, DirectiveType, structured_adjustment_for
from app.schemas.llm_output import NoteInterpretationLLM
from app.schemas.response import DirectiveInterpretation


def _fmt_hour_range(hours: list[int]) -> str:
    if not hours:
        return "no hours"
    if hours == list(range(24)):
        return "all day"
    return ", ".join(f"{h:02d}:00" for h in hours[:4]) + (" ..." if len(hours) > 4 else "")


def _deterministic_explanation(dt: DirectiveType, hours: list[int], factor, min_kwh, max_grid) -> str:
    hr = _fmt_hour_range(hours)
    if dt == "solar_reduction":
        return f"Usable solar limited to {round((factor or 0) * 100)}% of forecast for {hr}."
    if dt == "minimum_battery_reserve":
        return f"Battery must keep at least {min_kwh:.1f} kWh stored during {hr}."
    if dt == "no_charge_window":
        return f"Charging disabled during {hr}."
    if dt == "no_discharge_window":
        return f"Discharging disabled during {hr}."
    if dt == "max_grid_window":
        return f"Grid import capped at {max_grid:.1f} kWh per hour during {hr}."
    return "No schedule adjustment applied."


@dataclass
class NoteAssembly:
    entry: DirectiveInterpretation
    applied: list[AppliedDirective]
    degraded: bool = False


def assemble_note(
    note_index: int,
    llm_out: NoteInterpretationLLM,
    normalized: NormalizedNote,
    regex_alt_hours: list[int] | None,
    enable_hedging: bool,
    capacity_kwh: float,
    base_min_kwh: float,
) -> NoteAssembly:
    dt = llm_out.directive_type

    if dt == "no_op":
        entry = DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=llm_out.explanation or "Note does not affect today's schedule.",
        )
        return NoteAssembly(entry=entry, applied=[])

    values_changed = set(normalized.hours) != set(llm_out.hours)
    if dt == "solar_reduction" and llm_out.factor is not None:
        values_changed = values_changed or abs((normalized.factor or 0) - llm_out.factor) > 1e-3
    if dt == "minimum_battery_reserve" and llm_out.minimum_energy_kwh is not None:
        values_changed = values_changed or abs((normalized.minimum_energy_kwh or 0) - llm_out.minimum_energy_kwh) > 1e-3
    if dt == "max_grid_window" and llm_out.max_grid_kwh is not None:
        values_changed = values_changed or abs((normalized.max_grid_kwh or 0) - llm_out.max_grid_kwh) > 1e-3

    explanation = llm_out.explanation
    if values_changed or not explanation:
        explanation = _deterministic_explanation(
            dt, normalized.hours, normalized.factor, normalized.minimum_energy_kwh, normalized.max_grid_kwh
        )

    structured = structured_adjustment_for(
        dt, normalized.hours, normalized.factor, normalized.minimum_energy_kwh, normalized.max_grid_kwh
    )
    entry = DirectiveInterpretation(
        note_index=note_index,
        applies=True,
        directive_type=dt,
        structured_adjustment=structured,
        explanation=explanation[:240],
    )

    primary = AppliedDirective(
        note_index=note_index,
        type=dt,
        hours=normalized.hours,
        factor=normalized.factor,
        minimum_energy_kwh=normalized.minimum_energy_kwh,
        max_grid_kwh=normalized.max_grid_kwh,
        is_hedge=False,
    )
    applied = [primary]

    if enable_hedging:
        alt = llm_out.alternative
        if alt is not None and alt.directive_type == dt:
            from app.guardrails.normalizer import quantity_to_value, windows_to_hours

            try:
                alt_hours = windows_to_hours(alt.time_windows, dt)
                alt_factor, alt_min_kwh, alt_max_grid = quantity_to_value(
                    dt, alt.quantity, capacity_kwh=capacity_kwh, base_min_kwh=base_min_kwh
                ) if alt.quantity else (normalized.factor, normalized.minimum_energy_kwh, normalized.max_grid_kwh)
                applied.append(
                    AppliedDirective(
                        note_index=note_index,
                        type=dt,
                        hours=alt_hours,
                        factor=alt_factor if alt_factor is not None else normalized.factor,
                        minimum_energy_kwh=alt_min_kwh if alt_min_kwh is not None else normalized.minimum_energy_kwh,
                        max_grid_kwh=alt_max_grid if alt_max_grid is not None else normalized.max_grid_kwh,
                        is_hedge=True,
                    )
                )
            except Exception:
                pass

        if regex_alt_hours and set(regex_alt_hours) != set(normalized.hours):
            union_hours = sorted(set(normalized.hours) | set(regex_alt_hours))
            applied.append(
                AppliedDirective(
                    note_index=note_index,
                    type=dt,
                    hours=union_hours,
                    factor=normalized.factor,
                    minimum_energy_kwh=normalized.minimum_energy_kwh,
                    max_grid_kwh=normalized.max_grid_kwh,
                    is_hedge=True,
                )
            )

    return NoteAssembly(entry=entry, applied=applied)
