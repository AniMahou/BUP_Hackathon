"""Judge-mirror replay validator (§5.5). Pure function used three ways: at runtime after
postprocessing (tol 1e-6, self-check), in tests against fixture ground truth (tol 0.01, matching
the judge's own tolerance), and in scripts run against a live deployment.
"""

import math
from dataclasses import dataclass

from app.optimizer.constraints import HourlyBounds
from app.optimizer.postprocess import Totals
from app.schemas.request import OptimizeRequest
from app.schemas.response import DirectiveInterpretation, HourlyPlanEntry


@dataclass
class Violation:
    code: str
    message: str


def replay(
    req: OptimizeRequest,
    bounds: HourlyBounds,
    plan: list[HourlyPlanEntry],
    totals: Totals,
    tol: float = 1e-6,
    interpretation: list[DirectiveInterpretation] | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    battery = req.battery
    hours_by_h = {h.hour: h for h in req.hours}

    if len(plan) != 24:
        return [Violation("plan_length", f"expected 24 entries, got {len(plan)}")]
    seen_hours = [e.hour for e in plan]
    if seen_hours != sorted(seen_hours) or set(seen_hours) != set(range(24)):
        return [Violation("plan_hours", "hours must be unique, ascending, 0..23")]

    prev_e = battery.initial_energy_kwh
    for entry in plan:
        h = entry.hour
        req_h = hours_by_h[h]

        values = (entry.grid_kwh, entry.solar_used_kwh, entry.battery_kwh, entry.battery_energy_after_kwh)
        if not all(math.isfinite(v) for v in values):
            violations.append(Violation("non_finite", f"hour {h}: non-finite value"))
            prev_e = entry.battery_energy_after_kwh
            continue
        if entry.grid_kwh < -tol or entry.solar_used_kwh < -tol or entry.battery_kwh < -tol:
            violations.append(Violation("negative", f"hour {h}: negative value"))

        if entry.battery_action not in ("charge", "discharge", "idle"):
            violations.append(Violation("bad_action", f"hour {h}: invalid action {entry.battery_action}"))
            prev_e = entry.battery_energy_after_kwh
            continue

        if entry.battery_action == "idle":
            if abs(entry.battery_kwh) > tol:
                violations.append(Violation("idle_nonzero", f"hour {h}: idle but battery_kwh={entry.battery_kwh}"))
            expected_e = prev_e
        elif entry.battery_action == "charge":
            if entry.battery_kwh > battery.max_charge_kwh_per_hour + tol:
                violations.append(
                    Violation("charge_rate", f"hour {h}: charge {entry.battery_kwh} exceeds max {battery.max_charge_kwh_per_hour}")
                )
            if not bounds.charge_ok[h]:
                violations.append(Violation("charge_forbidden", f"hour {h}: charging not allowed here"))
            expected_e = prev_e + entry.battery_kwh
        else:  # discharge
            if entry.battery_kwh > battery.max_discharge_kwh_per_hour + tol:
                violations.append(
                    Violation("discharge_rate", f"hour {h}: discharge {entry.battery_kwh} exceeds max {battery.max_discharge_kwh_per_hour}")
                )
            if not bounds.discharge_ok[h]:
                violations.append(Violation("discharge_forbidden", f"hour {h}: discharging not allowed here"))
            expected_e = prev_e - entry.battery_kwh

        if abs(entry.battery_energy_after_kwh - expected_e) > tol:
            violations.append(
                Violation("transition", f"hour {h}: E_after {entry.battery_energy_after_kwh} != expected {expected_e}")
            )

        reserve_h = bounds.reserve[h]
        if entry.battery_energy_after_kwh < reserve_h - tol or entry.battery_energy_after_kwh > battery.capacity_kwh + tol:
            violations.append(
                Violation(
                    "reserve_capacity",
                    f"hour {h}: E_after {entry.battery_energy_after_kwh} outside [{reserve_h}, {battery.capacity_kwh}]",
                )
            )

        effective_solar_h = req_h.solar_kwh * bounds.factor[h]
        if entry.solar_used_kwh > effective_solar_h + tol:
            violations.append(
                Violation("solar_use", f"hour {h}: solar_used {entry.solar_used_kwh} exceeds effective solar {effective_solar_h}")
            )

        grid_cap_h = bounds.grid_cap[h]
        if grid_cap_h != float("inf") and entry.grid_kwh > grid_cap_h + tol:
            violations.append(Violation("grid_cap", f"hour {h}: grid {entry.grid_kwh} exceeds cap {grid_cap_h}"))

        discharge_term = entry.battery_kwh if entry.battery_action == "discharge" else 0.0
        charge_term = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        lhs = entry.grid_kwh + entry.solar_used_kwh + discharge_term
        rhs = req_h.demand_kwh + charge_term
        if abs(lhs - rhs) > tol:
            violations.append(Violation("balance", f"hour {h}: grid+solar+discharge={lhs} != demand+charge={rhs}"))

        prev_e = entry.battery_energy_after_kwh

    if abs(plan[-1].battery_energy_after_kwh - battery.initial_energy_kwh) > tol:
        violations.append(
            Violation(
                "eod_neutrality",
                f"E_after[23]={plan[-1].battery_energy_after_kwh} != initial {battery.initial_energy_kwh}",
            )
        )

    recomputed_grid = round(math.fsum(e.grid_kwh for e in plan), 6)
    recomputed_cost = round(math.fsum(e.grid_kwh * hours_by_h[e.hour].tariff_bdt_per_kwh for e in plan), 6)
    recomputed_peak = round(max(e.grid_kwh for e in plan), 6)
    if abs(recomputed_grid - totals.total_grid_kwh) > tol:
        violations.append(Violation("totals_grid", f"total_grid_kwh {totals.total_grid_kwh} != recomputed {recomputed_grid}"))
    if abs(recomputed_cost - totals.total_cost_bdt) > tol:
        violations.append(Violation("totals_cost", f"total_cost_bdt {totals.total_cost_bdt} != recomputed {recomputed_cost}"))
    if abs(recomputed_peak - totals.peak_grid_kwh) > tol:
        violations.append(Violation("totals_peak", f"peak_grid_kwh {totals.peak_grid_kwh} != recomputed {recomputed_peak}"))

    if interpretation is not None:
        expected_indices = list(range(len(req.operator_notes)))
        got_indices = [i.note_index for i in interpretation]
        if got_indices != expected_indices:
            violations.append(Violation("interpretation_order", f"note_index order {got_indices} != {expected_indices}"))
        for entry in interpretation:
            if entry.directive_type == "no_op":
                if entry.applies is not False or entry.structured_adjustment is not None:
                    violations.append(
                        Violation("no_op_shape", f"note {entry.note_index}: no_op must have applies=false and null adjustment")
                    )
            else:
                if entry.applies is not True or entry.structured_adjustment is None:
                    violations.append(
                        Violation("applies_shape", f"note {entry.note_index}: non-no_op must have applies=true and a structured_adjustment")
                    )

    return violations
