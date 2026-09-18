"""Judge simulator for the official public sample pack.

POSTs every case's `input` to a running service, then scores the response the way the hidden judge
does: interpretation vs ground truth (relevance / type / hours / values / shape), plan replayed
against the GROUND-TRUTH directives (tol 0.01), recomputed totals, and cost vs the reference optimum.

Usage:
    python scripts/run_public_samples.py --base-url http://localhost:8000
    python scripts/run_public_samples.py --base-url https://<deployed-host> --pack path/to/pack.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.optimizer.constraints import build_bounds  # noqa: E402
from app.optimizer.postprocess import Totals  # noqa: E402
from app.schemas.directives import AppliedDirective  # noqa: E402
from app.schemas.request import OptimizeRequest  # noqa: E402
from app.schemas.response import OptimizeResponse  # noqa: E402
from app.verification.replay import replay  # noqa: E402

DEFAULT_PACK = ROOT / "tests" / "fixtures" / "public_samples.json"
VALUE_KEYS = ("factor", "minimum_energy_kwh", "max_grid_kwh")


def ground_truth_directives(expected_interp: list[dict]) -> list[AppliedDirective]:
    out = []
    for e in expected_interp:
        if e["directive_type"] == "no_op":
            continue
        sa = e["structured_adjustment"]
        out.append(
            AppliedDirective(
                note_index=e["note_index"], type=e["directive_type"], hours=sa["hours"],
                factor=sa.get("factor"), minimum_energy_kwh=sa.get("minimum_energy_kwh"),
                max_grid_kwh=sa.get("max_grid_kwh"), is_hedge=False,
            )
        )
    return out


def compare_interpretation(got: list[dict], expected: list[dict]) -> list[str]:
    problems = []
    if [g.get("note_index") for g in got] != [e["note_index"] for e in expected]:
        return [f"note_index order/coverage {[g.get('note_index') for g in got]}"]
    for g, e in zip(got, expected, strict=True):
        i = e["note_index"]
        if g["applies"] != e["applies"]:
            problems.append(f"note {i}: applies {g['applies']} != {e['applies']}")
        if g["directive_type"] != e["directive_type"]:
            problems.append(f"note {i}: type {g['directive_type']} != {e['directive_type']}")
            continue
        gs, es = g["structured_adjustment"], e["structured_adjustment"]
        if es is None or gs is None:
            if gs != es:
                problems.append(f"note {i}: structured_adjustment {gs} != {es}")
            continue
        if set(gs) != set(es):
            problems.append(f"note {i}: shape keys {sorted(gs)} != {sorted(es)}")
        if gs.get("hours") != es["hours"]:
            problems.append(f"note {i}: hours {gs.get('hours')} != {es['hours']}")
        for k in VALUE_KEYS:
            if k in es and (gs.get(k) is None or abs(gs[k] - es[k]) > 0.01):
                problems.append(f"note {i}: {k} {gs.get(k)} != {es[k]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--pack", default=str(DEFAULT_PACK))
    ap.add_argument("--repeat", type=int, default=1, help="send each case N times (stability check)")
    args = ap.parse_args()

    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    cases = pack["cases"] if isinstance(pack, dict) else pack

    n = interp_ok = plan_ok = cost_ok = 0
    latencies: list[float] = []
    for case in cases:
        for _ in range(args.repeat):
            n += 1
            inp, exp = case["input"], case["expected_output"]
            t0 = time.monotonic()
            r = httpx.post(f"{args.base_url.rstrip('/')}/optimize-energy", json=inp, timeout=35.0)
            latencies.append(time.monotonic() - t0)
            if r.status_code != 200:
                print(f"{case['id']}: HTTP {r.status_code} {r.text[:200]}")
                continue
            body = r.json()
            resp = OptimizeResponse.model_validate(body)
            req = OptimizeRequest.model_validate(inp)

            iproblems = compare_interpretation(body["directive_interpretation"], exp["directive_interpretation"])
            bounds = build_bounds(ground_truth_directives(exp["directive_interpretation"]), req.battery.minimum_energy_kwh)
            totals = Totals(resp.total_grid_kwh, resp.total_cost_bdt, resp.peak_grid_kwh)
            violations = replay(req, bounds, resp.hourly_plan, totals, tol=0.01)
            echo_ok = body["scenario_id"] == inp["scenario_id"]
            c_ok = abs(resp.total_cost_bdt - exp["total_cost_bdt"]) <= 0.01

            interp_ok += not iproblems
            plan_ok += (not violations) and echo_ok
            cost_ok += c_ok and not violations
            status = "PASS" if (not iproblems and not violations and c_ok and echo_ok) else "FAIL"
            print(f"{case['id']}: {status}  {latencies[-1]:.2f}s  cost={resp.total_cost_bdt} ref={exp['total_cost_bdt']}")
            for p in iproblems:
                print(f"    interpretation: {p}")
            for v in violations[:5]:
                print(f"    plan: {v}")

    latencies.sort()
    p95 = latencies[max(0, int(round(0.95 * len(latencies))) - 1)]
    print(f"\ninterpretation exact: {interp_ok}/{n} | plans valid vs ground truth: {plan_ok}/{n} | "
          f"optimal cost: {cost_ok}/{n} | p50 {latencies[len(latencies)//2]:.2f}s p95 {p95:.2f}s")
    return 0 if interp_ok == plan_ok == cost_ok == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
