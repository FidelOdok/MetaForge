#!/usr/bin/env python3
"""Promote interesting captured sessions into replayable eval fixtures (MET-572).

``score_sessions.py`` grades production traces but the findings were
ephemeral — a flagged trajectory vanished when the session store was cleaned.
This is the dataset-from-production-traces half: sessions worth keeping
(failing a trajectory check, containing errored actions, or unusually long)
are frozen as self-contained fixtures under ``evals/fixtures/sessions/``, each
carrying the rubric turn record plus the check outcomes at promotion time.

The fixtures serve as a rubric regression corpus:
``tests/unit/test_session_fixtures.py`` replays every fixture through the
current ``evaluate_chat_tooluse`` and fails when an evaluator change silently
flips a real production trajectory's verdict.

    python3 evals/promote_sessions.py --gateway http://fidel-dev:8000
    python3 evals/promote_sessions.py --scores evals/session_scores.json --dry-run

Promotion is idempotent: a session's fixture filename is its id, existing
files are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from score_sessions import fetch_sessions, score_session, session_to_turn  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "sessions")

# A trajectory with this many actions is "interesting" even when clean — long
# runs are where discipline drifts, and they make good regression material.
DEFAULT_MIN_ACTIONS = 15


def promotion_reasons(row: dict[str, Any], min_actions: int = DEFAULT_MIN_ACTIONS) -> list[str]:
    """Why a scored session is worth freezing (empty list = not promotable)."""
    reasons: list[str] = []
    failing = sorted(k for k, v in (row.get("checks") or {}).items() if not v)
    if failing:
        reasons.append(f"failing checks: {', '.join(failing)}")
    if int(row.get("n_errored", 0)) > 0:
        reasons.append(f"errored actions: {row['n_errored']}")
    if int(row.get("n_actions", 0)) >= min_actions:
        reasons.append(f"long trajectory: {row['n_actions']} actions")
    return reasons


def build_fixture(
    session: dict[str, Any],
    row: dict[str, Any],
    reasons: list[str],
    gateway: str,
    promoted_at: str,
) -> dict[str, Any]:
    """A self-contained, replayable fixture for one promoted session."""
    return {
        "session_id": row.get("session_id"),
        "agent_code": row.get("agent_code"),
        "project_id": row.get("project_id"),
        "source_gateway": gateway,
        "promoted_at": promoted_at,
        "reasons": reasons,
        # The rubric input, frozen — replay needs no gateway.
        "turn": session_to_turn(session),
        # The verdict at promotion time — the regression baseline.
        "expected_checks": row.get("checks") or {},
        "score": row.get("score"),
    }


def fixture_path(session_id: Any, out_dir: str = FIXTURE_DIR) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return os.path.join(out_dir, f"{safe}.json")


def promote(
    sessions: list[dict[str, Any]],
    *,
    gateway: str,
    out_dir: str = FIXTURE_DIR,
    min_actions: int = DEFAULT_MIN_ACTIONS,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Score, select, and freeze promotable sessions. Returns written fixtures."""
    promoted_at = datetime.now(UTC).isoformat(timespec="seconds")
    written: list[dict[str, Any]] = []
    for session in sessions:
        row = score_session(session)
        if row is None:
            continue
        reasons = promotion_reasons(row, min_actions=min_actions)
        if not reasons:
            continue
        path = fixture_path(row.get("session_id"), out_dir)
        if os.path.exists(path):
            continue  # idempotent — a promoted session stays frozen as-is
        fixture = build_fixture(session, row, reasons, gateway, promoted_at)
        if not dry_run:
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(fixture, fh, indent=2)
        written.append(fixture)
        print(
            f"  ★ {row.get('session_id')} ({row.get('agent_code')}) — {'; '.join(reasons)}"
            + (" [dry-run]" if dry_run else f" → {os.path.relpath(path, HERE)}"),
            file=sys.stderr,
        )
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote captured sessions into eval fixtures")
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--project", default=None, help="only sessions for this project id")
    ap.add_argument("--limit", type=int, default=200, help="max sessions to consider")
    ap.add_argument(
        "--min-actions",
        type=int,
        default=DEFAULT_MIN_ACTIONS,
        help="promote clean sessions at/above this many actions (long-trajectory corpus)",
    )
    ap.add_argument("--out-dir", default=FIXTURE_DIR)
    ap.add_argument("--dry-run", action="store_true", help="select and report, write nothing")
    args = ap.parse_args()

    sessions = fetch_sessions(args.gateway, args.project)[: args.limit]
    if not sessions:
        print("promote: no sessions fetched", file=sys.stderr)
        return 2
    written = promote(
        sessions,
        gateway=args.gateway,
        out_dir=args.out_dir,
        min_actions=args.min_actions,
        dry_run=args.dry_run,
    )
    print(
        f"promote: {len(written)} fixture(s) {'selected' if args.dry_run else 'written'} "
        f"from {len(sessions)} sessions → {os.path.relpath(args.out_dir, HERE)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
