"""Golden test against the organizers' official public sample pack (tests/fixtures/public_samples.json,
an unmodified copy of BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json).

A fake LLM returns the ground-truth interpretation of each note (as the LLM *evidence* format:
time windows + quantity), so this exercises the full pipeline offline: normalizer -> guardrails ->
assembler -> constraint builder -> LP -> post-processing -> self-replay -> response. We then score
the response exactly like the judge: interpretation equals ground truth, plan replays cleanly
against the ground-truth directives (tol 0.01), totals recompute, cost equals the reference optimum.
"""

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.optimizer.constraints import build_bounds
from app.optimizer.postprocess import Totals
from app.pipeline.orchestrator import run_pipeline
from app.schemas.directives import AppliedDirective
from app.schemas.llm_output import NoteInterpretationLLM
from app.schemas.request import OptimizeRequest
from app.verification.replay import replay
from tests.conftest import build_interpreter
from tests.fakes.fake_llm import FakeGeminiClient

PACK = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "public_samples.json").read_text("utf-8"))
CASES = PACK["cases"]


def hours_to_windows(hours: list[int]) -> list[dict]:
    windows, start, prev = [], None, None
    for h in hours:
        if start is None:
            start = prev = h
        elif h == prev + 1:
            prev = h
        else:
            windows.append({"start_hour": start, "end_hour": prev + 1, "source_text": "x"})
            start = prev = h
    if start is not None:
        windows.append({"start_hour": start, "end_hour": prev + 1, "source_text": "x"})
    return windows


def llm_output_for(expected: dict) -> NoteInterpretationLLM:
    dt, sa = expected["directive_type"], expected["structured_adjustment"]
    if dt == "no_op":
        return NoteInterpretationLLM(
            analysis="irrelevant", affects_schedule=False, directive_type="no_op", time_windows=[],
            quantity=None, hours=[], factor=None, minimum_energy_kwh=None, max_grid_kwh=None,
            alternative=None, explanation="Not an energy directive.",
        )
    quantity = None
    if dt == "solar_reduction":
        quantity = {"value": sa["factor"], "unit": "fraction_remaining", "source_text": "x"}
    elif dt == "minimum_battery_reserve":
        quantity = {"value": sa["minimum_energy_kwh"], "unit": "kwh", "source_text": "x"}
    elif dt == "max_grid_window":
        quantity = {"value": sa["max_grid_kwh"], "unit": "kwh", "source_text": "x"}
    return NoteInterpretationLLM.model_validate({
        "analysis": "ground truth", "affects_schedule": True, "directive_type": dt,
        "time_windows": hours_to_windows(sa["hours"]), "quantity": quantity, "hours": sa["hours"],
        "factor": sa.get("factor"), "minimum_energy_kwh": sa.get("minimum_energy_kwh"),
        "max_grid_kwh": sa.get("max_grid_kwh"), "alternative": None, "explanation": "ground truth",
    })


def ground_truth(expected_interp: list[dict]) -> list[AppliedDirective]:
    out = []
    for e in expected_interp:
        if e["directive_type"] == "no_op":
            continue
        sa = e["structured_adjustment"]
        out.append(AppliedDirective(
            note_index=e["note_index"], type=e["directive_type"], hours=sa["hours"], factor=sa.get("factor"),
            minimum_energy_kwh=sa.get("minimum_energy_kwh"), max_grid_kwh=sa.get("max_grid_kwh"), is_hedge=False,
        ))
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
@pytest.mark.asyncio
async def test_public_sample_end_to_end_offline(case):
    inp, exp = case["input"], case["expected_output"]
    req = OptimizeRequest.model_validate(inp)
    responses = {
        note.strip(): llm_output_for(exp["directive_interpretation"][i]) for i, note in enumerate(inp["operator_notes"])
    }
    # Hedging off: the regex cross-check must not change the optimum on these unambiguous notes.
    interpreter = build_interpreter(FakeGeminiClient(responses=responses), enable_hedging=False)
    result = await run_pipeline(req, interpreter, Settings(infeasible_policy="best_effort"))
    body = result.response.model_dump()

    # Interpretation == ground truth (type, applies, hours, values, exact shape).
    got = body["directive_interpretation"]
    assert [g["note_index"] for g in got] == list(range(len(inp["operator_notes"])))
    for g, e in zip(got, exp["directive_interpretation"], strict=True):
        assert g["applies"] == e["applies"] and g["directive_type"] == e["directive_type"]
        if e["structured_adjustment"] is None:
            assert g["structured_adjustment"] is None
        else:
            assert set(g["structured_adjustment"]) == set(e["structured_adjustment"])
            for k, v in e["structured_adjustment"].items():
                assert g["structured_adjustment"][k] == pytest.approx(v, abs=1e-6)

    # Plan valid against the GROUND-TRUTH directives, exactly as the judge replays it.
    bounds = build_bounds(ground_truth(exp["directive_interpretation"]), req.battery.minimum_energy_kwh)
    totals = Totals(body["total_grid_kwh"], body["total_cost_bdt"], body["peak_grid_kwh"])
    assert replay(req, bounds, result.response.hourly_plan, totals, tol=0.01) == []

    # Optimal cost (the LP must hit the organizer reference optimum).
    assert body["total_cost_bdt"] == pytest.approx(exp["total_cost_bdt"], abs=0.01)
    assert body["scenario_id"] == inp["scenario_id"]
    assert list(body) == ["scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh",
                          "total_cost_bdt", "peak_grid_kwh", "plan_summary"]
