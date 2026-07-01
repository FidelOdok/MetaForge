"""Unit tests for the session write-lock (MET-547, Phase 3)."""

from __future__ import annotations

import pytest

from orchestrator.harness.locks import SessionLockedError, SessionWriteLock


def test_acquire_marks_held() -> None:
    lock = SessionWriteLock()
    lock.acquire("s1", "agent-a")
    assert lock.is_held("s1")
    assert lock.holder("s1") == "agent-a"


def test_different_holder_is_rejected() -> None:
    lock = SessionWriteLock()
    lock.acquire("s1", "agent-a")
    with pytest.raises(SessionLockedError) as exc:
        lock.acquire("s1", "agent-b")
    assert exc.value.holder == "agent-a"


def test_release_frees_the_session() -> None:
    lock = SessionWriteLock()
    token = lock.acquire("s1", "agent-a")
    lock.release(token)
    assert not lock.is_held("s1")
    # Now another holder can take it.
    lock.acquire("s1", "agent-b")
    assert lock.holder("s1") == "agent-b"


def test_reentrant_same_holder() -> None:
    lock = SessionWriteLock()
    t1 = lock.acquire("s1", "agent-a")
    t2 = lock.acquire("s1", "agent-a")  # re-entrant, count 2
    lock.release(t2)
    assert lock.is_held("s1")  # still held after one release
    lock.release(t1)
    assert not lock.is_held("s1")


def test_release_by_wrong_holder_raises() -> None:
    lock = SessionWriteLock()
    lock.acquire("s1", "agent-a")
    from orchestrator.harness.locks import LockToken

    with pytest.raises(SessionLockedError):
        lock.release(LockToken(session_id="s1", holder="agent-b"))


def test_hold_context_manager_releases() -> None:
    lock = SessionWriteLock()
    with lock.hold("s1", "agent-a"):
        assert lock.holder("s1") == "agent-a"
    assert not lock.is_held("s1")


def test_hold_contended_raises() -> None:
    lock = SessionWriteLock()
    with lock.hold("s1", "agent-a"):
        with pytest.raises(SessionLockedError):
            with lock.hold("s1", "agent-b"):
                pass


def test_unheld_session_reports_no_holder() -> None:
    lock = SessionWriteLock()
    assert lock.holder("nope") is None
    assert not lock.is_held("nope")
