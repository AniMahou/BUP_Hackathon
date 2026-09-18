"""LP matrix construction for HiGHS (§5.2). Variables per hour: g (grid), s (solar used),
c (charge), d (discharge), E (energy after) -> 120 base variables, 49 equalities.

Elastic mode (§5.3 step 4) adds 5 slack arrays (one per directive-restricted quantity) on top of
the base 120 variables. Physics bounds (battery capacity/rates, full solar, unlimited grid) are
never relaxed — only the extra restriction a directive adds gets slack, bounded so it can never
relax past the physical limit.
"""

from dataclasses import dataclass

import numpy as np

from app.optimizer.constraints import HourlyBounds
from app.schemas.request import BatteryInput

VARS_PER_HOUR = 5
G, S, C, D, E = 0, 1, 2, 3, 4


def idx(h: int, var: int) -> int:
    return h * VARS_PER_HOUR + var


def build_balance_dynamics(demand: list[float], initial_kwh: float) -> tuple[np.ndarray, np.ndarray]:
    n = 24 * VARS_PER_HOUR
    rows: list[np.ndarray] = []
    b: list[float] = []

    for h in range(24):
        row = np.zeros(n)
        row[idx(h, G)] = 1.0
        row[idx(h, S)] = 1.0
        row[idx(h, D)] = 1.0
        row[idx(h, C)] = -1.0
        rows.append(row)
        b.append(demand[h])

    for h in range(24):
        row = np.zeros(n)
        row[idx(h, E)] = 1.0
        row[idx(h, C)] = -1.0
        row[idx(h, D)] = 1.0
        if h == 0:
            b.append(initial_kwh)
        else:
            row[idx(h - 1, E)] = -1.0
            b.append(0.0)
        rows.append(row)

    row = np.zeros(n)
    row[idx(23, E)] = 1.0
    rows.append(row)
    b.append(initial_kwh)

    return np.array(rows), np.array(b)


def base_bounds(
    solar: list[float], hourly_bounds: HourlyBounds, battery: BatteryInput
) -> list[tuple[float, float | None]]:
    bounds: list[tuple[float, float | None]] = [(0.0, None)] * (24 * VARS_PER_HOUR)
    for h in range(24):
        effective_solar = solar[h] * hourly_bounds.factor[h]
        grid_cap = hourly_bounds.grid_cap[h]
        bounds[idx(h, G)] = (0.0, None if grid_cap == float("inf") else grid_cap)
        bounds[idx(h, S)] = (0.0, effective_solar)
        bounds[idx(h, C)] = (0.0, battery.max_charge_kwh_per_hour if hourly_bounds.charge_ok[h] else 0.0)
        bounds[idx(h, D)] = (0.0, battery.max_discharge_kwh_per_hour if hourly_bounds.discharge_ok[h] else 0.0)
        bounds[idx(h, E)] = (hourly_bounds.reserve[h], battery.capacity_kwh)
    return bounds


def cost_objective(tariff: list[float]) -> np.ndarray:
    c = np.zeros(24 * VARS_PER_HOUR)
    for h in range(24):
        c[idx(h, G)] = tariff[h]
    return c


def cycling_objective() -> np.ndarray:
    c = np.zeros(24 * VARS_PER_HOUR)
    for h in range(24):
        c[idx(h, C)] = 1.0
        c[idx(h, D)] = 1.0
    return c


# --- Elastic model ------------------------------------------------------------------------------

SLACK_GROUPS = ("grid", "solar", "charge", "discharge", "reserve")
N_BASE = 24 * VARS_PER_HOUR


def slack_idx(group: str, h: int) -> int:
    return N_BASE + SLACK_GROUPS.index(group) * 24 + h


@dataclass
class ElasticModel:
    A_eq: np.ndarray
    b_eq: np.ndarray
    A_ub: np.ndarray
    b_ub: np.ndarray
    bounds: list[tuple[float, float | None]]
    n_vars: int


def build_elastic_model(
    demand: list[float],
    solar: list[float],
    hourly_bounds: HourlyBounds,
    battery: BatteryInput,
) -> ElasticModel:
    n = N_BASE + len(SLACK_GROUPS) * 24
    A_eq_base, b_eq = build_balance_dynamics(demand, battery.initial_energy_kwh)
    A_eq = np.zeros((A_eq_base.shape[0], n))
    A_eq[:, :N_BASE] = A_eq_base

    bounds: list[tuple[float, float | None]] = [(0.0, None)] * n
    for h in range(24):
        bounds[idx(h, G)] = (0.0, None)
        bounds[idx(h, S)] = (0.0, solar[h])
        bounds[idx(h, C)] = (0.0, battery.max_charge_kwh_per_hour)
        bounds[idx(h, D)] = (0.0, battery.max_discharge_kwh_per_hour)
        bounds[idx(h, E)] = (battery.minimum_energy_kwh, battery.capacity_kwh)

        grid_cap = hourly_bounds.grid_cap[h]
        bounds[slack_idx("grid", h)] = (0.0, None) if grid_cap != float("inf") else (0.0, 0.0)

        effective_solar = solar[h] * hourly_bounds.factor[h]
        bounds[slack_idx("solar", h)] = (0.0, max(0.0, solar[h] - effective_solar))

        charge_cap = battery.max_charge_kwh_per_hour if hourly_bounds.charge_ok[h] else 0.0
        bounds[slack_idx("charge", h)] = (0.0, max(0.0, battery.max_charge_kwh_per_hour - charge_cap))

        discharge_cap = battery.max_discharge_kwh_per_hour if hourly_bounds.discharge_ok[h] else 0.0
        bounds[slack_idx("discharge", h)] = (0.0, max(0.0, battery.max_discharge_kwh_per_hour - discharge_cap))

        bounds[slack_idx("reserve", h)] = (0.0, max(0.0, hourly_bounds.reserve[h] - battery.minimum_energy_kwh))

    ub_rows: list[np.ndarray] = []
    ub_rhs: list[float] = []
    for h in range(24):
        grid_cap = hourly_bounds.grid_cap[h]
        if grid_cap != float("inf"):
            row = np.zeros(n)
            row[idx(h, G)] = 1.0
            row[slack_idx("grid", h)] = -1.0
            ub_rows.append(row)
            ub_rhs.append(grid_cap)

        effective_solar = solar[h] * hourly_bounds.factor[h]
        row = np.zeros(n)
        row[idx(h, S)] = 1.0
        row[slack_idx("solar", h)] = -1.0
        ub_rows.append(row)
        ub_rhs.append(effective_solar)

        if not hourly_bounds.charge_ok[h]:
            row = np.zeros(n)
            row[idx(h, C)] = 1.0
            row[slack_idx("charge", h)] = -1.0
            ub_rows.append(row)
            ub_rhs.append(0.0)

        if not hourly_bounds.discharge_ok[h]:
            row = np.zeros(n)
            row[idx(h, D)] = 1.0
            row[slack_idx("discharge", h)] = -1.0
            ub_rows.append(row)
            ub_rhs.append(0.0)

        reserve_h = hourly_bounds.reserve[h]
        if reserve_h > battery.minimum_energy_kwh:
            row = np.zeros(n)
            row[idx(h, E)] = -1.0
            row[slack_idx("reserve", h)] = -1.0
            ub_rows.append(row)
            ub_rhs.append(-reserve_h)

    A_ub = np.array(ub_rows) if ub_rows else np.zeros((0, n))
    b_ub = np.array(ub_rhs) if ub_rhs else np.zeros(0)

    return ElasticModel(A_eq=A_eq, b_eq=b_eq, A_ub=A_ub, b_ub=b_ub, bounds=bounds, n_vars=n)


def elastic_slack_objective(n_vars: int) -> np.ndarray:
    c = np.zeros(n_vars)
    for group in SLACK_GROUPS:
        for h in range(24):
            c[slack_idx(group, h)] = 1.0
    return c


def elastic_cost_objective(tariff: list[float], n_vars: int) -> np.ndarray:
    c = np.zeros(n_vars)
    for h in range(24):
        c[idx(h, G)] = tariff[h]
    return c
