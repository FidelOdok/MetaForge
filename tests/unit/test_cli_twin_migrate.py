"""`forge twin migrate <project> [--apply]` (FORGE-529)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cli.forge_cli.client import ForgeClient
from cli.forge_cli.main import build_parser, handle_twin

PID = "e7b896cc-0000-4000-8000-000000000529"
PLAN = {"project_id": PID, "plan_hash": "abc123def456", "items": [{"key": "CAD-X"}]}
RESULT = {
    "plan_hash": "abc123def456",
    "items_created": 4,
    "items_extended": 0,
    "revisions_linked": 16,
    "run_summaries_marked": 14,
    "records_pinned": 8,
    "records_stale": 5,
    "failures": [],
}


class _Client:
    def __init__(self, empty: bool = False) -> None:
        self.empty = empty
        self.applied: list[tuple[str, dict[str, Any], str]] = []

    def twin_migration_plan(self, project_id: str) -> dict[str, Any]:
        return {"plan": PLAN, "report": "REPORT TABLE", "empty": self.empty}

    def twin_migration_apply(
        self, project_id: str, plan: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        self.applied.append((project_id, plan, reason))
        return {"result": RESULT, "approved_by": "local:dashboard", "approver_verified": False}


def _args(*argv: str) -> Any:
    return build_parser().parse_args(["twin", "migrate", PID, *argv])


def test_the_default_is_a_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    client = _Client()
    assert handle_twin(_args(), client) is None  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "REPORT TABLE" in out and "--apply" in out
    assert client.applied == []


def test_json_dry_run_returns_the_plan() -> None:
    args = build_parser().parse_args(["--format", "json", "twin", "migrate", PID])
    assert handle_twin(args, _Client()) == PLAN  # type: ignore[arg-type]


def test_apply_with_yes_sends_the_plan_back(capsys: pytest.CaptureFixture[str]) -> None:
    client = _Client()
    handle_twin(_args("--apply", "--yes", "--reason", "reviewed"), client)  # type: ignore[arg-type]
    assert client.applied == [(PID, PLAN, "reviewed")]
    out = capsys.readouterr().out
    assert "4 items created" in out and "5 stale" in out


def test_apply_asks_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _Client()
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    handle_twin(_args("--apply"), client)  # type: ignore[arg-type]
    assert client.applied == []
    assert "Not applied" in capsys.readouterr().out


def test_an_empty_plan_applies_nothing() -> None:
    client = _Client(empty=True)
    handle_twin(_args("--apply", "--yes"), client)  # type: ignore[arg-type]
    assert client.applied == []


def test_client_raises_the_gateway_message_on_a_stale_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/twin/projects/{PID}/item-migration/apply"
        assert request.headers["X-MetaForge-Surface"] == "cli"
        return httpx.Response(409, json={"detail": "the twin changed since this plan was made"})

    client = ForgeClient(base_url="http://gw")
    monkeypatch.setattr(
        client,
        "_client",
        lambda: httpx.Client(base_url="http://gw", transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(RuntimeError, match="409.*twin changed"):
        client.twin_migration_apply(PID, PLAN, "r")
