"""Unit tests for MET-572 dataset promotion + per-variant trends. Network-free.

Includes the fixture regression corpus: every promoted session fixture under
``evals/fixtures/sessions/`` is replayed through the CURRENT chat_tooluse
evaluator and must reproduce the verdict frozen at promotion time — an
evaluator change that silently flips a real production trajectory fails here.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_tooluse_rubric import evaluate_chat_tooluse  # noqa: E402
from promote_sessions import (  # noqa: E402
    FIXTURE_DIR,
    build_fixture,
    fixture_path,
    promote,
    promotion_reasons,
)
from trend import variant_headlines  # noqa: E402


def _row(checks: dict | None = None, n_actions: int = 3, n_errored: int = 0) -> dict:
    return {
        "session_id": "s-1",
        "agent_code": "claude-code",
        "project_id": None,
        "checks": checks
        or {
            "no_duplicate_identical_calls": True,
            "no_repeated_failing_call": True,
            "tool_errors_bounded": True,
        },
        "n_actions": n_actions,
        "n_errored": n_errored,
        "score": 1.0,
    }


# --- promotion selection ---------------------------------------------------------
def test_clean_short_session_is_not_promotable() -> None:
    assert promotion_reasons(_row()) == []


def test_failing_check_is_promotable() -> None:
    reasons = promotion_reasons(_row(checks={"no_duplicate_identical_calls": False}))
    assert any("failing checks" in r for r in reasons)


def test_errored_actions_are_promotable() -> None:
    reasons = promotion_reasons(_row(n_errored=2))
    assert any("errored actions: 2" in r for r in reasons)


def test_long_trajectory_is_promotable_at_threshold() -> None:
    assert promotion_reasons(_row(n_actions=15))
    assert not promotion_reasons(_row(n_actions=14))


def test_fixture_path_sanitizes_ids() -> None:
    path = fixture_path("ab/../c d", out_dir="/x")
    assert path == "/x/ab____c_d.json"


def test_build_fixture_is_replayable() -> None:
    session = {
        "id": "s-1",
        "events": [
            {"type": "action", "data": {"tool_id": "twin.get_node", "status": "ok", "args": {}}},
            {"type": "action", "data": {"tool_id": "twin.get_node", "status": "ok", "args": {}}},
        ],
    }
    row = _row(checks={"no_duplicate_identical_calls": False})
    fx = build_fixture(session, row, ["failing checks: x"], "http://gw", "2026-08-05T00:00:00Z")
    # The frozen turn must reproduce the frozen verdict through the live evaluator.
    checks = evaluate_chat_tooluse([fx["turn"]])
    assert checks["no_duplicate_identical_calls"] is False


def test_promote_is_idempotent(tmp_path: Path) -> None:
    session = {
        "id": "s-dup",
        "agent_code": "a",
        "events": [
            {"type": "action", "data": {"tool_id": "t", "status": "error", "args": {}}},
        ],
    }
    first = promote([session], gateway="gw", out_dir=str(tmp_path))
    second = promote([session], gateway="gw", out_dir=str(tmp_path))
    assert len(first) == 1 and second == []
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_promote_dry_run_writes_nothing(tmp_path: Path) -> None:
    session = {
        "id": "s-dry",
        "events": [{"type": "action", "data": {"tool_id": "t", "status": "error", "args": {}}}],
    }
    out = promote([session], gateway="gw", out_dir=str(tmp_path), dry_run=True)
    assert len(out) == 1
    assert list(tmp_path.glob("*.json")) == []


# --- fixture regression corpus ------------------------------------------------------
def test_promoted_fixtures_reproduce_their_frozen_verdicts() -> None:
    """Replay every checked-in fixture through the CURRENT evaluator."""
    paths = sorted(glob.glob(str(Path(FIXTURE_DIR) / "*.json")))
    if not paths:
        return  # corpus is optional until first promotion lands
    for path in paths:
        fx = json.load(open(path, encoding="utf-8"))
        checks = evaluate_chat_tooluse([fx["turn"]])
        expected = fx["expected_checks"]
        relevant = {k: v for k, v in checks.items() if k in expected}
        assert relevant == expected, f"evaluator drift on {Path(path).name}"


# --- per-variant trend lines ---------------------------------------------------------
def test_variant_headlines_group_chat_rows() -> None:
    report = {
        "suite": "chat_context_v1",
        "summary": {
            "chat_a": {
                "native": {"passed": 5, "failed_unexpected": 1},
                "react": {"passed": 3, "failed_unexpected": 0},
            },
            "chat_b": {"native": {"passed": 2, "failed_unexpected": 0}},
        },
    }
    out = variant_headlines(report)
    assert out["native"] == {
        "scenarios": 2,
        "passed": 7,
        "failed_unexpected": 1,
        "xfail_confirmed": 0,
        "xpass_improved": 0,
    }
    assert out["react"]["scenarios"] == 1 and out["react"]["passed"] == 3


def test_variant_headlines_empty_for_runs_suite() -> None:
    assert variant_headlines({"summary": {"l0": {"completed_rate": 1.0}}}) == {}
