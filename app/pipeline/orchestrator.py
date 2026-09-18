"""End-to-end pipeline (§3.1): interpret -> build bounds -> solve (with hedges, then without) ->
diagnose + one correction round -> elastic fallback -> post-process -> self-replay -> summary ->
response. Also produces a `trace` dict for the UI's reasoning view; the official
POST /optimize-energy route only serializes `response`.
"""

from dataclasses import dataclass

from app.config import Settings
from app.guardrails.assembler import NoteAssembly
from app.llm.interpreter import NoteInterpreter
from app.optimizer.constraints import HourlyBounds, build_bounds
from app.optimizer.diagnostics import diagnose
from app.optimizer.postprocess import PostprocessError, Totals, postprocess
from app.optimizer.solver import Infeasible, Solution, solve, solve_elastic
from app.pipeline.deadline import Deadline
from app.schemas.request import OptimizeRequest
from app.schemas.response import HourlyPlanEntry, OptimizeResponse
from app.summary.plan_summary import plan_summary
from app.verification.replay import replay


class PipelineInfeasibleError(Exception):
    pass


@dataclass
class PipelineResult:
    response: OptimizeResponse
    trace: dict


def _series(req: OptimizeRequest) -> tuple[list[float], list[float], list[float]]:
    hours_sorted = sorted(req.hours, key=lambda h: h.hour)
    return (
        [h.demand_kwh for h in hours_sorted],
        [h.solar_kwh for h in hours_sorted],
        [h.tariff_bdt_per_kwh for h in hours_sorted],
    )


def _idle_fallback(req: OptimizeRequest) -> tuple[list[HourlyPlanEntry], Totals, HourlyBounds]:
    """Absolute last resort: physics-only (no directives), idle all day, grid covers all demand
    with whatever solar is naturally available. Always numerically valid because request
    validation already guarantees minimum_energy_kwh <= initial_energy_kwh <= capacity_kwh."""
    demand, solar, tariff = _series(req)
    bounds = build_bounds([], req.battery.minimum_energy_kwh, include_hedges=False)
    solar_used = [min(s, d) for s, d in zip(solar, demand, strict=True)]
    grid = [d - s for d, s in zip(demand, solar_used, strict=True)]
    e = req.battery.initial_energy_kwh
    entries = []
    for h in range(24):
        entries.append(
            HourlyPlanEntry(
                hour=h, grid_kwh=round(grid[h], 6), solar_used_kwh=round(solar_used[h], 6),
                battery_action="idle", battery_kwh=0.0, battery_energy_after_kwh=round(e, 6),
            )
        )
    total_grid = round(sum(grid), 6)
    total_cost = round(sum(g * t for g, t in zip(grid, tariff, strict=True)), 6)
    totals = Totals(total_grid_kwh=total_grid, total_cost_bdt=total_cost, peak_grid_kwh=round(max(grid), 6))
    return entries, totals, bounds


async def run_pipeline(req: OptimizeRequest, interpreter: NoteInterpreter, settings: Settings) -> PipelineResult:
    deadline = Deadline(settings.request_deadline_s)
    trace: dict = {"stages": [], "note_traces": []}

    def log(stage: str, **detail):
        trace["stages"].append({"stage": stage, "t_s": round(deadline.elapsed(), 3), **detail})

    log("request_received", scenario_id=req.scenario_id, n_notes=len(req.operator_notes))

    interp = await interpreter.interpret_request(req, deadline)
    log("interpretation_done", degraded=interp.meta.get("degraded", False))
    trace["note_traces"] = [
        {"note_index": t.note_index, "note_text": t.note_text, "stages": t.stages} for t in interp.traces
    ]
    trace["directive_interpretation"] = [e.model_dump() for e in interp.entries]

    demand, solar, tariff = _series(req)

    def rebuild_and_solve(assemblies: list[NoteAssembly], include_hedges: bool) -> tuple[HourlyBounds, Solution | Infeasible]:
        applied = [d for a in assemblies for d in a.applied]
        bounds = build_bounds(applied, req.battery.minimum_energy_kwh, include_hedges=include_hedges)
        sol = solve(demand, solar, tariff, bounds, req.battery, settings.enable_secondary_objective)
        return bounds, sol

    assemblies = interp.assemblies
    bounds, solution = rebuild_and_solve(assemblies, include_hedges=True)
    hedges_dropped = False

    if isinstance(solution, Infeasible):
        log("infeasible_with_hedges", message=solution.message)
        bounds, solution = rebuild_and_solve(assemblies, include_hedges=False)
        hedges_dropped = True

    elastic = False
    if isinstance(solution, Infeasible):
        log("infeasible_without_hedges", message=solution.message)
        applied = [d for a in assemblies for d in a.applied]
        conflicts = diagnose(demand, solar, bounds, req.battery, applied)
        log("diagnosed", conflicts=[c.description for c in conflicts])

        implicated = sorted({i for c in conflicts for i in c.note_indices})
        if implicated and not deadline.expired():
            conflict_text = "\n".join(f"- {c.description}" for c in conflicts)
            for note_index in implicated:
                new_assembly, corr_trace = await interpreter.correct_note(
                    req, note_index, deadline, conflict_text, previous_assembly=assemblies[note_index]
                )
                assemblies[note_index] = new_assembly
                trace["note_traces"].append(
                    {"note_index": note_index, "note_text": corr_trace.note_text, "stages": corr_trace.stages, "phase": "correction"}
                )
            log("correction_applied", note_indices=implicated)

            bounds, solution = rebuild_and_solve(assemblies, include_hedges=not hedges_dropped)
            if isinstance(solution, Infeasible):
                bounds, solution = rebuild_and_solve(assemblies, include_hedges=False)
                hedges_dropped = True

        if isinstance(solution, Infeasible):
            if settings.infeasible_policy == "error":
                raise PipelineInfeasibleError(solution.message)
            log("falling_back_to_elastic")
            elastic = True
            solution = solve_elastic(demand, solar, tariff, bounds, req.battery)

    entries = [a.entry for a in assemblies]

    try:
        plan, totals = postprocess(req, solution, effective_solar=[solar[h] * bounds.factor[h] for h in range(24)], grid_cap=bounds.grid_cap)
    except PostprocessError as e:
        log("postprocess_error_retrying_elastic", error=str(e))
        elastic = True
        solution = solve_elastic(demand, solar, tariff, bounds, req.battery)
        plan, totals = postprocess(req, solution, effective_solar=[solar[h] * bounds.factor[h] for h in range(24)], grid_cap=bounds.grid_cap)

    violations = replay(req, bounds, plan, totals, tol=1e-6)
    if violations:
        log("self_check_failed_retrying", violations=[v.code for v in violations])
        solution2 = solve_elastic(demand, solar, tariff, bounds, req.battery)
        try:
            plan2, totals2 = postprocess(
                req, solution2, effective_solar=[solar[h] * bounds.factor[h] for h in range(24)], grid_cap=bounds.grid_cap
            )
            if not replay(req, bounds, plan2, totals2, tol=1e-6):
                plan, totals, elastic = plan2, totals2, True
            else:
                raise PostprocessError("elastic retry still fails self-check")
        except Exception as e:
            log("elastic_retry_failed_using_idle_fallback", error=str(e))
            plan, totals, bounds = _idle_fallback(req)
            elastic = True

    log("done")

    summary = plan_summary(req, entries, plan, totals, degraded=interp.meta.get("degraded", False), elastic=elastic)

    response = OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=entries,
        hourly_plan=plan,
        total_grid_kwh=totals.total_grid_kwh,
        total_cost_bdt=totals.total_cost_bdt,
        peak_grid_kwh=totals.peak_grid_kwh,
        plan_summary=summary,
    )
    trace["hedges_dropped"] = hedges_dropped
    trace["elastic"] = elastic
    return PipelineResult(response=response, trace=trace)
