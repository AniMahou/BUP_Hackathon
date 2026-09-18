from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _reject_bool(v):
    if isinstance(v, bool):
        raise ValueError("boolean is not a valid number")
    return v


class HourInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

    @field_validator("hour", "demand_kwh", "solar_kwh", "tariff_bdt_per_kwh", mode="before")
    @classmethod
    def no_bool(cls, v):
        return _reject_bool(v)

    @field_validator("hour")
    @classmethod
    def hour_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("hour must be in 0..23")
        return v


class BatteryInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
        mode="before",
    )
    @classmethod
    def no_bool(cls, v):
        return _reject_bool(v)


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    scenario_id: str
    operator_notes: list[str]
    hours: list[HourInput]
    battery: BatteryInput

    @field_validator("scenario_id")
    @classmethod
    def scenario_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("scenario_id must be non-empty")
        return v

    @field_validator("operator_notes")
    @classmethod
    def notes_shape(cls, v: list[str]) -> list[str]:
        if not (1 <= len(v) <= 3):
            raise ValueError("operator_notes must contain 1 to 3 entries")
        for note in v:
            if not isinstance(note, str) or not note.strip():
                raise ValueError("operator_notes entries must be non-empty strings")
        return v

    @field_validator("hours")
    @classmethod
    def hours_shape(cls, v: list[HourInput]) -> list[HourInput]:
        if len(v) != 24:
            raise ValueError("hours must contain exactly 24 entries")
        seen = {h.hour for h in v}
        if len(seen) != 24 or seen != set(range(24)):
            raise ValueError("hours must have unique hour values covering 0..23")
        return v

    @model_validator(mode="after")
    def semantic_checks(self) -> "OptimizeRequest":
        errors: list[dict] = []

        for note in self.operator_notes:
            if len(note) > 4000:
                errors.append({"field": "operator_notes", "issue": "note exceeds 4000 characters"})

        b = self.battery
        if b.capacity_kwh < 0:
            errors.append({"field": "battery.capacity_kwh", "issue": "must be >= 0"})
        if b.minimum_energy_kwh < 0:
            errors.append({"field": "battery.minimum_energy_kwh", "issue": "must be >= 0"})
        if b.max_charge_kwh_per_hour < 0:
            errors.append({"field": "battery.max_charge_kwh_per_hour", "issue": "must be >= 0"})
        if b.max_discharge_kwh_per_hour < 0:
            errors.append({"field": "battery.max_discharge_kwh_per_hour", "issue": "must be >= 0"})
        if b.minimum_energy_kwh > b.capacity_kwh:
            errors.append({"field": "battery.minimum_energy_kwh", "issue": "must be <= capacity_kwh"})
        if not (b.minimum_energy_kwh <= b.initial_energy_kwh <= b.capacity_kwh):
            errors.append(
                {"field": "battery.initial_energy_kwh", "issue": "must be within [minimum_energy_kwh, capacity_kwh]"}
            )

        for h in self.hours:
            if h.demand_kwh < 0:
                errors.append({"field": f"hours[{h.hour}].demand_kwh", "issue": "must be >= 0"})
            if h.solar_kwh < 0:
                errors.append({"field": f"hours[{h.hour}].solar_kwh", "issue": "must be >= 0"})

        huge = 1e9
        for h in self.hours:
            for field_name, value in (
                ("demand_kwh", h.demand_kwh),
                ("solar_kwh", h.solar_kwh),
                ("tariff_bdt_per_kwh", h.tariff_bdt_per_kwh),
            ):
                if abs(value) > huge:
                    errors.append({"field": f"hours[{h.hour}].{field_name}", "issue": "magnitude too large"})
        for field_name, value in (
            ("capacity_kwh", b.capacity_kwh),
            ("initial_energy_kwh", b.initial_energy_kwh),
            ("minimum_energy_kwh", b.minimum_energy_kwh),
            ("max_charge_kwh_per_hour", b.max_charge_kwh_per_hour),
            ("max_discharge_kwh_per_hour", b.max_discharge_kwh_per_hour),
        ):
            if abs(value) > huge:
                errors.append({"field": f"battery.{field_name}", "issue": "magnitude too large"})

        if errors:
            raise SemanticValidationError(errors)
        return self


class SemanticValidationError(ValueError):
    """Raised for business-rule violations that should map to HTTP 422 (as opposed to structural/type errors -> 400)."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("semantic validation failed")
