"""Live LLM interpretation eval (hallucination / paraphrase robustness), rubric-style.

Runs every note in tests/fixtures/paraphrase_corpus.jsonl through the REAL interpreter (Gemini,
guardrails, repair loop, fallbacks) with the cache disabled, and scores it the way Category 1 is
scored: relevance, directive type, hours, numeric value, and exact shape. Notes whose wording is
genuinely ambiguous list extra `acceptable` answers.

Battery context for every note: capacity 200, minimum 40, initial 120, rates 50/50 kWh.

Usage:  python scripts/eval_interpreter.py [--concurrency 2] [--only p03,p14]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.llm.cache import InterpretationCache  # noqa: E402
from app.llm.gemini_client import GeminiClient  # noqa: E402
from app.llm.interpreter import NoteInterpreter  # noqa: E402
from app.pipeline.deadline import Deadline  # noqa: E402
from app.schemas.request import OptimizeRequest  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "paraphrase_corpus.jsonl"
BATTERY = {"capacity_kwh": 200, "initial_energy_kwh": 120, "minimum_energy_kwh": 40,
           "max_charge_kwh_per_hour": 50, "max_discharge_kwh_per_hour": 50}


def matches(got: dict, exp: dict) -> tuple[bool, bool, bool, bool]:
    """(relevance, type, hours, value) correctness."""
    rel = (got["directive_type"] == "no_op") == (exp["directive_type"] == "no_op")
    typ = got["directive_type"] == exp["directive_type"]
    gs, es = got["structured_adjustment"], exp["structured_adjustment"]
    if es is None:
        return rel, typ, typ, typ
    if not typ or gs is None:
        return rel, typ, False, False
    hours = gs.get("hours") == es["hours"]
    value = all(abs((gs.get(k) if gs.get(k) is not None else -1e9) - v) <= 0.005 for k, v in es.items() if k != "hours") and set(gs) == set(es)
    return rel, typ, hours, value


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--corpus", default=str(CORPUS), help="JSONL; rows may carry their own battery / context_notes")
    args = ap.parse_args()

    rows = [json.loads(line) for line in Path(args.corpus).read_text("utf-8").splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        rows = [r for r in rows if r["id"] in wanted]

    interpreter = NoteInterpreter(
        client=GeminiClient(settings.api_key_list), model_chain=settings.model_chain,
        primary_timeout_s=settings.llm_timeout_s, fallback_timeout_s=settings.llm_fallback_timeout_s,
        max_repairs=settings.llm_max_repairs, thinking_budget=settings.thinking_budget,
        cache=InterpretationCache(0), prompt_version=settings.prompt_version,
        enable_hedging=settings.enable_hedging, enable_rule_fallback=settings.enable_rule_fallback,
    )
    hours = [{"hour": h, "demand_kwh": 150, "solar_kwh": 100 if 7 <= h <= 17 else 0, "tariff_bdt_per_kwh": 10} for h in range(24)]
    sem = asyncio.Semaphore(args.concurrency)
    score = {"relevance": 0, "type": 0, "hours": 0, "value": 0, "exact": 0}
    latencies, degraded = [], 0

    async def run(row):
        nonlocal degraded
        async with sem:
            notes = row.get("context_notes") or [row["note"]]
            idx = row.get("note_index", 0)
            req = OptimizeRequest.model_validate({"scenario_id": row["id"], "operator_notes": notes, "hours": hours, "battery": row.get("battery", BATTERY)})
            t0 = time.monotonic()
            res = await interpreter.interpret_request(req, Deadline(25))
            latencies.append(time.monotonic() - t0)
            got = res.entries[idx].model_dump()
            degraded += bool(res.meta.get("degraded"))
            candidates = [row["expected"], *[{"directive_type": row["expected"]["directive_type"], "structured_adjustment": a} for a in row["acceptable"]]]
            best = max((matches(got, c) for c in candidates), key=sum)
            for k, ok in zip(("relevance", "type", "hours", "value"), best, strict=True):
                score[k] += ok
            score["exact"] += all(best)
            flag = "OK  " if all(best) else "FAIL"
            model = res.meta.get("per_note_model", ["?"] * (idx + 1))[idx]
            print(f"{flag} {row['id']} [{row['tag']}] via {model}: {row['note'][:70]}")
            if not all(best):
                print(f"       got      {got['directive_type']} {got['structured_adjustment']}")
                print(f"       expected {row['expected']['directive_type']} {row['expected']['structured_adjustment']}")

    await asyncio.gather(*(run(r) for r in rows))
    n = len(rows)
    latencies.sort()
    print("\n" + " | ".join(f"{k} {v}/{n}" for k, v in score.items()) +
          f" | degraded {degraded} | p50 {latencies[n // 2]:.2f}s p95 {latencies[max(0, int(0.95 * n) - 1)]:.2f}s")
    return 0 if score["exact"] == n else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
