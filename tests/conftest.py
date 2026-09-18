import os

# Offline test suite: never call the real LLM (and never spend quota on warm-up), even if a local
# .env holds keys. Live checks live in tests/live and scripts/.
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_API_KEYS"] = ""

import pytest

from app.llm.cache import InterpretationCache
from app.llm.interpreter import NoteInterpreter
from app.schemas.request import BatteryInput, HourInput, OptimizeRequest
from tests.fakes.fake_llm import FakeGeminiClient


@pytest.fixture
def default_battery() -> BatteryInput:
    return BatteryInput(
        capacity_kwh=200.0,
        initial_energy_kwh=120.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


@pytest.fixture
def default_hours() -> list[HourInput]:
    hours = []
    for h in range(24):
        if h < 6:
            demand, tariff = 90.0, 5.0
        elif h < 17:
            demand, tariff = 160.0, 12.0
        elif h < 22:
            demand, tariff = 210.0, 28.0
        else:
            demand, tariff = 120.0, 8.0
        solar = max(0.0, 180.0 * ((h - 6) / 11.0)) if 6 <= h <= 17 else 0.0
        solar = min(solar, 180.0)
        hours.append(HourInput(hour=h, demand_kwh=demand, solar_kwh=round(solar, 2), tariff_bdt_per_kwh=tariff))
    return hours


@pytest.fixture
def make_request(default_battery, default_hours):
    def _make(notes: list[str], battery: BatteryInput | None = None, hours: list[HourInput] | None = None, scenario_id: str = "test"):
        return OptimizeRequest(
            scenario_id=scenario_id,
            operator_notes=notes,
            hours=hours or default_hours,
            battery=battery or default_battery,
        )

    return _make


def build_interpreter(fake_client: FakeGeminiClient, **overrides) -> NoteInterpreter:
    defaults = dict(
        model_chain=["fake-model-1", "fake-model-2"],
        primary_timeout_s=5.0,
        fallback_timeout_s=5.0,
        max_repairs=1,
        thinking_budget=0,
        cache=InterpretationCache(0),
        prompt_version="test",
        enable_hedging=True,
        enable_rule_fallback=True,
    )
    defaults.update(overrides)
    return NoteInterpreter(client=fake_client, **defaults)
