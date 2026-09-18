import pytest

from app.guardrails.normalizer import (
    NormalizationError,
    quantity_to_value,
    window_to_hours,
    windows_to_hours,
)
from app.schemas.llm_output import Quantity, TimeWindow


def test_window_to_hours_simple():
    assert window_to_hours(13, 15) == [13, 14]


def test_window_to_hours_wrap_midnight():
    assert window_to_hours(22, 2) == [22, 23, 0, 1]


def test_window_to_hours_rejects_equal():
    with pytest.raises(NormalizationError):
        window_to_hours(5, 5)


def test_window_to_hours_rejects_out_of_range():
    with pytest.raises(NormalizationError):
        window_to_hours(-1, 5)
    with pytest.raises(NormalizationError):
        window_to_hours(5, 25)


def test_windows_to_hours_empty_non_no_op_means_whole_day():
    assert windows_to_hours([], "solar_reduction") == list(range(24))


def test_windows_to_hours_empty_no_op_means_no_hours():
    assert windows_to_hours([], "no_op") == []


def test_windows_to_hours_union_and_sort():
    windows = [TimeWindow(start_hour=20, end_hour=22, source_text=""), TimeWindow(start_hour=1, end_hour=3, source_text="")]
    assert windows_to_hours(windows, "no_charge_window") == [1, 2, 20, 21]


@pytest.mark.parametrize(
    "unit,value,expected_factor",
    [
        ("percent_remaining", 20, 0.2),
        ("percent_reduction", 80, 0.2),
        ("fraction_remaining", 0.2, 0.2),
        ("fraction_reduction", 0.25, 0.75),
    ],
)
def test_quantity_to_value_solar(unit, value, expected_factor):
    q = Quantity(value=value, unit=unit, source_text="x")
    factor, min_kwh, max_grid = quantity_to_value("solar_reduction", q, capacity_kwh=200, base_min_kwh=40)
    assert factor == pytest.approx(expected_factor)
    assert min_kwh is None and max_grid is None


@pytest.mark.parametrize(
    "unit,value,capacity,base_min,expected",
    [
        ("kwh", 120, 200, 40, 120),
        ("mwh", 0.12, 200, 40, 120),
        ("percent_of_capacity", 50, 200, 40, 100),
        ("fraction_of_capacity", 0.5, 200, 40, 100),
        ("kwh_above_base_minimum", 30, 200, 40, 70),
    ],
)
def test_quantity_to_value_reserve(unit, value, capacity, base_min, expected):
    q = Quantity(value=value, unit=unit, source_text="x")
    factor, min_kwh, max_grid = quantity_to_value("minimum_battery_reserve", q, capacity_kwh=capacity, base_min_kwh=base_min)
    assert min_kwh == pytest.approx(expected)
    assert factor is None and max_grid is None


def test_quantity_to_value_missing_quantity_raises():
    with pytest.raises(NormalizationError):
        quantity_to_value("solar_reduction", None, capacity_kwh=200, base_min_kwh=40)


def test_quantity_to_value_no_charge_ignores_quantity():
    factor, min_kwh, max_grid = quantity_to_value("no_charge_window", None, capacity_kwh=200, base_min_kwh=40)
    assert (factor, min_kwh, max_grid) == (None, None, None)
