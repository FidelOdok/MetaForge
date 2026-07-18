"""Unit tests for `forge routine` scheduled runs (MET-563)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.forge_cli.routines import (
    Routine,
    RoutineError,
    RoutineStore,
    parse_interval,
    run_due,
)


class StubClient:
    """Duck-typed ForgeClient for routine execution."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def create_thread(self, scope_kind: str, scope_entity_id: str, **_: Any) -> dict[str, Any]:
        assert scope_kind == "assistant"
        return {"id": f"t-{scope_entity_id}"}

    def send_message(self, thread_id: str, content: str, **_: Any) -> dict[str, Any]:
        self.sent.append(content)
        return {"id": "u-1"}


def test_parse_interval_units() -> None:
    assert parse_interval("45s") == 45
    assert parse_interval("10m") == 600
    assert parse_interval("2h") == 7200
    assert parse_interval("1d") == 86400


def test_parse_interval_rejects_garbage() -> None:
    with pytest.raises(RoutineError):
        parse_interval("soon")


def test_is_due_never_run_is_due() -> None:
    r = Routine(id="a", prompt="p", interval_seconds=60)
    assert r.is_due(now=1000.0) is True


def test_is_due_respects_interval() -> None:
    r = Routine(id="a", prompt="p", interval_seconds=60, last_run=1000.0)
    assert r.is_due(now=1030.0) is False  # only 30s elapsed
    assert r.is_due(now=1060.0) is True  # exactly 60s elapsed


def test_is_due_disabled_never_due() -> None:
    r = Routine(id="a", prompt="p", interval_seconds=1, last_run=None, enabled=False)
    assert r.is_due(now=9999.0) is False


def test_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "routines.json"
    store = RoutineStore(path=path)
    store.add(Routine(id="a", prompt="hello", interval_seconds=600))
    store.save()
    reloaded = RoutineStore.load(path)
    assert len(reloaded.routines) == 1
    assert reloaded.routines[0].prompt == "hello"


def test_store_load_missing_is_empty(tmp_path: Path) -> None:
    assert RoutineStore.load(tmp_path / "absent.json").routines == []


def test_store_remove(tmp_path: Path) -> None:
    store = RoutineStore(path=tmp_path / "r.json")
    store.add(Routine(id="a", prompt="p", interval_seconds=1))
    assert store.remove("a") is True
    assert store.remove("missing") is False


def test_run_due_fires_only_due_and_stamps(tmp_path: Path) -> None:
    store = RoutineStore(path=tmp_path / "r.json")
    store.add(Routine(id="due", prompt="fire me", interval_seconds=60, last_run=None))
    store.add(Routine(id="notdue", prompt="wait", interval_seconds=60, last_run=1000.0))
    client = StubClient()

    fired = run_due(client, store, now=1030.0)  # type: ignore[arg-type]

    assert fired == 1
    assert client.sent == ["fire me"]
    due = next(r for r in store.routines if r.id == "due")
    assert due.last_run == 1030.0  # stamped
    # persisted
    assert any(r.last_run == 1030.0 for r in RoutineStore.load(store.path).routines)


def test_run_due_nothing_due_returns_zero(tmp_path: Path) -> None:
    store = RoutineStore(path=tmp_path / "r.json")
    store.add(Routine(id="x", prompt="p", interval_seconds=3600, last_run=1000.0))
    client = StubClient()
    assert run_due(client, store, now=1100.0) == 0  # type: ignore[arg-type]
    assert client.sent == []
