"""Unit tests for `forge approvals` and its aliases (FORGE-509)."""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx
import pytest

from cli.forge_cli import approvals as ap
from cli.forge_cli.client import ForgeClient, ForgeClientError
from cli.forge_cli.main import build_parser, handle_approve, handle_reject
from cli.forge_cli.runs import handle_runs


def _item(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "gate:run_1",
        "kind": "gate",
        "status": "pending",
        "title": "Design gate",
        "summary": "s",
        "project_id": "p1",
        "created_at": "2026-10-03T00:00:00Z",
        "findings": [{"kind": "ungrounded", "severity": "error", "message": "no source"}],
        "allowed_decisions": ["approve", "reject", "retry", "rework"],
        "rework_targets": ["design"],
        "reason_required_for": ["reject", "retry", "rework"],
        "decidable": True,
        "not_decidable_reason": None,
        "detail": {"run_id": "run_1", "phase": "mechanical"},
        "decision": None,
    }
    base.update(kw)
    return base


class Fake:
    def __init__(self, item: dict[str, Any] | None = None, err: ForgeClientError | None = None):
        self.item = item or _item()
        self.err = err
        self.posts: list[tuple[Any, ...]] = []
        self.list_args: dict[str, Any] = {}

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        if self.err and self.err.status_code == 404:
            raise self.err
        return self.item

    def decide_approval(self, approval_id, decision, reason=None, to_phase=None):  # type: ignore[no-untyped-def]
        self.posts.append((approval_id, decision, reason, to_phase))
        if self.err:
            raise self.err
        return {**self.item, "status": "approved"}

    def list_approvals(self, **kw: Any) -> dict[str, Any]:
        self.list_args = kw
        return {"items": [self.item], "unscoped_count": 2}


def _run(argv: list[str], client: Any) -> int:
    args = build_parser().parse_args(argv)
    try:
        ap.handle_approvals(args, client)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_list_defaults_to_pending_and_reports_unscoped(capsys: pytest.CaptureFixture[str]) -> None:
    c = Fake()
    assert _run(["approvals", "list"], c) == 0
    assert c.list_args == {"status": "pending", "project_id": None, "kind": None}
    out = capsys.readouterr().out
    assert "gate:run_1" in out and "2 approval(s)" in out


def test_list_flags(capsys: pytest.CaptureFixture[str]) -> None:
    c = Fake()
    _run(["approvals", "list", "--all", "--project", "p1", "--kind", "gate", "--json"], c)
    assert c.list_args == {"status": "all", "project_id": "p1", "kind": "gate"}
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "gate:run_1"
    _run(["approvals", "list", "--decided"], c)
    assert c.list_args["status"] == "decided"


def test_show_prints_all_fields(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["approvals", "show", "gate:run_1"], Fake()) == 0
    out = capsys.readouterr().out
    for needle in ("no source", "approve, reject, retry, rework", "design", "mechanical", "yes"):
        assert needle in out


def test_show_json_is_raw(capsys: pytest.CaptureFixture[str]) -> None:
    _run(["approvals", "show", "gate:run_1", "--json"], Fake())
    assert json.loads(capsys.readouterr().out) == _item()


def test_show_not_found_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = _run(["approvals", "show", "gate:x"], Fake(err=ForgeClientError("nope", 404)))
    assert code == ap.EXIT_NOT_FOUND
    assert "no approval with id" in capsys.readouterr().err


def test_approve_posts_without_reason() -> None:
    c = Fake()
    assert _run(["approvals", "approve", "gate:run_1"], c) == 0
    assert c.posts == [("gate:run_1", "approve", None, None)]


def test_reject_requires_reason_locally() -> None:
    c = Fake()
    assert _run(["approvals", "reject", "gate:run_1"], c) == ap.EXIT_INVALID
    assert c.posts == []
    assert _run(["approvals", "reject", "gate:run_1", "--reason", "bad"], c) == 0
    assert c.posts == [("gate:run_1", "reject", "bad", None)]


def test_decision_not_allowed_locally() -> None:
    c = Fake(_item(allowed_decisions=["approve"]))
    assert _run(["approvals", "retry", "gate:run_1", "--reason", "x"], c) == ap.EXIT_INVALID
    assert c.posts == []


def test_rework_validates_target(capsys: pytest.CaptureFixture[str]) -> None:
    c = Fake()
    code = _run(["approvals", "rework", "gate:run_1", "--to", "nope", "--reason", "r"], c)
    assert code == ap.EXIT_INVALID and c.posts == []
    assert "rework target" in capsys.readouterr().err
    assert _run(["approvals", "rework", "gate:run_1", "--to", "design", "--reason", "r"], c) == 0
    assert c.posts == [("gate:run_1", "rework", "r", "design")]


def test_not_decidable_is_conflict() -> None:
    c = Fake(_item(decidable=False, not_decidable_reason="expired"))
    assert _run(["approvals", "approve", "gate:run_1"], c) == ap.EXIT_CONFLICT
    assert c.posts == []


@pytest.mark.parametrize(
    ("status", "code"),
    [(409, ap.EXIT_CONFLICT), (422, ap.EXIT_INVALID), (401, ap.EXIT_AUTH), (500, ap.EXIT_ERROR)],
)
def test_server_errors_map_to_exit_codes(status: int, code: int) -> None:
    c = Fake(err=ForgeClientError("boom", status))
    assert _run(["approvals", "approve", "gate:run_1"], c) == code


def test_runs_approve_alias_uses_gate_id() -> None:
    c = Fake()
    args = build_parser().parse_args(["runs", "approve", "run_1"])
    handle_runs(args, c)  # type: ignore[arg-type]
    assert c.posts == [("gate:run_1", "approve", None, None)]


def test_proposal_aliases_use_change_id() -> None:
    c = Fake(_item(id="change:abc"))
    handle_approve(build_parser().parse_args(["approve", "abc", "--reason", "ok"]), c)  # type: ignore[arg-type]
    handle_reject(build_parser().parse_args(["reject", "abc", "--reason", "no"]), c)  # type: ignore[arg-type]
    assert c.posts == [("change:abc", "approve", "ok", None), ("change:abc", "reject", "no", None)]


# -- real client over a mocked HTTP layer ----------------------------------


def _client_with(handler: Any) -> ForgeClient:
    client = ForgeClient(base_url="http://gw")
    transport = httpx.MockTransport(handler)
    client._client = lambda: httpx.Client(base_url="http://gw", transport=transport)  # type: ignore[method-assign]
    return client


def test_client_headers_and_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("METAFORGE_APPROVAL_AGENT", "METAFORGE_APPROVAL_ON_BEHALF_OF"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("METAFORGE_AUTH_TOKEN", "tok")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [], "unscoped_count": 0})

    client = _client_with(handler)
    client.list_approvals(status="all", project_id="p", kind="gate")
    req = seen[0]
    assert req.url.path == "/v1/approvals"
    assert dict(req.url.params) == {"status": "all", "project_id": "p", "kind": "gate"}
    assert req.headers["x-metaforge-surface"] == "cli"
    assert req.headers["authorization"] == "Bearer tok"
    assert "x-metaforge-on-behalf-of" not in req.headers


def test_client_agent_surface_and_decision_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_APPROVAL_AGENT", "bot")
    monkeypatch.setenv("METAFORGE_APPROVAL_ON_BEHALF_OF", "alice")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_item())

    _client_with(handler).decide_approval("gate:run_1", "rework", "why", "design")
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/approvals/gate:run_1/decision"
    assert json.loads(req.content) == {"decision": "rework", "reason": "why", "to_phase": "design"}
    assert req.headers["x-metaforge-surface"] == "agent"
    assert req.headers["x-metaforge-on-behalf-of"] == "alice"


def test_client_raises_with_status_and_detail() -> None:
    client = _client_with(lambda r: httpx.Response(409, json={"detail": "expired"}))
    with pytest.raises(ForgeClientError) as ei:
        client.get_approval("gate:x")
    assert ei.value.status_code == 409 and "expired" in str(ei.value)


def test_argparse_namespace_roundtrip() -> None:
    args = build_parser().parse_args(
        ["approvals", "rework", "gate:r", "--to", "d", "--reason", "x"]
    )
    assert isinstance(args, argparse.Namespace) and args.to == "d"
