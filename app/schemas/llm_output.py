
from pydantic import BaseModel, Field

from app.schemas.directives import DirectiveType, QuantityUnit


class TimeWindow(BaseModel):
    start_hour: int = Field(description="0..23, inclusive start")
    end_hour: int = Field(description="0..24, exclusive end")
    source_text: str = Field(description="the exact phrase in the note this window came from")


class Quantity(BaseModel):
    value: float = Field(description="the number as written in the note, before any conversion")
    unit: QuantityUnit
    source_text: str = Field(description="the exact phrase in the note this number came from")


class AlternativeReading(BaseModel):
    directive_type: DirectiveType
    time_windows: list[TimeWindow]
    quantity: Quantity | None
    reason: str = Field(description="why the note genuinely supports this second reading, <= 20 words")


class NoteInterpretationLLM(BaseModel):
    """Structured-output schema handed to Gemini as response_schema. Field order matters: reasoning first."""

    analysis: str = Field(description="<= 40 words: what the note says and whether it changes today's schedule")
    affects_schedule: bool
    directive_type: DirectiveType
    time_windows: list[TimeWindow] = Field(description="[] for no_op")
    quantity: Quantity | None
    hours: list[int] = Field(description="final expanded hour list, channel B")
    factor: float | None = Field(description="final factor for solar_reduction only, else null")
    minimum_energy_kwh: float | None = Field(description="final kWh for minimum_battery_reserve only, else null")
    max_grid_kwh: float | None = Field(description="final kWh for max_grid_window only, else null")
    alternative: AlternativeReading | None = Field(description="only if genuinely ambiguous, else null")
    explanation: str = Field(description="<= 25 words, human-readable")
