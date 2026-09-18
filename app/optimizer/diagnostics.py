"""Human-readable infeasibility diagnostics (§5.3 step 2), used both in the constraint-correction
LLM prompt and in logs. Best-effort: catches the common single-cause cases explicitly, and falls
back to a generic message otherwise (the elastic solve is what actually recovers in that case).
"""

from dataclasses import dataclass

from app.optimizer.constraints import HourlyBounds
from app.schemas.directives import AppliedDirective
from app.schemas.request import BatteryInput


@dataclass
class Conflict:
    description: str
    note_indices: list[int]


def _notes_touching(applied: list[AppliedDirective], directive_type: str, hour: int) -> list[int]:
    return sorted({d.note_index for d in applied if d.type == directive_type and hour in d.hours})


def diagnose(
    demand: list[float],
    solar: list[float],
    hourly_bounds: HourlyBounds,
    battery: BatteryInput,
    applied: list[AppliedDirective],
) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for h in range(24):
        effective_solar = solar[h] * hourly_bounds.factor[h]
        max_discharge_h = battery.max_discharge_kwh_per_hour if hourly_bounds.discharge_ok[h] else 0.0
        min_grid_needed = demand[h] - effective_solar - max_discharge_h
        grid_cap = hourly_bounds.grid_cap[h]
        if grid_cap != float("inf") and min_grid_needed > grid_cap + 1e-6:
            notes = (
                _notes_touching(applied, "max_grid_window", h)
                + _notes_touching(applied, "solar_reduction", h)
                + _notes_touching(applied, "no_discharge_window", h)
            )
            conflicts.append(
                Conflict(
                    description=(
                        f"hour {h}: demand {demand[h]:.1f}, effective solar {effective_solar:.1f}, "
                        f"max discharge {max_discharge_h:.1f} => grid needed >= {min_grid_needed:.1f} "
                        f"but the cap is {grid_cap:.1f}"
                    ),
                    note_indices=sorted(set(notes)),
                )
            )

    e_max = battery.initial_energy_kwh
    for h in range(24):
        charge_cap = battery.max_charge_kwh_per_hour if hourly_bounds.charge_ok[h] else 0.0
        e_max = min(battery.capacity_kwh, e_max + charge_cap)
        if hourly_bounds.reserve[h] > e_max + 1e-6:
            notes = _notes_touching(applied, "minimum_battery_reserve", h) + [
                d.note_index for d in applied if d.type == "no_charge_window" and any(hh <= h for hh in d.hours)
            ]
            conflicts.append(
                Conflict(
                    description=(
                        f"hour {h}: reserve requires >= {hourly_bounds.reserve[h]:.1f} kWh but the battery can "
                        f"reach at most {e_max:.1f} kWh by then given the charge limits so far"
                    ),
                    note_indices=sorted(set(notes)),
                )
            )
            break  # downstream hours will repeat the same root cause

    if hourly_bounds.reserve[23] > battery.initial_energy_kwh + 1e-6:
        conflicts.append(
            Conflict(
                description=(
                    f"hour 23 reserve requires >= {hourly_bounds.reserve[23]:.1f} kWh but the plan must end at "
                    f"the initial energy {battery.initial_energy_kwh:.1f} kWh (end-of-day neutrality)"
                ),
                note_indices=_notes_touching(applied, "minimum_battery_reserve", 23),
            )
        )

    if not conflicts:
        conflicts.append(
            Conflict(
                description="The combination of directives is infeasible; no single-hour root cause was found "
                "(likely several directives interacting across hours).",
                note_indices=sorted({d.note_index for d in applied}),
            )
        )

    return conflicts
