import math

from app.optimizer.constraints import HourlyBounds
from app.optimizer.solver import Infeasible, solve, solve_elastic
from app.schemas.request import BatteryInput


def _battery(**overrides) -> BatteryInput:
    base = dict(capacity_kwh=200.0, initial_energy_kwh=50.0, minimum_energy_kwh=0.0, max_charge_kwh_per_hour=50.0, max_discharge_kwh_per_hour=50.0)
    base.update(overrides)
    return BatteryInput(**base)


def test_uses_free_solar_before_grid():
    demand = [100.0] * 24
    solar = [0.0] * 24
    solar[10] = 50.0
    tariff = [10.0] * 24
    bounds = HourlyBounds(reserve=[0.0] * 24)

    sol = solve(demand, solar, tariff, bounds, _battery())
    assert not isinstance(sol, Infeasible)
    assert sol.grid[10] == 50.0
    assert sol.solar_used[10] == 50.0


def test_end_of_day_neutrality():
    demand = [100.0] * 24
    solar = [20.0] * 24
    tariff = [10.0] * 24
    bounds = HourlyBounds(reserve=[0.0] * 24)

    sol = solve(demand, solar, tariff, bounds, _battery())
    assert not isinstance(sol, Infeasible)
    assert math.isclose(sol.energy_after[23], 50.0, abs_tol=1e-6)


def test_no_charge_window_forces_zero_charge_that_hour():
    demand = [100.0] * 24
    solar = [200.0] * 24  # plenty of "free" solar to tempt charging
    tariff = [10.0] * 24
    bounds = HourlyBounds(reserve=[0.0] * 24)
    bounds.charge_ok[5] = False

    sol = solve(demand, solar, tariff, bounds, _battery())
    assert not isinstance(sol, Infeasible)
    assert sol.charge[5] == 0.0


def test_grid_cap_can_make_it_infeasible():
    demand = [500.0] * 24  # far above solar + max discharge
    solar = [0.0] * 24
    tariff = [10.0] * 24
    bounds = HourlyBounds(reserve=[0.0] * 24)
    for h in range(24):
        bounds.grid_cap[h] = 10.0  # way too low

    sol = solve(demand, solar, tariff, bounds, _battery())
    assert isinstance(sol, Infeasible)


def test_elastic_solve_always_succeeds_and_reports_slack():
    demand = [500.0] * 24
    solar = [0.0] * 24
    tariff = [10.0] * 24
    battery = _battery()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    for h in range(24):
        bounds.grid_cap[h] = 10.0

    sol = solve_elastic(demand, solar, tariff, bounds, battery)
    assert sol.elastic is True
    assert sol.slack_total > 0

    # physics must still hold exactly even though the directive cap was relaxed
    for h in range(24):
        net = sol.charge[h] - sol.discharge[h]
        assert math.isclose(sol.grid[h] + sol.solar_used[h] + sol.discharge[h] - sol.charge[h], demand[h], abs_tol=1e-6)
        assert sol.grid[h] >= -1e-6
        assert -battery.max_discharge_kwh_per_hour - 1e-6 <= net <= battery.max_charge_kwh_per_hour + 1e-6
    assert math.isclose(sol.energy_after[23], battery.initial_energy_kwh, abs_tol=1e-6)
