#!/usr/bin/env python3
"""Eval report diff + trend tool (MET-574).

Reports were write-only: nothing compared two runs, so a regression (or the
xfail→xpass flip that proves a fix landed) had to be eyeballed out of raw
JSON. This tool reads reports from **either suite** (design-flow runs or chat
context) and:

- ``diff``  — compares two reports scenario-by-scenario, prints deltas,
  improvements (new passes, xfail→xpass flips, completeness gains) and
  regressions (new unexpected failures, completed-rate/completeness drops,
  judge-score drops). ``--strict`` exits 1 on any regression, which is the
  threshold gate for nightly runs and future CI.
- ``history`` — walks a directory of persisted reports (as written by
  ``nightly.sh``) oldest-first and prints one headline row per report, so
  drift is visible over time.

    python3 evals/trend.py diff --before evals/reports/A/report_chat.json \
                                --after  evals/reports/B/report_chat.json --strict
    python3 evals/trend.py history --dir evals/reports

Stdlib only; pure functions are unit-tested in tests/unit/test_eval_trend.py.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any

# Score drops below this are treated as judge noise, not regressions.
_JUDGE_EPSILON = 0.05


def suite_of(report: dict[str, Any]) -> str:
    """ "chat" (run_chat_scenarios.py) or "runs" (run_scenarios.py)."""
    return "chat" if report.get("suite") == "chat_context_v1" else "runs"


def extract_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten a report's summary into comparable rows keyed by scenario
    (runs suite) or ``scenario::variant`` (chat suite)."""
    summary = report.get("summary") or {}
    rows: dict[str, dict[str, Any]] = {}
    if suite_of(report) == "chat":
        for sid, variants in summary.items():
            if not isinstance(variants, dict):
                continue
            for vname, stats in variants.items():
                if isinstance(stats, dict):
                    rows[f"{sid}::{vname}"] = dict(stats)
    else:
        # Skip scalar summary entries so foreign report shapes (e.g. the
        # session-scores report, whose summary holds aggregates) don't crash
        # a directory-wide history walk.
        for sid, stats in summary.items():
            if isinstance(stats, dict):
                rows[sid] = dict(stats)
    return rows


def headline(report: dict[str, Any]) -> dict[str, Any]:
    """One-line aggregate for a report (the ``history`` row)."""
    rows = extract_rows(report)
    out: dict[str, Any] = {"suite": suite_of(report), "scenarios": len(rows)}
    if suite_of(report) == "chat":
        for key in ("passed", "failed_unexpected", "xfail_confirmed", "xpass_improved"):
            out[key] = sum(int(r.get(key, 0)) for r in rows.values())
    else:
        n = len(rows) or 1
        out["completed_rate"] = round(
            sum(float(r.get("completed_rate", 0)) for r in rows.values()) / n, 3
        )
        out["avg_completeness"] = round(
            sum(float(r.get("avg_completeness", 0)) for r in rows.values()) / n, 3
        )
    judge = report.get("judge") or {}
    if judge.get("avg_overall_score") is not None:
        out["judge_avg"] = judge["avg_overall_score"]
    return out


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two reports of the same suite into improvements and regressions.

    Regression rules (deterministic, documented):
    - chat:  ``failed_unexpected`` increased (or appeared on a new row), or
             ``xpass_improved`` fell while ``xfail_confirmed`` rose (a fix
             reverted).
    - runs:  ``completed_rate`` or ``avg_completeness`` decreased.
    - both:  report-level judge ``avg_overall_score`` dropped by more than
             0.05 (advisory scores get an epsilon).
    """
    if suite_of(before) != suite_of(after):
        raise ValueError(
            f"cannot diff a {suite_of(before)} report against a {suite_of(after)} report"
        )
    suite = suite_of(after)
    b_rows, a_rows = extract_rows(before), extract_rows(after)
    improvements: list[str] = []
    regressions: list[str] = []

    for key, a in sorted(a_rows.items()):
        b = b_rows.get(key)
        if suite == "chat":
            b_fail = int(b.get("failed_unexpected", 0)) if b else 0
            a_fail = int(a.get("failed_unexpected", 0))
            if a_fail > b_fail:
                regressions.append(f"{key}: failed_unexpected {b_fail} → {a_fail}")
            elif b and a_fail < b_fail:
                improvements.append(f"{key}: failed_unexpected {b_fail} → {a_fail}")
            if b:
                b_xp, a_xp = int(b.get("xpass_improved", 0)), int(a.get("xpass_improved", 0))
                b_xf, a_xf = int(b.get("xfail_confirmed", 0)), int(a.get("xfail_confirmed", 0))
                if a_xp > b_xp:
                    improvements.append(f"{key}: xpass_improved {b_xp} → {a_xp} (fix landed)")
                elif a_xp < b_xp and a_xf > b_xf:
                    regressions.append(f"{key}: xpass reverted to xfail ({b_xp} → {a_xp})")
        else:
            if not b:
                continue
            for metric in ("completed_rate", "avg_completeness"):
                b_v, a_v = float(b.get(metric, 0)), float(a.get(metric, 0))
                if a_v < b_v:
                    regressions.append(f"{key}: {metric} {b_v} → {a_v}")
                elif a_v > b_v:
                    improvements.append(f"{key}: {metric} {b_v} → {a_v}")

    b_judge = (before.get("judge") or {}).get("avg_overall_score")
    a_judge = (after.get("judge") or {}).get("avg_overall_score")
    if b_judge is not None and a_judge is not None:
        if a_judge < b_judge - _JUDGE_EPSILON:
            regressions.append(f"judge: avg_overall_score {b_judge} → {a_judge}")
        elif a_judge > b_judge + _JUDGE_EPSILON:
            improvements.append(f"judge: avg_overall_score {b_judge} → {a_judge}")

    return {
        "suite": suite,
        "improvements": improvements,
        "regressions": regressions,
        "before": headline(before),
        "after": headline(after),
    }


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fmt_headline(h: dict[str, Any]) -> str:
    return "  ".join(f"{k}={v}" for k, v in h.items())


def cmd_diff(args: argparse.Namespace) -> int:
    result = compare(_load(args.before), _load(args.after))
    print(f"suite: {result['suite']}")
    print(f"before: {_fmt_headline(result['before'])}")
    print(f"after:  {_fmt_headline(result['after'])}")
    for line in result["improvements"]:
        print(f"  ▲ {line}")
    for line in result["regressions"]:
        print(f"  ▼ {line}")
    if not result["improvements"] and not result["regressions"]:
        print("  = no change")
    if args.strict and result["regressions"]:
        print(f"strict: {len(result['regressions'])} regression(s)", file=sys.stderr)
        return 1
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    paths = sorted(glob.glob(os.path.join(args.dir, "**", "*.json"), recursive=True))
    if not paths:
        print(f"no reports under {args.dir}", file=sys.stderr)
        return 2
    for path in paths:
        try:
            report = _load(path)
        except (OSError, json.JSONDecodeError):
            continue
        if "summary" not in report:
            continue  # not an eval report (e.g. a judge-only artifact)
        rel = os.path.relpath(path, args.dir)
        print(f"{rel}: {_fmt_headline(headline(report))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval report diff + trend tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="compare two reports of the same suite")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--strict", action="store_true", help="exit 1 on any regression")
    d.set_defaults(fn=cmd_diff)

    h = sub.add_parser("history", help="headline rows for every report under a directory")
    h.add_argument(
        "--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    )
    h.set_defaults(fn=cmd_history)

    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
