#!/usr/bin/env python3
"""Scenario eval runner for the design-flow harness (MET-10).

Drives reference hardware/robotics projects through the *real* gated design-flow
harness (`POST /v1/runs`, flow = the scenario's flow) unattended — auto-approving
each gate — and writes a structured eval record per run: which gates were
reached, which deliverables actually landed in the twin, deliverable
completeness against the fixture's expectation, terminal status, and duration.

This is step 1-2 of the eval flywheel (scenarios → experimentation → data). It
captures *presence/completeness* today; rubric/quality scoring (LLM-as-judge
against each scenario's `definition_of_done`) is the next step and is left as a
field (`definition_of_done`) carried through into the report so a scorer can
consume it.

    python3 evals/run_scenarios.py --gateway http://fidel-dev:8000 --repeat 3
    python3 evals/run_scenarios.py --scenario l0_bracket --out evals/report.json

Stdlib only; talks HTTP to a running gateway with a real LLM + tools.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TERMINAL = {"completed", "failed", "rejected", "canceled"}


def api(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def load_scenarios(only: str | None) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "scenarios", "*.json"))):
        sc = json.load(open(path, encoding="utf-8"))
        if only is None or sc["id"] == only:
            out.append(sc)
    return out


def deliverable_counts(base: str, project_id: str) -> dict[str, int]:
    """Count the project's work products by type (what actually landed)."""
    try:
        proj = api(base, "GET", f"/v1/projects/{project_id}")
    except urllib.error.URLError:
        return {}
    counts: dict[str, int] = {}
    for wp in proj.get("work_products", []):
        t = wp.get("type", "?")
        counts[t] = counts.get(t, 0) + 1
    return counts


def completeness(expected: dict[str, int], present: dict[str, int]) -> float:
    """Fraction of expected deliverable *types* met (present >= expected)."""
    if not expected:
        return 1.0
    met = sum(1 for t, n in expected.items() if present.get(t, 0) >= n)
    return round(met / len(expected), 3)


def run_once(base: str, scenario: dict, idx: int, cap_s: float) -> dict:
    started = time.time()
    rec: dict = {
        "scenario": scenario["id"],
        "run": idx,
        "flow": scenario.get("flow", "design_v1"),
        "gates": [],
        "terminal_status": "error",
    }
    try:
        proj = api(
            base,
            "POST",
            "/v1/projects",
            {"name": f"eval-{scenario['id']}-{idx}-{int(started)}"},
        )
        pid = proj["id"]
        rec["project_id"] = pid
        run = api(
            base,
            "POST",
            "/v1/runs",
            {
                "request": {
                    "flow": scenario["flow"],
                    "goal": scenario["goal"],
                    "project_id": pid,
                },
                "start": True,
            },
        )
        run_id = run["id"]
        rec["run_id"] = run_id
    except (urllib.error.URLError, KeyError) as exc:
        rec["error"] = f"launch failed: {exc}"
        return rec

    seen_gates = 0
    while time.time() - started < cap_s:
        time.sleep(6)
        try:
            r = api(base, "GET", f"/v1/runs/{run_id}")
        except urllib.error.URLError:
            continue
        status = r.get("status", "?")
        if status == "awaiting_approval":
            seen_gates += 1
            rec["gates"].append(
                {
                    "reason": (r.get("approval_reason") or "")[:600],
                    "deliverables_after": deliverable_counts(base, pid),
                }
            )
            try:
                api(base, "POST", f"/v1/runs/{run_id}/approval", {"decision": "approve"})
            except urllib.error.URLError:
                pass
            continue
        if status in TERMINAL:
            rec["terminal_status"] = status
            break
    else:
        rec["terminal_status"] = "timeout"

    present = deliverable_counts(base, pid)
    rec["deliverables_final"] = present
    rec["expected_deliverables"] = scenario.get("expected_deliverables", {})
    rec["deliverable_completeness"] = completeness(rec["expected_deliverables"], present)
    rec["gates_reached"] = seen_gates
    rec["duration_s"] = round(time.time() - started, 1)

    # Correctness rubrics (not just presence). A scenario may request several
    # (e.g. hardware_v1 exercises both mechanical and electronics phases).
    rubrics = scenario.get("rubric")
    rubrics = [rubrics] if isinstance(rubrics, str) else (rubrics or [])
    goal = scenario.get("goal", "")
    for name in rubrics:
        if name == "mechanical":
            from mechanical_rubric import score_mechanical

            rec["mechanical_rubric"] = score_mechanical(base, pid, goal)
        elif name == "electronics":
            from electronics_rubric import score_electronics

            rec["electronics_rubric"] = score_electronics(base, pid, goal)
        elif name == "firmware":
            from firmware_rubric import score_firmware

            rec["firmware_rubric"] = score_firmware(base, pid, goal)
        elif name == "vv":
            from vv_rubric import score_vv

            rec["vv_rubric"] = score_vv(base, pid, goal)
        elif name == "manufacturing":
            from manufacturing_rubric import score_manufacturing

            rec["manufacturing_rubric"] = score_manufacturing(base, pid, goal)
        elif name == "requirements":
            from requirements_rubric import score_requirements

            rec["requirements_rubric"] = score_requirements(base, pid, goal)
        elif name == "architecture":
            from architecture_rubric import score_architecture

            rec["architecture_rubric"] = score_architecture(base, pid, goal)
        elif name == "consistency":
            from consistency_rubric import score_consistency

            rec["consistency_rubric"] = score_consistency(base, pid, goal)
    return rec


def summarize(records: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in records:
        by.setdefault(r["scenario"], []).append(r)
    summary = {}
    for sid, rs in by.items():
        n = len(rs)
        completed = sum(1 for r in rs if r["terminal_status"] == "completed")
        summary[sid] = {
            "runs": n,
            "completed": completed,
            "completed_rate": round(completed / n, 3) if n else 0,
            "avg_completeness": round(sum(r.get("deliverable_completeness", 0) for r in rs) / n, 3)
            if n
            else 0,
            "avg_duration_s": round(sum(r.get("duration_s", 0) for r in rs) / n, 1) if n else 0,
            "terminal_statuses": {
                s: sum(1 for r in rs if r["terminal_status"] == s)
                for s in sorted({r["terminal_status"] for r in rs})
            },
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Design-flow scenario eval runner")
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--scenario", default=None, help="run a single scenario id (default: all)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario (variance)")
    ap.add_argument("--cap-seconds", type=float, default=1200, help="per-run timeout")
    ap.add_argument("--out", default=os.path.join(HERE, "report.json"))
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenario)
    if not scenarios:
        print(f"no scenarios found (id={args.scenario})", file=sys.stderr)
        return 2

    print(
        f"eval: gateway={args.gateway}  scenarios={[s['id'] for s in scenarios]}  "
        f"repeat={args.repeat}",
        file=sys.stderr,
    )
    records = []
    for sc in scenarios:
        for i in range(1, args.repeat + 1):
            print(f"  ▶ {sc['id']} run {i}/{args.repeat} (flow={sc['flow']}) …", file=sys.stderr)
            rec = run_once(args.gateway, sc, i, args.cap_seconds)
            records.append(rec)
            print(
                f"    {rec['terminal_status']}  gates={rec.get('gates_reached', 0)}  "
                f"completeness={rec.get('deliverable_completeness', 0)}  "
                f"deliverables={rec.get('deliverables_final', {})}  "
                f"{rec.get('duration_s', 0)}s",
                file=sys.stderr,
            )
            for name in (
                "mechanical",
                "electronics",
                "firmware",
                "vv",
                "manufacturing",
                "requirements",
                "architecture",
                "consistency",
            ):
                rub = rec.get(f"{name}_rubric")
                if rub:
                    failed = [k for k, v in rub.get("checks", {}).items() if not v]
                    trace = rub.get("goal_traceable")
                    trace_s = "" if trace is None else f"  goal_traceable={trace}"
                    print(
                        f"    {name} rubric: {rub.get('score')}  "
                        f"failing={failed or 'none'}{trace_s}",
                        file=sys.stderr,
                    )

    report = {
        "gateway": args.gateway,
        "summary": summarize(records),
        "runs": records,
        "definitions_of_done": {s["id"]: s.get("definition_of_done", {}) for s in scenarios},
    }
    if os.path.dirname(args.out):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\n=== summary ===\n{json.dumps(report['summary'], indent=2)}", file=sys.stderr)
    print(f"report → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
