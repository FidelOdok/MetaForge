"""A held call takes its project from the session handle (FORGE-831).

Seen live: a headless agent called ``session.start`` on the lamp project and
was told ``project_scope_bound: true`` and a handle to pass back as
``metaforge_session``. Its next call, ``cadquery.execute_script`` (no
``project_id`` argument) with that handle, was held although the project's
policy is ``auto``: the gateway said "the call names no project". The handle
moved the call onto its session, but the project had been resolved from the
transport before the handle was read, so every hold, policy, delegation and
approval-key decision saw no project.

The project is now resolved in this order: an explicit ``project_id``
argument, then the handle's project, then the transport session's scope.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from mcp_core.context import (
    HEADER_SESSION,
    bind_session_project,
    clear_session_project,
    context_from_headers,
    with_context,
)
from mcp_core.guardrails import Caller
from mcp_core.protocol_versions import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MODERN_PREVIEW_ENV,
    SESSION_ARGUMENT,
)
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

SCRIPT = "cadquery.execute_script"
HANDOVER = "twin.imagine_handover"
LAMP = uuid4()
OTHER = uuid4()
DELEGATE_FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
AGENT_HEADERS = {"X-MetaForge-Agent": "claude-code-e2e", "X-MetaForge-On-Behalf-Of": "fidel"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(MODERN_PREVIEW_ENV, "on")
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.delenv(DELEGATE_FLAG, raising=False)
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    reset_policy_store(PolicyStore())
    yield
    reset_policy_store(None)


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


async def _policy(project: UUID, mode: str) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=str(project), mode=mode, updated_by="user:fidel")
    )


def _handle(project: UUID) -> UUID:
    """A handle bound to ``project``, as ``session.start`` leaves it."""
    handle = uuid4()
    bind_session_project(handle, project)
    return handle


def _adapter(ran: list[tuple[str, dict[str, Any]]]) -> McpToolServer:
    class _Tools(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="cadquery", version="0.1.0")
            for tool in (SCRIPT, HANDOVER):
                self.register_tool(
                    ToolManifest(
                        tool_id=tool, adapter_id="cadquery", name=tool, description="stub",
                        capability="test",
                        input_schema={
                            "type": "object",
                            "properties": {"script": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    ),
                    self._handler(tool),
                )  # fmt: skip

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append((tool, dict(args)))
                return {"ok": True}

            return handle

    return _Tools()


def _server(http: httpx.AsyncClient, ran: list[tuple[str, dict[str, Any]]]) -> UnifiedMcpServer:
    return UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )


async def _modern_call(
    server: UnifiedMcpServer,
    tool: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One handshake-free call, as the headless agent made it."""
    body = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": arguments,
            "_meta": {
                META_PROTOCOL_VERSION: "2026-07-28",
                META_CLIENT_CAPABILITIES: capabilities or {},
            },
        },
    }
    with with_context(context_from_headers(headers or {})):
        reply: dict[str, Any] = json.loads(await server.handle_request(json.dumps(body)))
    return reply


def _runs() -> list[Any]:
    from api_gateway.chat.tool_approvals import get_approval_store

    return list(get_approval_store().list())


@pytest.mark.parametrize(
    "capabilities",
    [{}, {"elicitation": {"form": {}}}],
    ids=["durable-hold-path", "in-result-question-path"],
)
@pytest.mark.asyncio
async def test_a_handle_bound_to_an_auto_project_is_approved_by_its_policy(
    capabilities: dict[str, Any], gateway: httpx.AsyncClient
) -> None:
    await _policy(LAMP, "auto")
    handle = _handle(LAMP)
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        reply = await _modern_call(
            _server(http, ran),
            SCRIPT,
            {SESSION_ARGUMENT: str(handle), "script": "result = 1"},
            capabilities=capabilities,
        )
    # Ran, and the tool never saw the handle among its own arguments.
    assert ran == [(SCRIPT, {"script": "result = 1"})], reply
    (run,) = [r for r in _runs() if r.request.get("route") == "policy"]
    # The policy request carried the project and the session the handle names.
    assert run.request["project"] == str(LAMP)
    assert run.request["session_id"] == str(handle)
    clear_session_project(handle)


@pytest.mark.asyncio
async def test_with_no_handle_and_no_scope_the_call_is_still_held(
    gateway: httpx.AsyncClient,
) -> None:
    await _policy(LAMP, "auto")
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        await _modern_call(_server(http, ran), SCRIPT, {"script": "result = 1"})
    assert ran == []
    assert all(r.request.get("route") != "policy" for r in _runs())


@pytest.mark.asyncio
async def test_a_handle_for_another_project_uses_that_project(
    gateway: httpx.AsyncClient,
) -> None:
    """The transport's own scope is the lamp (auto); the handle names another
    project (off). The handle's project decides, and nothing of the lamp's
    policy leaks into it."""
    await _policy(LAMP, "auto")
    await _policy(OTHER, "off")
    transport = uuid4()
    bind_session_project(transport, LAMP)
    other = _handle(OTHER)
    lamp = _handle(LAMP)
    headers = {HEADER_SESSION: str(transport)}
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = _server(http, ran)
        await _modern_call(
            server, SCRIPT, {SESSION_ARGUMENT: str(other), "script": "a"}, headers=headers
        )
        assert ran == [], "held under the other project's policy"
        held = [r for r in _runs() if r.request.get("tool") == SCRIPT]
        assert held and all(r.request.get("project") == str(OTHER) for r in held)

        await _modern_call(
            server, SCRIPT, {SESSION_ARGUMENT: str(lamp), "script": "b"}, headers=headers
        )
        assert ran == [(SCRIPT, {"script": "b"})]
        (approved,) = [r for r in _runs() if r.request.get("route") == "policy"]
        assert approved.request["project"] == str(LAMP)
        assert approved.request["session_id"] == str(lamp)
    for session in (transport, other, lamp):
        clear_session_project(session)


@pytest.mark.asyncio
async def test_an_explicit_project_argument_outranks_the_handle(
    gateway: httpx.AsyncClient,
) -> None:
    from metaforge.mcp.server import _context_for_handle, _effective_project

    handle = _handle(LAMP)
    with with_context(_context_for_handle(context_from_headers({}), handle)):
        assert _effective_project({}) == str(LAMP)
        assert _effective_project({"project_id": str(OTHER)}) == str(OTHER)
    clear_session_project(handle)


@pytest.mark.asyncio
async def test_a_handshake_client_passing_the_handle_gets_the_same_scope(
    gateway: httpx.AsyncClient,
) -> None:
    """A handshake (2025-06-18) client that does not keep the transport
    session can pass the handle too: stripped before the tool's schema, and
    the handle's project decides."""
    await _policy(LAMP, "auto")
    handle = _handle(LAMP)
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = _server(http, ran)
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
        call = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": SCRIPT,
                "arguments": {SESSION_ARGUMENT: str(handle), "script": "c"},
            },
        }
        with with_context(context_from_headers({})):
            reply = json.loads(await server.handle_request(json.dumps(call)))
        bad = {**call, "params": {"name": SCRIPT, "arguments": {SESSION_ARGUMENT: "nope"}}}
        with with_context(context_from_headers({})):
            refused = json.loads(await server.handle_request(json.dumps(bad)))
    assert ran == [(SCRIPT, {"script": "c"})], reply
    (run,) = [r for r in _runs() if r.request.get("route") == "policy"]
    assert run.request["project"] == str(LAMP)
    assert refused["error"]["code"] == -32602
    clear_session_project(handle)


@pytest.mark.asyncio
async def test_the_delegation_path_resolves_the_project_from_the_handle(
    gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DELEGATE_FLAG, "true")
    handle = _handle(LAMP)
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        reply = await _modern_call(
            _server(http, ran), HANDOVER, {SESSION_ARGUMENT: str(handle)}, headers=AGENT_HEADERS
        )
    assert [t for t, _ in ran] == [HANDOVER], reply
    (run,) = [r for r in _runs() if r.request.get("route") == "delegated"]
    assert run.request["project"] == str(LAMP)
    assert run.request["session_id"] == str(handle)
    clear_session_project(handle)
