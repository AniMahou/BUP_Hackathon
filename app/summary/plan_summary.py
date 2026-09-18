"""Deterministic plan_summary text (§6.4). Never depends on the LLM's own wording, so it stays
stable across repeats and safe fallbacks."""

from app.optimizer.postprocess import Totals
from app.schemas.request import OptimizeRequest
from app.schemas.response import DirectiveInterpretation, HourlyPlanEntry


def plan_summary(
    req: OptimizeRequest,
    entries: list[DirectiveInterpretation],
    plan: list[HourlyPlanEntry],
    totals: Totals,
    degraded: bool = False,
    elastic: bool = False,
) -> str:
    n_active = sum(1 for e in entries if e.applies)
    parts = [
        f"Optimized 24-hour schedule for scenario {req.scenario_id}: total grid draw "
        f"{totals.total_grid_kwh:.1f} kWh, cost {totals.total_cost_bdt:.1f} BDT, peak hourly grid "
        f"draw {totals.peak_grid_kwh:.1f} kWh."
    ]
    if n_active:
        parts.append(f"{n_active} of {len(entries)} operator note(s) produced an active schedule constraint.")
    else:
        parts.append("No operator notes required a schedule change.")
    if elastic:
        parts.append(
            "The reported directives could not all be satisfied together; this plan minimizes total "
            "directive violation first, then cost."
        )
    if degraded:
        parts.append("One or more notes were interpreted in degraded (non-LLM) mode because the model was unavailable.")
    return " ".join(parts)
