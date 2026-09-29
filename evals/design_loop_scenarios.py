#!/usr/bin/env python3
"""Design-loop scenario runner (FORGE-292, gap G-G6).

Exercises the closed design loop end to end against a real CAD work
product and grades it with ``design_loop_rubric.evaluate_design_loop`` --
a real outcome check, not the substring/regex "keyword-based" grading the
rest of this directory's rubrics use. Writes a ``summary`` shaped exactly
like ``run_scenarios.py``'s own ``summarize()`` output (``completed_rate``,
``avg_completeness``) so the SAME ``GET /v1/evals`` route and
``evals/trend.py history`` can read either suite's reports interchangeably.

Deliberately a standalone in-process script, not a new ``evals/scenarios/
*.json`` entry driven through the full agentic ``POST /v1/runs`` harness:
the design loop's own bisection is deterministic math with nothing for an
LLM to decide, so routing it through an agentic run would add cost and
flakiness without adding signal. Talks to the twin directly
(``twin_core.api.InMemoryTwinAPI.create_from_env()``), the same pattern
this session's own live-validation scripts already use.

    python3 evals/design_loop_scenarios.py --work-product-id <id> \
        --out evals/reports/manual/report_design_loop.json

Demonstrated on the robotic-arm yardstick project (FORGE-292's own
definition-of-done line) by pointing ``--work-product-id`` at the real
"Upper Arm Link" work product and running this against fidel-dev's own
Neo4j-backed twin.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)


async def run_once(*, work_product_id: str, load_n: float, deflection_limit_mm: float) -> dict:
    from design_loop_rubric import score_design_loop_run  # noqa: PLC0415

    from twin_core.api import InMemoryTwinAPI  # noqa: PLC0415

    started = time.time()
    rec: dict = {
        "scenario": "design_loop_wall_thickness",
        "terminal_status": "error",
    }
    try:
        twin = await InMemoryTwinAPI.create_from_env()
        result = await score_design_loop_run(
            twin=twin,
            work_product_id=work_product_id,
            load_n=load_n,
            deflection_limit_mm=deflection_limit_mm,
        )
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        rec["error"] = str(exc)
        rec["duration_s"] = round(time.time() - started, 1)
        return rec

    if "error" in result:
        rec["error"] = result["error"]
        rec["terminal_status"] = "error"
    else:
        rec["design_loop_rubric"] = result
        rec["terminal_status"] = "completed" if result["score"] == 1.0 else "failed"
        rec["deliverable_completeness"] = result["score"]
    rec["duration_s"] = round(time.time() - started, 1)
    return rec


def summarize(records: list[dict]) -> dict:
    n = len(records) or 1
    completed = sum(1 for r in records if r["terminal_status"] == "completed")
    return {
        "design_loop_wall_thickness": {
            "runs": len(records),
            "completed": completed,
            "completed_rate": round(completed / n, 3),
            "avg_completeness": round(
                sum(r.get("deliverable_completeness", 0) for r in records) / n, 3
            ),
        }
    }


async def main_async(args: argparse.Namespace) -> int:
    records = [
        await run_once(
            work_product_id=args.work_product_id,
            load_n=args.load_n,
            deflection_limit_mm=args.deflection_limit_mm,
        )
        for _ in range(args.repeat)
    ]
    report = {"suite": "design_loop_v1", "summary": summarize(records), "runs": records}
    if os.path.dirname(args.out):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"=== summary ===\n{json.dumps(report['summary'], indent=2)}", file=sys.stderr)
    print(f"report -> {args.out}", file=sys.stderr)
    return 0 if all(r["terminal_status"] == "completed" for r in records) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Design-loop scenario eval runner")
    ap.add_argument("--work-product-id", required=True, help="a CAD_MODEL work product id")
    ap.add_argument("--load-n", type=float, default=800.0)
    ap.add_argument("--deflection-limit-mm", type=float, default=0.6)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument(
        "--out", default=os.path.join(HERE, "reports", "manual", "report_design_loop.json")
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
