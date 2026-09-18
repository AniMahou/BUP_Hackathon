from typing import Literal

from pydantic import BaseModel, ConfigDict

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

DIRECTIVE_TYPES: tuple[DirectiveType, ...] = (
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
)

QuantityUnit = Literal[
    "percent_remaining",
    "percent_reduction",
    "fraction_remaining",
    "fraction_reduction",
    "kwh",
    "mwh",
    "percent_of_capacity",
    "fraction_of_capacity",
    "kwh_above_base_minimum",
    "none",
]


class AppliedDirective(BaseModel):
    """One hard constraint fed into the optimizer, derived from one note (or an alternative/hedge reading of it)."""

    model_config = ConfigDict(extra="forbid")

    note_index: int
    type: DirectiveType
    hours: list[int]
    factor: float | None = None
    minimum_energy_kwh: float | None = None
    max_grid_kwh: float | None = None
    is_hedge: bool = False


def structured_adjustment_for(
    directive_type: DirectiveType,
    hours: list[int],
    factor: float | None,
    minimum_energy_kwh: float | None,
    max_grid_kwh: float | None,
) -> dict | None:
    """Builds the exact-shape structured_adjustment dict for the response, per the API contract."""
    if directive_type == "no_op":
        return None
    if directive_type == "solar_reduction":
        return {"hours": hours, "factor": factor}
    if directive_type == "minimum_battery_reserve":
        return {"hours": hours, "minimum_energy_kwh": minimum_energy_kwh}
    if directive_type == "no_charge_window":
        return {"hours": hours}
    if directive_type == "no_discharge_window":
        return {"hours": hours}
    if directive_type == "max_grid_window":
        return {"hours": hours, "max_grid_kwh": max_grid_kwh}
    raise ValueError(f"unknown directive_type: {directive_type}")
