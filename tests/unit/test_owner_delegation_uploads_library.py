"""Owner delegation reaches an agent's own uploads and library registration.

A headless run taking a desk lamp to release stopped twice for a person nobody
could reach: the board it had edited and uploaded waited as an ``upload``
approval, and ``library.register_component`` (an ISO 4762 M4x35 screw) was
held on a client prompt. With ``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY`` on and
the MCP request naming the agent and the person:

* ``files.decide_upload`` decides an upload the same agent staged, recorded as
  that agent's decision for the person; any other upload is refused;
* ``library.register_*`` and ``library.update_*`` run, and the entry records
  ``agent X on behalf of Y``: delegated, never verified by the person.

With the flag off nothing changes. A project policy never approves any of
these, and ``library.dispatch_job`` and ``vendor.place_order`` are never
delegated at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from mcp_core.context import context_from_headers, with_context
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    DELEGATED_AGENT_ARG,
    Caller,
    delegable_library_write,
    delegated_authority,
    delegated_label,
    never_auto_approved,
    owner_delegable,
    policy_may_approve,
)
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.files.adapter import FilesServer
from tool_registry.tools.library.adapter import LibraryServer

FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
PROJECT = "p-lamp"
AGENT = "claude-code-e2e"
HEADERS = {"X-MetaForge-Agent": AGENT, "X-MetaForge-On-Behalf-Of": "fidel"}
SCREW = {
    "name": "iso4762_m4x35",
    "title": "M4x35 ISO 4762",
    "family": "screw",
    "origin": "standard",
    "standard": "ISO 4762",
    "size": "M4x35",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    reset_policy_store(PolicyStore())
    yield
    reset_policy_store(None)


# -- the rules ------------------------------------------------------------------------


def test_what_the_owner_may_delegate() -> None:
    for tool in (
        "library.register_component",
        "library.update_component",
        "library.register_material",
        "library.register_machine",
        "library.update_machine",
        "files.decide_upload",
        "twin.imagine_handover",
    ):
        assert owner_delegable(tool), tool
    for tool in (
        "library.dispatch_job",
        "library.record_job_result",
        "vendor.place_order",
        "library.discover_machine",
        "project.delete",
        "twin.approve_release",
    ):
        assert not owner_delegable(tool), tool
    assert not delegable_library_write("library.dispatch_job")


@pytest.mark.parametrize(
    "tool",
    ["library.register_component", "library.update_material", "files.decide_upload"],
)
def test_a_policy_never_approves_them(tool: str) -> None:
    assert never_auto_approved(tool)
    assert not policy_may_approve(tool, "auto")


def test_the_label_reads_back_as_delegated_never_verified() -> None:
    label = delegated_label(AGENT, "fidel")
    assert label == f"agent {AGENT} on behalf of fidel"
    assert len(delegated_label("a" * 300, "b" * 300)) <= 100
    assert delegated_authority(label) == {
        "kind": "delegated",
        "agent": AGENT,
        "on_behalf_of": "fidel",
        "verified_by_person": False,
    }
    assert delegated_authority("mcp")["kind"] == "recorded"
    assert delegated_authority("mcp")["verified_by_person"] is False


# -- the MCP server --------------------------------------------------------------------


class _Library:
    """The library service, recording who each write was made as."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, dict[str, Any], str]] = []

    async def register_entry(self, kind: str, data: dict[str, Any], by: str) -> dict[str, Any]:
        self.writes.append(("register", kind, data, by))
        return {"entry": {"id": "co_1", "name": data.get("name")}}

    async def update_entry(
        self, kind: str, ref: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]:
        self.writes.append(("update", kind, changes, by))
        return {"entry": {"id": ref}}

    async def register_machine(self, data: dict[str, Any], by: str) -> dict[str, Any]:
        self.writes.append(("register", "machine", data, by))
        return {"machine": {"id": "m_1"}}

    async def dispatch_job(self, machine_id: str, work_product_id: str, by: str) -> dict[str, Any]:
        self.writes.append(("dispatch", machine_id, {}, by))
        return {"job": {"id": "j_1"}}


class _Files:
    """The files service: uploads by id, and the decisions made on them."""

    supports_local_paths = False

    def __init__(self) -> None:
        self.uploads = {
            "u-mine": {"id": "u-mine", "status": "pending", "agent": AGENT},
            "u-other": {"id": "u-other", "status": "pending", "agent": "cursor"},
            "u-person": {"id": "u-person", "status": "pending", "agent": None},
        }
        self.decided: list[tuple[str, str, str, str, str]] = []

    def absolute(self, path: str) -> str:
        return path

    async def upload_status(self, upload_id: str) -> dict[str, Any]:
        return dict(self.uploads[upload_id])

    async def decide_upload(
        self, upload_id: str, decision: str, reason: str, agent: str, on_behalf_of: str
    ) -> dict[str, Any]:
        self.decided.append((upload_id, decision, reason, agent, on_behalf_of))
        self.uploads[upload_id]["status"] = "approved" if decision == "approve" else "rejected"
        return {"status": self.uploads[upload_id]["status"], "decision": {"agent": agent}}


def _vendor(ran: list[str]) -> McpToolServer:
    class _Vendor(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="vendor", version="0.1.0")

            async def place(args: dict[str, Any]) -> dict[str, Any]:
                ran.append("vendor.place_order")
                return {"ok": True}

            self.register_tool(
                ToolManifest(
                    tool_id="vendor.place_order", adapter_id="vendor", name="order",
                    description="stub", capability="test",
                ),
                place,
            )  # fmt: skip

    return _Vendor()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


async def _server(
    http: httpx.AsyncClient, library: _Library, files: _Files, vendor_ran: list[str]
) -> Any:
    server = UnifiedMcpServer(
        [LibraryServer(library), FilesServer(files), _vendor(vendor_ran)],  # type: ignore[arg-type]
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "2.1.316"},
                },
            }
        )
    )
    return server


async def _call(
    server: Any, tool: str, arguments: dict[str, Any], headers: dict[str, str] = HEADERS
) -> str:
    with with_context(context_from_headers(headers)):
        reply = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {"project_id": PROJECT, **arguments}},
                }
            )
        )
    return str(reply)


def _runs() -> list[Any]:
    from api_gateway.chat.tool_approvals import get_approval_store

    return list(get_approval_store().list())


def _delegated() -> list[Any]:
    return [r for r in _runs() if r.request.get("route") == "delegated"]


@pytest.mark.asyncio
async def test_flag_on_a_library_registration_runs_as_the_agent_for_the_person(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        reply = await _call(server, "library.register_component", SCREW)
    assert len(library.writes) == 1, reply
    _, kind, data, by = library.writes[0]
    assert kind == "component" and data["name"] == "iso4762_m4x35"
    assert by == f"agent {AGENT} on behalf of fidel"
    # The reserved keys the dispatcher set never reach the library.
    assert APPROVED_BY_ARG not in data and DELEGATED_AGENT_ARG not in data
    assert '"kind": "delegated"' in reply.replace('\\"', '"')
    assert "verified_by_person" in reply
    (run,) = _delegated()
    assert run.request["tool"] == "library.register_component"
    assert run.request["agent"] == AGENT and run.request["on_behalf_of"] == "fidel"


@pytest.mark.asyncio
async def test_flag_on_a_library_update_runs_too(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        await _call(server, "library.update_component", {"ref": "co_1", "changes": {"notes": "x"}})
    assert library.writes == [
        ("update", "component", {"notes": "x"}, f"agent {AGENT} on behalf of fidel")
    ]


@pytest.mark.asyncio
async def test_flag_off_a_library_registration_is_held_as_before(
    gateway: httpx.AsyncClient,
) -> None:
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        await _call(server, "library.register_component", SCREW)
    assert library.writes == []
    assert _delegated() == []
    assert any(r.request.get("tool") == "library.register_component" for r in _runs())


@pytest.mark.asyncio
async def test_a_policy_never_approves_a_library_registration(gateway: httpx.AsyncClient) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto", updated_by="user:fidel")
    )
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        await _call(server, "library.register_component", SCREW)
    assert library.writes == []
    assert all(r.request.get("route") not in ("policy", "delegated") for r in _runs())


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("library.dispatch_job", {"machine_id": "m_1", "work_product_id": "wp"}),
        ("vendor.place_order", {"quote_id": "q"}),
    ],
)
@pytest.mark.asyncio
async def test_a_print_job_or_an_order_is_never_delegated(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tool: str, arguments: Any
) -> None:
    from api_gateway.approvals.policy import get_policy_store

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv("METAFORGE_ALLOW_AGENT_RELEASE", "true")
    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto", updated_by="user:fidel")
    )
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        await _call(server, tool, arguments)
    assert library.writes == [] and vendor == []
    assert all(r.request.get("route") not in ("policy", "delegated") for r in _runs())


@pytest.mark.asyncio
async def test_flag_on_the_agent_decides_its_own_upload(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        reply = await _call(
            server, "files.decide_upload", {"upload_id": "upload:u-mine", "decision": "approve"}
        )
    assert files.decided == [("u-mine", "approve", "", AGENT, "fidel")], reply
    assert f"agent {AGENT} on behalf of fidel" in reply
    (run,) = _delegated()
    assert run.request["tool"] == "files.decide_upload"


@pytest.mark.parametrize("upload_id", ["u-other", "u-person"])
@pytest.mark.asyncio
async def test_the_agent_never_decides_an_upload_it_did_not_stage(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, upload_id: str
) -> None:
    monkeypatch.setenv(FLAG, "true")
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        reply = await _call(
            server, "files.decide_upload", {"upload_id": upload_id, "decision": "approve"}
        )
    assert files.decided == []
    assert "decides only its own uploads" in reply


@pytest.mark.parametrize("headers", [{}, HEADERS])
@pytest.mark.asyncio
async def test_without_the_delegation_an_upload_decision_is_refused_not_held(
    gateway: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    library, files, vendor = _Library(), _Files(), []
    async with gateway as http:
        server = await _server(http, library, files, vendor)
        reply = await _call(
            server, "files.decide_upload", {"upload_id": "u-mine", "decision": "approve"}, headers
        )
    assert files.decided == []
    assert "files.decide_upload runs only for an agent the owner delegates" in reply
    assert not any(r.request.get("tool") == "files.decide_upload" for r in _runs())


@pytest.mark.asyncio
async def test_the_handler_refuses_a_call_the_dispatcher_did_not_delegate() -> None:
    from mcp_core.guardrails import NotDelegatedError

    server = FilesServer(_Files())  # type: ignore[arg-type]
    with pytest.raises(NotDelegatedError):
        await server.decide_upload({"upload_id": "u-mine", "decision": "approve"})


@pytest.mark.asyncio
async def test_the_gateway_records_a_delegated_library_write_and_nothing_physical(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"agent": AGENT, "on_behalf_of": "fidel", "approver": "fidel"}
    url = "/v1/chat/tool_approvals/delegated"
    async with gateway as http:
        off = await http.post(url, json={**body, "tool": "library.register_component"})
        monkeypatch.setenv(FLAG, "true")
        on = await http.post(url, json={**body, "tool": "library.register_component"})
        upload = await http.post(url, json={**body, "tool": "files.decide_upload"})
        dispatch = await http.post(url, json={**body, "tool": "library.dispatch_job"})
        order = await http.post(url, json={**body, "tool": "vendor.place_order"})
    assert off.status_code == 403
    assert on.status_code == 200 and upload.status_code == 200
    assert dispatch.status_code == 422 and order.status_code == 422


# -- the upload, through the approvals API and the in-process binding -----------------


class _NoMetrics:
    def record_approval_decision(self, *args: object) -> None:
        return None


@pytest.fixture
def uploads(tmp_path: Path) -> Iterator[TestClient]:
    from api_gateway.approvals.routes import router as approvals_router
    from api_gateway.approvals.routes import set_metrics
    from api_gateway.twin import routes as twin_routes
    from api_gateway.uploads import routes as upload_routes
    from api_gateway.uploads.store import SqliteUploadStore
    from twin_core.api import InMemoryTwinAPI

    store = SqliteUploadStore(str(tmp_path / "u.db"), staging_dir=tmp_path / "staging")
    twin = InMemoryTwinAPI.create()
    upload_routes.init_upload_store(store)
    twin_routes.init_twin(twin)
    set_metrics(_NoMetrics())
    app = FastAPI()
    app.state.twin = twin
    app.include_router(upload_routes.router)
    app.include_router(approvals_router)
    yield TestClient(app)
    upload_routes.init_upload_store(None)
    set_metrics(None)


def _stage(client: TestClient) -> str:
    resp = client.post(
        "/v1/uploads",
        files=[("files", ("lamp.kicad_pcb", b"(kicad_pcb (version 20240108))"))],
        headers={"X-MetaForge-Agent": AGENT},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


AGENT_HEADERS = {
    "X-MetaForge-Surface": "agent",
    "X-MetaForge-Agent": AGENT,
    "X-MetaForge-On-Behalf-Of": "fidel",
}


def test_flag_off_the_approvals_api_refuses_an_agent(uploads: TestClient) -> None:
    upload_id = _stage(uploads)
    resp = uploads.post(
        f"/v1/approvals/upload:{upload_id}/decision",
        json={"decision": "approve"},
        headers=AGENT_HEADERS,
    )
    assert resp.status_code == 403


def test_flag_on_the_approvals_api_records_the_agent_for_the_person(
    uploads: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    upload_id = _stage(uploads)
    resp = uploads.post(
        f"/v1/approvals/upload:{upload_id}/decision",
        json={"decision": "approve"},
        headers=AGENT_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    decision = resp.json()["decision"]
    assert decision["surface"] == "agent" and decision["agent"] == AGENT
    assert decision["on_behalf_of"] == "fidel" and decision["approver_verified"] is False
    upload = uploads.get(f"/v1/uploads/{upload_id}").json()
    assert upload["status"] == "approved" and upload["agent"] == AGENT
    assert upload["decision"]["agent"] == AGENT
    assert upload["files"][0]["work_product_id"]


@pytest.mark.asyncio
async def test_the_in_process_binding_decides_through_the_same_rules(
    uploads: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.files.mcp_bindings import LocalFilesService

    service = LocalFilesService()
    upload_id = _stage(uploads)
    with pytest.raises(ValueError, match="agents may not decide it"):
        await service.decide_upload(upload_id, "approve", "", AGENT, "fidel")
    monkeypatch.setenv(FLAG, "true")
    item = await service.decide_upload(upload_id, "reject", "wrong board", AGENT, "fidel")
    assert item["status"] == "rejected"
    status = await service.upload_status(upload_id)
    assert status["decision"]["agent"] == AGENT
    assert status["decision"]["on_behalf_of"] == "fidel"


@pytest.mark.asyncio
async def test_the_library_history_says_the_change_was_delegated() -> None:
    from api_gateway.library import catalog_routes
    from api_gateway.library.mcp_bindings import LocalMachineLibrary
    from twin_core.library.catalog import SqliteCatalog

    catalog_routes.init_catalog(SqliteCatalog())
    try:
        library = LocalMachineLibrary()
        created = await library.register_entry(
            "component", dict(SCREW), delegated_label(AGENT, "fidel")
        )
        got = await library.get_entry("component", created["entry"]["id"])
    finally:
        catalog_routes.init_catalog(None)
    (version,) = got["history"]
    assert version["authority"] == {
        "kind": "delegated",
        "agent": AGENT,
        "on_behalf_of": "fidel",
        "verified_by_person": False,
    }
