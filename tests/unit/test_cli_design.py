"""`forge design` CLI command — friendly wrapper over runs create (MET-10)."""

from __future__ import annotations

import argparse
from typing import Any

from cli.forge_cli.main import build_parser
from cli.forge_cli.runs import handle_design


class _Client:
    def __init__(self) -> None:
        self.created: dict[str, Any] = {}
        self.started: bool | None = None
        self.streamed: str | None = None

    def create_run(self, request: dict[str, Any], *, start: bool = True) -> dict[str, Any]:
        self.created = request
        self.started = start
        return {"id": "run-123", "status": "running"}

    def stream_run_events(self, run_id: str):
        self.streamed = run_id
        return iter([{"status": "completed", "approval_reason": ""}])


def test_parser_registers_design_with_flow_default() -> None:
    args = build_parser().parse_args(["design", "an I2C IMU breakout board"])
    assert args.command == "design"
    assert args.goal == "an I2C IMU breakout board"
    assert args.flow == "hardware_v1"  # default


def test_parser_rejects_unknown_flow() -> None:
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["design", "x", "--flow", "not_a_flow"])


def test_handle_design_builds_request_and_starts() -> None:
    client = _Client()
    args = argparse.Namespace(
        goal="a BME280 logger",
        flow="hardware_v1",
        project_id="p1",
        no_start=False,
        no_watch=True,  # skip streaming in the test
    )
    handle_design(args, client)
    assert client.created == {"goal": "a BME280 logger", "flow": "hardware_v1", "project_id": "p1"}
    assert client.started is True
    assert client.streamed is None  # --no-watch


def test_handle_design_watches_by_default() -> None:
    client = _Client()
    args = argparse.Namespace(
        goal="a bracket", flow="mech_v1", project_id=None, no_start=False, no_watch=False
    )
    handle_design(args, client)
    assert "project_id" not in client.created  # omitted when not given
    assert client.streamed == "run-123"  # streamed the started run


def test_handle_design_no_start_skips_watch() -> None:
    client = _Client()
    args = argparse.Namespace(
        goal="x", flow="design_v1", project_id=None, no_start=True, no_watch=False
    )
    handle_design(args, client)
    assert client.started is False
    assert client.streamed is None  # not started -> nothing to watch
