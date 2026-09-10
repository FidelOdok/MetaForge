"""Unit tests for the chat project-brief rubric (MET-570). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_brief_rubric import evaluate_chat_brief  # noqa: E402

_PID = "proj-abc-123"
_PNAME = "eval-chat_brief_project-native-1-99"


def _turn(reply: str = "", steps: list | None = None) -> dict:
    return {"reply": reply, "steps": steps or [], "checks": [], "reply_error": False}


def _decision(project_id: str | None) -> dict:
    args: dict = {"title": "t"}
    if project_id is not None:
        args["project_id"] = project_id
    return {"tool": "mcp_twin_record_decision", "arguments": args}


def test_scoped_decision_and_named_project_pass() -> None:
    turns = [
        _turn(steps=[_decision(_PID)]),
        _turn(reply=f"We are in project {_PNAME} ({_PID})."),
    ]
    checks = evaluate_chat_brief(turns, _PID, _PNAME)
    assert checks == {
        "project_name_known": True,
        "decision_scoped_to_project": True,
        "no_foreign_project_id": True,
    }


def test_unscoped_decision_fails_scoping() -> None:
    turns = [_turn(steps=[_decision(None)])]
    checks = evaluate_chat_brief(turns, _PID, _PNAME)
    assert not checks["decision_scoped_to_project"]
    # Omitting project_id is unscoped, not foreign.
    assert checks["no_foreign_project_id"]


def test_foreign_project_id_flags_hallucination() -> None:
    turns = [_turn(steps=[_decision("some-other-project")])]
    checks = evaluate_chat_brief(turns, _PID, _PNAME)
    assert not checks["no_foreign_project_id"]
    assert not checks["decision_scoped_to_project"]


def test_project_name_unknown_when_never_mentioned() -> None:
    turns = [_turn(reply="I do not know which project this is.")]
    assert not evaluate_chat_brief(turns, _PID, _PNAME)["project_name_known"]


def test_commit_geometry_counts_as_twin_write() -> None:
    step = {"tool": "mcp_twin_commit_geometry", "arguments": {"project_id": _PID}}
    checks = evaluate_chat_brief([_turn(steps=[step])], _PID, _PNAME)
    assert checks["decision_scoped_to_project"]
