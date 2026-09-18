"""Runs the LP (§5.2 stage 1 + optional stage 2 tie-break) and the elastic fallback (§5.3 step 4)."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from app.optimizer.constraints import HourlyBounds
from app.optimizer.lp_model import (
    SLACK_GROUPS,
    base_bounds,
    build_balance_dynamics,
    build_elastic_model,
    cost_objective,
    cycling_objective,
    elastic_cost_objective,
    elastic_slack_objective,
    idx,
    slack_idx,
)
from app.schemas.request import BatteryInput


@dataclass
class Solution:
    grid: list[float]
    solar_used: list[float]
    charge: list[float]
    discharge: list[float]
    energy_after: list[float]
    stage1_cost: float
    stage2_cost: float | None = None
    elastic: bool = False
    slack_total: float = 0.0
    slack_by_hour: dict[str, list[float]] | None = None


@dataclass
class Infeasible:
    message: str


def _extract(x: np.ndarray) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    g = [float(x[idx(h, 0)]) for h in range(24)]
    s = [float(x[idx(h, 1)]) for h in range(24)]
    c = [float(x[idx(h, 2)]) for h in range(24)]
    d = [float(x[idx(h, 3)]) for h in range(24)]
    e = [float(x[idx(h, 4)]) for h in range(24)]
    return g, s, c, d, e


def solve(
    demand: list[float],
    solar: list[float],
    tariff: list[float],
    hourly_bounds: HourlyBounds,
    battery: BatteryInput,
    enable_secondary_objective: bool = True,
) -> Solution | Infeasible:
    A_eq, b_eq = build_balance_dynamics(demand, battery.initial_energy_kwh)
    bounds = base_bounds(solar, hourly_bounds, battery)
    c1 = cost_objective(tariff)

    res1 = linprog(c1, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res1.success:
        return Infeasible(message=res1.message)

    stage1_cost = float(res1.fun)
    x = res1.x
    stage2_cost = None

    if enable_secondary_objective:
        c2 = cycling_objective()
        A_ub = np.array([c1])
        b_ub = np.array([stage1_cost + 1e-6])
        res2 = linprog(c2, A_eq=A_eq, b_eq=b_eq, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if res2.success and float(c1 @ res2.x) <= stage1_cost + 1e-4:
            x = res2.x
            stage2_cost = float(res2.fun)

    g, s, c, d, e = _extract(x)
    return Solution(grid=g, solar_used=s, charge=c, discharge=d, energy_after=e, stage1_cost=stage1_cost, stage2_cost=stage2_cost)


def solve_elastic(
    demand: list[float],
    solar: list[float],
    tariff: list[float],
    hourly_bounds: HourlyBounds,
    battery: BatteryInput,
) -> Solution:
    """Physics stays hard; directive-added restrictions get slack. Lexicographic: minimize total
    slack first, then cost, subject to that minimal slack (§5.3 step 4)."""
    model = build_elastic_model(demand, solar, hourly_bounds, battery)

    c_slack = elastic_slack_objective(model.n_vars)
    res_slack = linprog(
        c_slack, A_eq=model.A_eq, b_eq=model.b_eq, A_ub=model.A_ub, b_ub=model.b_ub, bounds=model.bounds, method="highs"
    )
    if not res_slack.success:
        # Physics-only should always be feasible (§5.2); this should not happen.
        raise RuntimeError(f"elastic LP infeasible even with full slack: {res_slack.message}")

    min_slack = float(res_slack.fun)
    c_cost = elastic_cost_objective(tariff, model.n_vars)
    A_ub2 = np.vstack([model.A_ub, c_slack]) if model.A_ub.size else np.array([c_slack])
    b_ub2 = np.concatenate([model.b_ub, [min_slack + 1e-6]])

    res_cost = linprog(
        c_cost, A_eq=model.A_eq, b_eq=model.b_eq, A_ub=A_ub2, b_ub=b_ub2, bounds=model.bounds, method="highs"
    )
    x = res_cost.x if res_cost.success else res_slack.x

    g, s, c, d, e = _extract(x)
    slack_by_hour = {
        group: [float(x[slack_idx(group, h)]) for h in range(24)] for group in SLACK_GROUPS
    }
    stage1_cost = float(sum(t * gg for t, gg in zip(tariff, g, strict=True)))
    return Solution(
        grid=g,
        solar_used=s,
        charge=c,
        discharge=d,
        energy_after=e,
        stage1_cost=stage1_cost,
        elastic=True,
        slack_total=min_slack,
        slack_by_hour=slack_by_hour,
    )
