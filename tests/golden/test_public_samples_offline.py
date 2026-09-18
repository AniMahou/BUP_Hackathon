"""Golden test against the organizers' public sample cases, replayed with a fake LLM that returns
the case's ground-truth interpretation directly (so this test is about the optimizer/replay path,
not the LLM). Skips cleanly if the real fixture hasn't been dropped in yet -- see
scripts/run_public_samples.py's docstring for the expected shape and why it's not shipped here.
"""

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "public_samples.json"

pytestmark = pytest.mark.skipif(
    not FIXTURE_PATH.exists() or FIXTURE_PATH.stat().st_size == 0,
    reason="tests/fixtures/public_samples.json not populated (official pack not available in this environment)",
)


def _load_cases():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases() if FIXTURE_PATH.exists() and FIXTURE_PATH.stat().st_size else [])
@pytest.mark.asyncio
async def test_case_matches_reference_cost(case):
    from app.config import Settings
    from app.optimizer.constraints import build_bounds
    from app.pipeline.orchestrator import run_pipeline
    from app.schemas.directives import AppliedDirective
    from app.schemas.llm_output import NoteInterpretationLLM
    from app.schemas.request import OptimizeRequest
    from app.verification.replay import replay
    from tests.conftest import build_interpreter
    from tests.fakes.fake_llm import FakeGeminiClient

    req = OptimizeRequest.model_validate(case["request"])
    responses = {
        req.operator_notes[i]: NoteInterpretationLLM.model_validate(case["llm_outputs"][i])
        for i in range(len(req.operator_notes))
    }
    interpreter = build_interpreter(FakeGeminiClient(responses=responses))
    settings = Settings(request_deadline_s=25.0)

    result = await run_pipeline(req, interpreter, settings)

    assert result.response.total_cost_bdt == pytest.approx(case["reference_total_cost_bdt"], abs=0.01)

    ground_truth = [AppliedDirective.model_validate(d) for d in case["ground_truth_directives"]]
    bounds = build_bounds(ground_truth, req.battery.minimum_energy_kwh, include_hedges=False)
    from app.optimizer.postprocess import Totals

    totals = Totals(
        total_grid_kwh=result.response.total_grid_kwh,
        total_cost_bdt=result.response.total_cost_bdt,
        peak_grid_kwh=result.response.peak_grid_kwh,
    )
    violations = replay(req, bounds, result.response.hourly_plan, totals, tol=0.01)
    assert violations == []
