"""Unit tests for the run lifecycle + approval state machine (MET-547)."""

from __future__ import annotations

import pytest

from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunNotFoundError,
    RunStatus,
)


def _store() -> InMemoryRunStore:
    ticks = iter(range(1, 10_000))
    return InMemoryRunStore(clock=lambda: float(next(ticks)))


def test_create_starts_queued() -> None:
    store = _store()
    run = store.create({"goal": "build a widget"})
    assert run.status is RunStatus.QUEUED
    assert run.id.startswith("run_")
    assert run.request == {"goal": "build a widget"}
    assert run.history == [RunStatus.QUEUED]
    assert not run.is_terminal


def test_get_unknown_raises() -> None:
    with pytest.raises(RunNotFoundError):
        _store().get("run_missing")


def test_duplicate_id_raises() -> None:
    store = _store()
    store.create({}, run_id="run_x")
    with pytest.raises(ValueError, match="already exists"):
        store.create({}, run_id="run_x")


def test_happy_path_to_completed() -> None:
    store = _store()
    store.create({}, run_id="r1")
    store.start("r1")
    run = store.complete("r1", result={"artifacts": ["a.py"]})
    assert run.status is RunStatus.COMPLETED
    assert run.result == {"artifacts": ["a.py"]}
    assert run.is_terminal
    assert run.history == [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPLETED]


def test_approval_approve_resumes_running() -> None:
    store = _store()
    store.create({}, run_id="r1")
    store.start("r1")
    store.request_approval("r1", reason="about to run a destructive tool")
    assert store.get("r1").status is RunStatus.AWAITING_APPROVAL
    assert store.get("r1").approval_reason == "about to run a destructive tool"
    run = store.submit_approval("r1", ApprovalDecision.APPROVE)
    assert run.status is RunStatus.RUNNING
    assert run.approval_reason is None


def test_approval_reject_is_terminal() -> None:
    store = _store()
    store.create({}, run_id="r1")
    store.start("r1")
    store.request_approval("r1")
    run = store.submit_approval("r1", ApprovalDecision.REJECT)
    assert run.status is RunStatus.REJECTED
    assert run.is_terminal


def test_illegal_transition_raises() -> None:
    store = _store()
    store.create({}, run_id="r1")
    # queued -> completed is not allowed (must go through running)
    with pytest.raises(InvalidTransition):
        store.complete("r1")


def test_cannot_transition_from_terminal() -> None:
    store = _store()
    store.create({}, run_id="r1")
    store.start("r1")
    store.fail("r1", error="boom")
    with pytest.raises(InvalidTransition):
        store.start("r1")


def test_submit_approval_requires_awaiting_state() -> None:
    store = _store()
    store.create({}, run_id="r1")
    store.start("r1")
    with pytest.raises(InvalidTransition):
        store.submit_approval("r1", ApprovalDecision.APPROVE)


def test_timestamps_advance_on_transition() -> None:
    store = _store()
    run = store.create({}, run_id="r1")
    created = run.updated_at
    store.start("r1")
    assert store.get("r1").updated_at > created


def test_list_returns_all_runs() -> None:
    store = _store()
    store.create({}, run_id="a")
    store.create({}, run_id="b")
    assert {r.id for r in store.list()} == {"a", "b"}
