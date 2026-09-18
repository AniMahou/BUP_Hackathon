from app.guardrails.normalizer import NormalizedNote
from app.guardrails.validator import validate
from app.schemas.llm_output import NoteInterpretationLLM, Quantity, TimeWindow


def _llm_out(**overrides) -> NoteInterpretationLLM:
    base = dict(
        analysis="x",
        affects_schedule=True,
        directive_type="solar_reduction",
        time_windows=[TimeWindow(start_hour=13, end_hour=15, source_text="1-3pm")],
        quantity=Quantity(value=20, unit="percent_remaining", source_text="20%"),
        hours=[13, 14],
        factor=0.2,
        minimum_energy_kwh=None,
        max_grid_kwh=None,
        alternative=None,
        explanation="e",
    )
    base.update(overrides)
    return NoteInterpretationLLM(**base)


def test_valid_solar_note_has_no_issues():
    llm_out = _llm_out()
    normalized = NormalizedNote(hours=[13, 14], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "Solar drops to 20% from 1 to 3 PM.", capacity_kwh=200)
    assert issues == []


def test_g5_empty_hours_flagged():
    llm_out = _llm_out()
    normalized = NormalizedNote(hours=[], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "text with 20", capacity_kwh=200)
    assert any(i.code == "G5" for i in issues)


def test_g6_factor_out_of_range():
    llm_out = _llm_out(factor=1.5)
    normalized = NormalizedNote(hours=[13, 14], factor=1.5, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "text", capacity_kwh=200)
    assert any(i.code == "G6" for i in issues)


def test_g7_reserve_exceeds_capacity():
    llm_out = _llm_out(
        directive_type="minimum_battery_reserve",
        quantity=Quantity(value=999, unit="kwh", source_text="999 kWh"),
        factor=None,
        minimum_energy_kwh=999,
    )
    normalized = NormalizedNote(hours=[13, 14], factor=None, minimum_energy_kwh=999, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "keep 999 kWh", capacity_kwh=200)
    assert any(i.code == "G7" for i in issues)


def test_g10_channel_disagreement():
    llm_out = _llm_out(hours=[13, 14, 15])  # channel B says 3 hours
    normalized = NormalizedNote(hours=[13, 14], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None)  # channel A says 2
    issues = validate(llm_out, normalized, "20% from 1-3pm", capacity_kwh=200)
    assert any(i.code == "G10" for i in issues)


def test_g11_grounding_missing_number():
    llm_out = _llm_out()
    normalized = NormalizedNote(hours=[13, 14], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "solar will be reduced somewhat this afternoon", capacity_kwh=200)
    assert any(i.code == "G11" for i in issues)


def test_g12_relevance_mismatch():
    llm_out = _llm_out(affects_schedule=False)  # says no_op-like but directive_type is not no_op
    normalized = NormalizedNote(hours=[13, 14], factor=0.2, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "20% from 1 to 3pm", capacity_kwh=200)
    assert any(i.code == "G12" for i in issues)


def test_no_op_skips_hour_and_quantity_checks():
    llm_out = _llm_out(
        directive_type="no_op", affects_schedule=False, time_windows=[], quantity=None, hours=[], factor=None,
    )
    normalized = NormalizedNote(hours=[], factor=None, minimum_energy_kwh=None, max_grid_kwh=None)
    issues = validate(llm_out, normalized, "the cafeteria menu changes tomorrow", capacity_kwh=200)
    assert issues == []
