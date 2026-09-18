"""Numeric hygiene on the LP solution (§5.4): net battery action, exact-by-construction energy
transitions, end-of-day residual correction, solar/grid derivation, and totals. Serialization
(exact key order, no exclude_none) is handled by response.py's field order — this module just
returns plain HourlyPlanEntry / Totals objects in hour order.
"""

import math
from dataclasses import dataclass

from app.optimizer.solver import Solution
from app.schemas.request import OptimizeRequest
from app.schemas.response import HourlyPlanEntry


class PostprocessError(Exception):
    pass


@dataclass
class Totals:
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def _norm(x: float) -> float:
    x = round(float(x), 6)
    return 0.0 if x == 0.0 else x


def postprocess(
    req: OptimizeRequest,
    solution: Solution,
    effective_solar: list[float],
    grid_cap: list[float],
) -> tuple[list[HourlyPlanEntry], Totals]:
    battery = req.battery
    hours_sorted = sorted(req.hours, key=lambda h: h.hour)
    demand = [h.demand_kwh for h in hours_sorted]
    tariff = [h.tariff_bdt_per_kwh for h in hours_sorted]

    net = [round(solution.charge[h] - solution.discharge[h], 6) for h in range(24)]
    for h in range(24):
        if abs(net[h]) < 1e-7:
            net[h] = 0.0
        net[h] = max(-battery.max_discharge_kwh_per_hour, min(battery.max_charge_kwh_per_hour, net[h]))

    def recompute_energy(net_seq: list[float]) -> list[float]:
        e = [0.0] * 24
        prev = battery.initial_energy_kwh
        for h in range(24):
            prev = round(prev + net_seq[h], 6)
            e[h] = prev
        return e

    energy_after = recompute_energy(net)
    residual = round(energy_after[23] - battery.initial_energy_kwh, 6)
    if residual != 0.0:
        for h in sorted(range(24), key=lambda h: -abs(net[h])):
            adjusted = round(net[h] - residual, 6)
            if -battery.max_discharge_kwh_per_hour <= adjusted <= battery.max_charge_kwh_per_hour:
                net[h] = adjusted
                energy_after = recompute_energy(net)
                break

    grid = [0.0] * 24
    solar_used = [0.0] * 24
    for h in range(24):
        need = max(0.0, demand[h] + net[h])
        s_h = min(round(solution.solar_used[h], 6), effective_solar[h], need)
        s_h = max(0.0, s_h)
        g_h = need - s_h
        cap = grid_cap[h]
        if cap != float("inf") and g_h > cap + 1e-6:
            s_h = min(effective_solar[h], need)
            g_h = need - s_h
            if cap != float("inf") and g_h > cap + 1e-6:
                raise PostprocessError(
                    f"hour {h}: cannot satisfy grid cap {cap} even after maximizing solar use (need {need})"
                )
        grid[h] = _norm(g_h)
        solar_used[h] = _norm(s_h)

    entries: list[HourlyPlanEntry] = []
    for h in range(24):
        n = net[h]
        if n > 1e-9:
            action, k = "charge", n
        elif n < -1e-9:
            action, k = "discharge", -n
        else:
            action, k = "idle", 0.0
        entries.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=grid[h],
                solar_used_kwh=solar_used[h],
                battery_action=action,
                battery_kwh=_norm(k),
                battery_energy_after_kwh=_norm(energy_after[h]),
            )
        )

    total_grid = round(math.fsum(grid), 6)
    total_cost = round(math.fsum(g * t for g, t in zip(grid, tariff, strict=True)), 6)
    peak_grid = round(max(grid), 6)
    totals = Totals(total_grid_kwh=total_grid, total_cost_bdt=total_cost, peak_grid_kwh=peak_grid)
    return entries, totals
