"""Unit tests for the SQLite session ledger (MET-547, Phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import InMemoryRunStore


@pytest.fixture
def ledger() -> SqliteRunLedger:
    return SqliteRunLedger(":memory:", clock=lambda: 1.0)


def _run(run_id: str = "r1"):
    store = InMemoryRunStore(clock=lambda: 5.0)
    return store.create({"goal": "build enclosure"}, run_id=run_id)


def test_record_and_get_run(ledger: SqliteRunLedger) -> None:
    ledger.record_run(_run("r1"))
    got = ledger.get_run("r1")
    assert got is not None
    assert got["status"] == "queued"
    assert got["request"] == {"goal": "build enclosure"}
    assert got["result"] is None


def test_get_unknown_run_is_none(ledger: SqliteRunLedger) -> None:
    assert ledger.get_run("nope") is None


def test_record_run_upserts(ledger: SqliteRunLedger) -> None:
    store = InMemoryRunStore(clock=lambda: 5.0)
    run = store.create({}, run_id="r1")
    ledger.record_run(run)
    store.start("r1")
    store.complete("r1", result={"ok": True})
    ledger.record_run(run)  # same id, new state
    got = ledger.get_run("r1")
    assert got["status"] == "completed"
    assert got["result"] == {"ok": True}


def test_record_and_list_events(ledger: SqliteRunLedger) -> None:
    ledger.record_event("r1", "thought", "considering the enclosure walls")
    ledger.record_event("r1", "action", "called mcp_freecad_generate_enclosure")
    ledger.record_event("r2", "thought", "unrelated")
    events = ledger.events("r1")
    assert [e.kind for e in events] == ["thought", "action"]
    assert events[0].detail.startswith("considering")


def test_search_finds_event(ledger: SqliteRunLedger) -> None:
    ledger.record_event("r1", "action", "called mcp_freecad_generate_enclosure")
    ledger.record_event("r1", "thought", "budget is tight")
    hits = ledger.search("enclosure")
    assert len(hits) == 1
    assert hits[0].run_id == "r1"
    assert "enclosure" in hits[0].detail


def test_search_no_match(ledger: SqliteRunLedger) -> None:
    ledger.record_event("r1", "thought", "nothing relevant")
    assert ledger.search("nonexistentterm") == []


def test_persists_across_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "ledger.db")
    first = SqliteRunLedger(db, clock=lambda: 1.0)
    first.record_run(_run("r1"))
    first.record_event("r1", "action", "did a thing")
    first.close()

    reopened = SqliteRunLedger(db, clock=lambda: 2.0)
    assert reopened.get_run("r1") is not None
    assert len(reopened.events("r1")) == 1
    reopened.close()
