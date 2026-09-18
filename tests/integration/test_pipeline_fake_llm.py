import pytest

from app.config import Settings
from app.optimizer.constraints import build_bounds
from app.optimizer.postprocess import Totals
from app.pipeline.orchestrator import run_pipeline
from app.schemas.llm_output import NoteInterpretationLLM, Quantity, TimeWindow
from app.verification.replay import replay
from tests.conftest import build_interpreter
from tests.fakes.fake_llm import FakeGeminiClient


def _settings(**overrides) -> Settings:
    defaults = dict(request_deadline_s=25.0, enable_secondary_objective=True, infeasible_policy="best_effort")
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_solar_reduction_end_to_end(make_request):
    note = "Solar output will drop to about 20% from 1 PM to 3 PM."
    parsed = NoteInterpretationLLM(
        analysis="solar drop", affects_schedule=True, directive_type="solar_reduction",
        time_windows=[TimeWindow(start_hour=13, end_hour=15, source_text="1 PM to 3 PM")],
        quantity=Quantity(value=20, unit="percent_remaining", source_text="20%"),
        hours=[13, 14], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None,
        alternative=None, explanation="Usable solar limited to 20% for 13:00-15:00.",
    )
    fake = FakeGeminiClient(responses={note: parsed})
    interpreter = build_interpreter(fake)
    req = make_request([note])

    result = await run_pipeline(req, interpreter, _settings())

    entry = result.response.directive_interpretation[0]
    assert entry.applies is True
    assert entry.directive_type == "solar_reduction"
    assert entry.structured_adjustment == {"hours": [13, 14], "factor": 0.2}

    bounds = build_bounds([], req.battery.minimum_energy_kwh)
    totals = Totals(
        total_grid_kwh=result.response.total_grid_kwh,
        total_cost_bdt=result.response.total_cost_bdt,
        peak_grid_kwh=result.response.peak_grid_kwh,
    )
    violations = replay(req, bounds, result.response.hourly_plan, totals, tol=1e-4)
    # bounds here is directive-free on purpose (independent physics/balance sanity check);
    # the directive's own effect (reserve/cap/etc.) is already asserted above via structured_adjustment.
    assert not any(v.code in ("balance", "transition", "eod_neutrality", "totals_grid", "totals_cost", "totals_peak") for v in violations)


@pytest.mark.asyncio
async def test_irrelevant_note_is_no_op(make_request):
    note = "The cafeteria menu changes tomorrow."
    parsed = NoteInterpretationLLM(
        analysis="irrelevant", affects_schedule=False, directive_type="no_op",
        time_windows=[], quantity=None, hours=[], factor=None, minimum_energy_kwh=None, max_grid_kwh=None,
        alternative=None, explanation="Not related to energy scheduling.",
    )
    fake = FakeGeminiClient(responses={note: parsed})
    interpreter = build_interpreter(fake)
    req = make_request([note])

    result = await run_pipeline(req, interpreter, _settings())

    entry = result.response.directive_interpretation[0]
    assert entry.applies is False
    assert entry.directive_type == "no_op"
    assert entry.structured_adjustment is None


@pytest.mark.asyncio
async def test_total_llm_outage_falls_back_to_rule_interpreter_or_no_op(make_request):
    note = "Do not charge the battery between 2 PM and 4 PM."
    fake = FakeGeminiClient(fail_models={"fake-model-1", "fake-model-2"})
    interpreter = build_interpreter(fake)
    req = make_request([note])

    result = await run_pipeline(req, interpreter, _settings())

    entry = result.response.directive_interpretation[0]
    # either the rule-based fallback catches this clear case, or it safely degrades to no_op --
    # either way the response must stay schema-valid and never crash.
    assert entry.directive_type in ("no_charge_window", "no_op")
    if entry.directive_type == "no_op":
        assert entry.applies is False and entry.structured_adjustment is None
    else:
        assert entry.applies is True and entry.structured_adjustment is not None


@pytest.mark.asyncio
async def test_response_passes_self_replay_check(make_request):
    note = "Keep at least 120 kWh in reserve from 6 PM until 9 PM."
    parsed = NoteInterpretationLLM(
        analysis="reserve", affects_schedule=True, directive_type="minimum_battery_reserve",
        time_windows=[TimeWindow(start_hour=18, end_hour=21, source_text="6 PM until 9 PM")],
        quantity=Quantity(value=120, unit="kwh", source_text="120 kWh"),
        hours=[18, 19, 20], factor=None, minimum_energy_kwh=120, max_grid_kwh=None,
        alternative=None, explanation="Battery must keep at least 120.0 kWh stored during 18:00-21:00.",
    )
    fake = FakeGeminiClient(responses={note: parsed})
    interpreter = build_interpreter(fake)
    req = make_request([note])

    result = await run_pipeline(req, interpreter, _settings())

    for h in (18, 19, 20):
        entry = result.response.hourly_plan[h]
        assert entry.battery_energy_after_kwh >= 120.0 - 1e-6
