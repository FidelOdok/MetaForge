"""Unit tests for the `forge runs` CLI handlers (MET-548)."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from typing import Any

import pytest

from cli.forge_cli.client import ForgeClientError, ForgeClientNotFound
from cli.forge_cli.runs import handle_runs


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.raise_approval: Exception | None = None

    def create_run(self, request: dict | None = None, *, start: bool = True) -> dict:
        self.calls.append(("create", request, start))
        return {"id": "run_1", "status": "running" if start else "queued", "request": request or {}}

    def list_runs(self) -> dict:
        return {
            "runs": [{"id": "run_1", "status": "running", "created_at": 1.0, "updated_at": 2.0}]
        }

    def get_run(self, run_id: str) -> dict:
        if run_id == "missing":
            raise ForgeClientNotFound("No run with id 'missing'")
        return {"id": run_id, "status": "running"}

    def submit_run_approval(self, run_id: str, decision: str) -> dict:
        self.calls.append(("approval", run_id, decision))
        if self.raise_approval:
            raise self.raise_approval
        return {"id": run_id, "status": "running" if decision == "approve" else "rejected"}

    def stream_run_events(self, run_id: str) -> Iterator[dict]:
        yield {"status": "running", "error": None, "approval_reason": None}
        yield {"status": "completed", "error": None, "approval_reason": None}


def _args(**kw: Any) -> argparse.Namespace:
    base = {"json": False, "goal": None, "request_json": None, "no_start": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_create_calls_client_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    handle_runs(_args(runs_command="create", goal="build a widget"), client)  # type: ignore[arg-type]
    assert client.calls[0] == ("create", {"goal": "build a widget"}, True)
    assert "run_1" in capsys.readouterr().out


def test_create_no_start(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    handle_runs(_args(runs_command="create", no_start=True), client)  # type: ignore[arg-type]
    assert client.calls[0] == ("create", {}, False)


def test_list_table(capsys: pytest.CaptureFixture[str]) -> None:
    handle_runs(_args(runs_command="list"), FakeClient())  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "run_1" in out and "running" in out


def test_get(capsys: pytest.CaptureFixture[str]) -> None:
    handle_runs(_args(runs_command="get", run_id="run_1"), FakeClient())  # type: ignore[arg-type]
    assert "run_1" in capsys.readouterr().out


def test_get_missing(capsys: pytest.CaptureFixture[str]) -> None:
    handle_runs(_args(runs_command="get", run_id="missing"), FakeClient())  # type: ignore[arg-type]
    assert "No run with id" in capsys.readouterr().err


def test_approve(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    handle_runs(_args(runs_command="approve", run_id="r1"), client)  # type: ignore[arg-type]
    assert client.calls[0] == ("approval", "r1", "approve")
    assert "r1 -> running" in capsys.readouterr().out


def test_reject(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    handle_runs(_args(runs_command="reject", run_id="r1"), client)  # type: ignore[arg-type]
    assert client.calls[0] == ("approval", "r1", "reject")
    assert "r1 -> rejected" in capsys.readouterr().out


def test_approve_conflict_surfaces_error(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    client.raise_approval = ForgeClientError("409 run is running")
    handle_runs(_args(runs_command="approve", run_id="r1"), client)  # type: ignore[arg-type]
    assert "could not approve" in capsys.readouterr().err


def test_watch_streams_events(capsys: pytest.CaptureFixture[str]) -> None:
    handle_runs(_args(runs_command="watch", run_id="r1"), FakeClient())  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "running" in out and "completed" in out
