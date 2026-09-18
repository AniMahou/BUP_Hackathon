from app.optimizer.constraints import build_bounds
from app.schemas.directives import AppliedDirective


def test_solar_factor_product_across_different_notes():
    applied = [
        AppliedDirective(note_index=0, type="solar_reduction", hours=[13, 14], factor=0.5),
        AppliedDirective(note_index=1, type="solar_reduction", hours=[13, 14], factor=0.5),
    ]
    bounds = build_bounds(applied, base_min_kwh=40)
    assert bounds.factor[13] == 0.25
    assert bounds.factor[12] == 1.0


def test_solar_factor_min_within_same_note_hedge():
    applied = [
        AppliedDirective(note_index=0, type="solar_reduction", hours=[13, 14], factor=0.5, is_hedge=False),
        AppliedDirective(note_index=0, type="solar_reduction", hours=[13, 14, 15], factor=0.5, is_hedge=True),
    ]
    bounds = build_bounds(applied, base_min_kwh=40)
    # same note's own candidates combine by min, not product -> stays 0.5, and hour 15 picks it up too
    assert bounds.factor[13] == 0.5
    assert bounds.factor[15] == 0.5


def test_reserve_takes_max_across_notes():
    applied = [
        AppliedDirective(note_index=0, type="minimum_battery_reserve", hours=[18, 19], minimum_energy_kwh=80),
        AppliedDirective(note_index=1, type="minimum_battery_reserve", hours=[18, 19], minimum_energy_kwh=120),
    ]
    bounds = build_bounds(applied, base_min_kwh=40)
    assert bounds.reserve[18] == 120
    assert bounds.reserve[0] == 40  # untouched hours keep base_min


def test_no_charge_and_no_discharge_are_union():
    applied = [
        AppliedDirective(note_index=0, type="no_charge_window", hours=[2, 3]),
        AppliedDirective(note_index=1, type="no_discharge_window", hours=[18]),
    ]
    bounds = build_bounds(applied, base_min_kwh=40)
    assert bounds.charge_ok[2] is False and bounds.charge_ok[3] is False
    assert bounds.charge_ok[4] is True
    assert bounds.discharge_ok[18] is False
    assert bounds.discharge_ok[17] is True


def test_grid_cap_takes_min_across_notes():
    applied = [
        AppliedDirective(note_index=0, type="max_grid_window", hours=[18, 19], max_grid_kwh=150),
        AppliedDirective(note_index=1, type="max_grid_window", hours=[18], max_grid_kwh=100),
    ]
    bounds = build_bounds(applied, base_min_kwh=40)
    assert bounds.grid_cap[18] == 100
    assert bounds.grid_cap[19] == 150
    assert bounds.grid_cap[0] == float("inf")


def test_hedges_excluded_when_include_hedges_false():
    applied = [
        AppliedDirective(note_index=0, type="no_charge_window", hours=[2], is_hedge=False),
        AppliedDirective(note_index=0, type="no_charge_window", hours=[2, 3], is_hedge=True),
    ]
    bounds = build_bounds(applied, base_min_kwh=40, include_hedges=False)
    assert bounds.charge_ok[2] is False
    assert bounds.charge_ok[3] is True
