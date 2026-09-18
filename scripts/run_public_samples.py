"""Posts each case in tests/fixtures/public_samples.json to a running service, replays the
response against the case's ground truth (tol 0.01, matching the judge), and reports a summary.

NOTE: this repo does not ship the real public sample cases (they weren't available when this was
scaffolded) -- drop the official file at tests/fixtures/public_samples.json before running this.
Expected shape: a JSON array of {"request": <OptimizeRequest>, "ground_truth_directives": [...],
"reference_total_cost_bdt": <float>}.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.optimizer.constraints import build_bounds  # noqa: E402
from app.optimizer.postprocess import Totals  # noqa: E402
from app.schemas.directives import AppliedDirective  # noqa: E402
from app.schemas.request import OptimizeRequest  # noqa: E402
from app.schemas.response import OptimizeResponse  # noqa: E402
from app.verification.replay import replay  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "public_samples.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    if not FIXTURE_PATH.exists() or FIXTURE_PATH.stat().st_size == 0:
        print(f"No public sample cases found at {FIXTURE_PATH}.")
        print("Drop the official 'Public Sample Cases JSON' there (see this script's docstring) and re-run.")
        return 1

    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    passed = 0
    for i, case in enumerate(cases):
        req = OptimizeRequest.model_validate(case["request"])
        resp = httpx.post(f"{args.base_url}/optimize-energy", json=case["request"], timeout=30.0)
        resp.raise_for_status()
        response = OptimizeResponse.model_validate(resp.json())

        ground_truth = [AppliedDirective.model_validate(d) for d in case["ground_truth_directives"]]
        bounds = build_bounds(ground_truth, req.battery.minimum_energy_kwh, include_hedges=False)
        totals = Totals(
            total_grid_kwh=response.total_grid_kwh,
            total_cost_bdt=response.total_cost_bdt,
            peak_grid_kwh=response.peak_grid_kwh,
        )
        violations = replay(req, bounds, response.hourly_plan, totals, tol=0.01)

        cost_ok = abs(response.total_cost_bdt - case["reference_total_cost_bdt"]) <= 0.01
        ok = not violations and cost_ok
        passed += ok
        print(f"case {i} ({req.scenario_id}): {'PASS' if ok else 'FAIL'}" + ("" if ok else f" violations={violations} cost={response.total_cost_bdt} ref={case['reference_total_cost_bdt']}"))

    print(f"\n{passed}/{len(cases)} cases valid vs ground truth.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
