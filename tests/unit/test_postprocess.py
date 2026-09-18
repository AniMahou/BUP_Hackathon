import pytest

from app.optimizer.constraints import HourlyBounds
from app.optimizer.postprocess import postprocess
from app.optimizer.solver import Solution
from app.schemas.request import BatteryInput, HourInput, OptimizeRequest
from app.verification.replay import replay


def _req() -> OptimizeRequest:
    hours = [HourInput(hour=h, demand_kwh=100.0, solar_kwh=20.0, tariff_bdt_per_kwh=10.0) for h in range(24)]
    battery = BatteryInput(
        capacity_kwh=200.0, initial_energy_kwh=50.0, minimum_energy_kwh=0.0,
        max_charge_kwh_per_hour=50.0, max_discharge_kwh_per_hour=50.0,
    )
    return OptimizeRequest(scenario_id="t", operator_notes=["n"], hours=hours, battery=battery)


def test_postprocess_zeroes_tiny_noise_and_stays_valid():
    req = _req()
    charge = [0.0] * 24
    charge[2] = 1e-9  # numeric noise, should be zeroed
    discharge = [0.0] * 24
    sol = Solution(
        grid=[80.0] * 24, solar_used=[20.0] * 24, charge=charge, discharge=discharge,
        energy_after=[50.0] * 24, stage1_cost=80.0 * 10 * 24,
    )

    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = postprocess(req, sol, effective_solar=[20.0] * 24, grid_cap=bounds.grid_cap)
    assert plan[2].battery_action == "idle"
    assert replay(req, bounds, plan, totals) == []


def test_postprocess_corrects_eod_residual():
    req = _req()
    charge = [0.0] * 24
    charge[0] = 5.0  # battery ends the day 5 kWh above initial unless corrected
    discharge = [0.0] * 24
    energy_after = [55.0] * 24
    sol = Solution(
        grid=[85.0] + [80.0] * 23, solar_used=[20.0] * 24, charge=charge, discharge=discharge,
        energy_after=energy_after, stage1_cost=0.0,
    )

    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = postprocess(req, sol, effective_solar=[20.0] * 24, grid_cap=bounds.grid_cap)
    assert plan[-1].battery_energy_after_kwh == pytest.approx(req.battery.initial_energy_kwh, abs=1e-6)
    assert replay(req, bounds, plan, totals) == []
