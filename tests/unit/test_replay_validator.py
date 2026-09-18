import copy

from app.optimizer.constraints import HourlyBounds
from app.optimizer.postprocess import Totals
from app.schemas.request import BatteryInput, HourInput, OptimizeRequest
from app.schemas.response import HourlyPlanEntry
from app.verification.replay import replay


def _req() -> OptimizeRequest:
    hours = [HourInput(hour=h, demand_kwh=100.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0) for h in range(24)]
    battery = BatteryInput(capacity_kwh=200.0, initial_energy_kwh=50.0, minimum_energy_kwh=0.0, max_charge_kwh_per_hour=50.0, max_discharge_kwh_per_hour=50.0)
    return OptimizeRequest(scenario_id="t", operator_notes=["irrelevant note"], hours=hours, battery=battery)


def _valid_plan_and_totals():
    plan = [
        HourlyPlanEntry(hour=h, grid_kwh=100.0, solar_used_kwh=0.0, battery_action="idle", battery_kwh=0.0, battery_energy_after_kwh=50.0)
        for h in range(24)
    ]
    totals = Totals(total_grid_kwh=2400.0, total_cost_bdt=24000.0, peak_grid_kwh=100.0)
    return plan, totals


def test_valid_plan_has_no_violations():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    assert replay(req, bounds, plan, totals) == []


def test_negative_grid_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    plan[3] = plan[3].model_copy(update={"grid_kwh": -5.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "negative" for v in violations)


def test_wrong_transition_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    plan[5] = plan[5].model_copy(update={"battery_energy_after_kwh": 55.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "transition" for v in violations)


def test_idle_with_nonzero_battery_kwh_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    plan[7] = plan[7].model_copy(update={"battery_kwh": 5.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "idle_nonzero" for v in violations)


def test_charge_over_max_rate_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    plan[8] = plan[8].model_copy(update={"battery_action": "charge", "battery_kwh": 999.0, "grid_kwh": 199.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "charge_rate" for v in violations)


def test_charge_in_no_charge_hour_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    bounds.charge_ok[8] = False
    plan, totals = _valid_plan_and_totals()
    plan[8] = plan[8].model_copy(update={"battery_action": "charge", "battery_kwh": 10.0, "grid_kwh": 110.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "charge_forbidden" for v in violations)


def test_end_of_day_neutrality_violation_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    plan[23] = plan[23].model_copy(update={"battery_energy_after_kwh": 60.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code in ("transition", "eod_neutrality") for v in violations)


def test_wrong_totals_are_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    plan, totals = _valid_plan_and_totals()
    totals = copy.deepcopy(totals)
    totals.total_cost_bdt = 99999.0
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "totals_cost" for v in violations)


def test_solar_use_beyond_effective_solar_is_caught():
    req = _req()
    bounds = HourlyBounds(reserve=[0.0] * 24)
    bounds.factor[0] = 0.0  # no solar allowed this hour
    plan, totals = _valid_plan_and_totals()
    plan[0] = plan[0].model_copy(update={"solar_used_kwh": 10.0, "grid_kwh": 90.0})
    violations = replay(req, bounds, plan, totals)
    assert any(v.code == "solar_use" for v in violations)
