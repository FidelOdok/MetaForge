#!/usr/bin/env python3
"""Online eval: score captured agent sessions from production traces (MET-572).

Session capture (MET-492/496) records every MCP tool call into
``/v1/sessions`` — but the corpus was write-only: nothing scored it. This
scorer replays the MET-570 tool-trajectory rubric (``chat_tooluse``) over
those *production* traces, so the same discipline checks that gate scripted
eval scenarios (no duplicate identical calls, no identical retry of a failing
call, bounded error rate) also run over what agents actually did.

Each captured session becomes one synthetic "turn" whose steps are its
``action`` events (``data: {tool_id, status, args, ...}``). Sessions without
action events are skipped — there is no trajectory to judge.

    python3 evals/score_sessions.py --gateway http://fidel-dev:8000
    python3 evals/score_sessions.py --project <id> --since 2026-08-01 --strict

This is the first MET-572 slice (trajectory scoring). Dataset promotion
(captured failures → eval fixtures) and per-model trend lines remain on the
issue. Stdlib only, plus the sibling rubric module.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from chat_rubric_common import score  # noqa: E402
from chat_tooluse_rubric import evaluate_chat_tooluse  # noqa: E402


# --- pure helpers (unit-tested without network) --------------------------------
def session_to_turn(session: dict[str, Any]) -> dict[str, Any]:
    """Map a captured session's action events into a rubric turn record."""
    steps: list[dict[str, Any]] = []
    for event in session.get("events") or []:
        if event.get("type") != "action":
            continue
        data = event.get("data") or {}
        status = str(data.get("status", "ok"))
        steps.append(
            {
                "tool": str(data.get("tool_id") or event.get("message") or "?"),
                "arguments": data.get("args") or {},
                "error": None if status == "ok" else f"status={status}",
            }
        )
    return {
        "steps": steps,
        # Trajectory-only scoring: no conversational reply exists for a
        # captured session, so reply-based checks stay vacuous.
        "reply": session.get("summary") or "(captured session)",
        "reply_error": False,
        "tag": None,
        "checks": [],
    }


def score_session(session: dict[str, Any]) -> dict[str, Any] | None:
    """Score one captured session's trajectory; None when it has no actions."""
    turn = session_to_turn(session)
    if not turn["steps"]:
        return None
    checks = evaluate_chat_tooluse([turn])
    # Reply-based checks are meaningless for captured traces — drop them.
    checks.pop("big_observation_survived", None)
    errored = sum(1 for s in turn["steps"] if s.get("error"))
    return {
        "session_id": session.get("id"),
        "agent_code": session.get("agent_code"),
        "status": session.get("status"),
        "project_id": session.get("project_id"),
        "n_actions": len(turn["steps"]),
        "n_errored": errored,
        "checks": checks,
        "score": score(checks),
    }


def summarize_sessions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-session trajectory scores for the report header."""
    n = len(rows)
    failing = [r["session_id"] for r in rows if not all(r["checks"].values())]
    return {
        "sessions_scored": n,
        "avg_score": round(sum(r["score"] for r in rows) / n, 3) if n else None,
        "total_actions": sum(r["n_actions"] for r in rows),
        "total_errored": sum(r["n_errored"] for r in rows),
        "sessions_with_failing_checks": failing,
    }


# --- gateway plumbing --------------------------------------------------------------
def fetch_sessions(base: str, project_id: str | None) -> list[dict[str, Any]]:
    url = base.rstrip("/") + "/v1/sessions"
    if project_id:
        url += f"?project_id={project_id}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
            payload = json.loads(r.read() or "{}")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"score_sessions: fetch failed: {exc}", file=sys.stderr)
        return []
    return list(payload.get("sessions") or [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Score captured agent sessions (online eval)")
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--project", default=None, help="scope to one project id")
    ap.add_argument("--since", default=None, help="ISO date floor on started_at (e.g. 2026-08-01)")
    ap.add_argument("--limit", type=int, default=100, help="max sessions to score (newest first)")
    ap.add_argument("--out", default=os.path.join(HERE, "session_scores.json"))
    ap.add_argument(
        "--strict", action="store_true", help="exit 1 when any session fails a trajectory check"
    )
    args = ap.parse_args()

    sessions = fetch_sessions(args.gateway, args.project)
    if args.since:
        sessions = [s for s in sessions if str(s.get("started_at", "")) >= args.since]
    sessions = sessions[: args.limit]

    rows = [row for s in sessions if (row := score_session(s))]
    summary = summarize_sessions(rows)

    report = {"gateway": args.gateway, "summary": summary, "sessions": rows}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print(
        f"scored {summary['sessions_scored']} sessions "
        f"({len(sessions)} fetched)  avg={summary['avg_score']}  "
        f"actions={summary['total_actions']}  errored={summary['total_errored']}",
        file=sys.stderr,
    )
    for row in rows:
        failing = [k for k, v in row["checks"].items() if not v]
        if failing:
            print(
                f"  ▼ {row['session_id']} [{row['agent_code']}] failing={failing}", file=sys.stderr
            )
    print(f"report → {args.out}", file=sys.stderr)

    if args.strict and summary["sessions_with_failing_checks"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
