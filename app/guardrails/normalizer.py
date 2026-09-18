"""Channel A: deterministic, authoritative normalization of the LLM's own extracted evidence
(time_windows, quantity) into hours / factor / minimum_energy_kwh / max_grid_kwh.

The LLM does semantics (what the note says); this module does arithmetic (§4.5 of planning.md).
"""

import math
from dataclasses import dataclass

from app.schemas.directives import DirectiveType
from app.schemas.llm_output import NoteInterpretationLLM, Quantity, TimeWindow


class NormalizationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def window_to_hours(start_hour: int, end_hour: int) -> list[int]:
    if not (0 <= start_hour <= 23):
        raise NormalizationError("bad_window", f"start_hour {start_hour} out of range 0..23")
    if not (0 <= end_hour <= 24):
        raise NormalizationError("bad_window", f"end_hour {end_hour} out of range 0..24")
    if start_hour < end_hour:
        return list(range(start_hour, end_hour))
    if start_hour > end_hour:
        return list(range(start_hour, 24)) + list(range(0, end_hour))
    raise NormalizationError("bad_window", "start_hour must not equal end_hour")


def windows_to_hours(windows: list[TimeWindow], directive_type: DirectiveType) -> list[int]:
    if not windows:
        if directive_type == "no_op":
            return []
        return list(range(24))
    hours: set[int] = set()
    for w in windows:
        hours.update(window_to_hours(w.start_hour, w.end_hour))
    return sorted(hours)


def quantity_to_value(
    directive_type: DirectiveType,
    quantity: Quantity | None,
    capacity_kwh: float,
    base_min_kwh: float,
) -> tuple[float | None, float | None, float | None]:
    """Returns (factor, minimum_energy_kwh, max_grid_kwh) derived purely from the quantity (channel A)."""
    if directive_type in ("no_charge_window", "no_discharge_window", "no_op"):
        return None, None, None
    if quantity is None:
        raise NormalizationError("missing_quantity", f"{directive_type} requires a quantity")

    v, unit = quantity.value, quantity.unit
    factor = minimum_energy_kwh = max_grid_kwh = None

    if directive_type == "solar_reduction":
        if unit == "percent_remaining":
            factor = v / 100
        elif unit == "percent_reduction":
            factor = 1 - v / 100
        elif unit == "fraction_remaining":
            factor = v
        elif unit == "fraction_reduction":
            factor = 1 - v
        else:
            raise NormalizationError("bad_unit", f"unit {unit} invalid for solar_reduction")
        factor = round(factor, 6)
    elif directive_type == "minimum_battery_reserve":
        if unit == "kwh":
            minimum_energy_kwh = v
        elif unit == "mwh":
            minimum_energy_kwh = v * 1000
        elif unit == "percent_of_capacity":
            minimum_energy_kwh = v / 100 * capacity_kwh
        elif unit == "fraction_of_capacity":
            minimum_energy_kwh = v * capacity_kwh
        elif unit == "kwh_above_base_minimum":
            minimum_energy_kwh = base_min_kwh + v
        else:
            raise NormalizationError("bad_unit", f"unit {unit} invalid for minimum_battery_reserve")
        minimum_energy_kwh = round(minimum_energy_kwh, 6)
    elif directive_type == "max_grid_window":
        if unit == "kwh":
            max_grid_kwh = v
        elif unit == "mwh":
            max_grid_kwh = v * 1000
        else:
            raise NormalizationError("bad_unit", f"unit {unit} invalid for max_grid_window")
        max_grid_kwh = round(max_grid_kwh, 6)

    for val in (factor, minimum_energy_kwh, max_grid_kwh):
        if val is not None and not math.isfinite(val):
            raise NormalizationError("non_finite", "computed value is not finite")
    return factor, minimum_energy_kwh, max_grid_kwh


@dataclass
class NormalizedNote:
    hours: list[int]
    factor: float | None
    minimum_energy_kwh: float | None
    max_grid_kwh: float | None


def normalize(llm_out: NoteInterpretationLLM, capacity_kwh: float, base_min_kwh: float) -> NormalizedNote:
    hours = windows_to_hours(llm_out.time_windows, llm_out.directive_type)
    factor, min_kwh, max_grid = quantity_to_value(
        llm_out.directive_type, llm_out.quantity, capacity_kwh, base_min_kwh
    )
    return NormalizedNote(hours=hours, factor=factor, minimum_energy_kwh=min_kwh, max_grid_kwh=max_grid)
