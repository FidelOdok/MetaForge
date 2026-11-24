"""Notices ride in a structured field, and results carry structured content (FORGE-930, R10).

The tool's JSON was the first text block and notices were further text blocks; a
client that joins a result's text (as Claude Code shows it) got the notice glued onto
the JSON, which no longer parsed. Now the JSON is also ``structuredContent``, every
notice is in ``_meta["metaforge/notices"]``, and a client that declares it reads that
list gets no notice text at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer
from tests.unit.test_unified_mcp_server import _AlphaServer, _request

WAITING = {"id": "gate:run_1", "status": "pending", "title": "Design gate"}


async def _lister(project_id: str | None) -> list[dict[str, Any]]:
    return [WAITING]


async def _call(server: UnifiedMcpServer) -> dict[str, Any]:
    raw = await server.handle_request(
        _request("tools/call", {"name": "alpha.add", "arguments": {"a": 3, "b": 4}})
    )
    body = json.loads(raw)
    assert "error" not in body, body
    return dict(body["result"])


def _server(capabilities: dict[str, Any] | None = None) -> UnifiedMcpServer:
    server = UnifiedMcpServer(adapters=[_AlphaServer()], caller=Caller.LOCAL)
    server.attach_approval_notices(_lister)
    server._client_capabilities = capabilities or {}  # noqa: SLF001 - as initialize sets it
    return server


@pytest.mark.asyncio
async def test_the_result_is_structured_content_and_notices_are_a_field() -> None:
    result = await _call(_server())
    assert result["structuredContent"]["data"] == {"sum": 7}
    assert json.loads(result["content"][0]["text"])["data"] == {"sum": 7}
    (notice,) = result["_meta"]["metaforge/notices"]
    assert notice["kind"] == "approvals_waiting" and "Design gate" in notice["text"]
    # A client whose model reads only content still hears it, as its own block.
    assert result["content"][-1]["text"] == notice["text"]


@pytest.mark.asyncio
async def test_a_client_that_reads_the_field_gets_no_notice_text() -> None:
    result = await _call(_server({"experimental": {"metaforge/notices": True}}))
    assert len(result["content"]) == 1  # the tool's JSON alone: it parses whole
    assert json.loads(result["content"][0]["text"])["data"] == {"sum": 7}
    assert result["_meta"]["metaforge/notices"][0]["kind"] == "approvals_waiting"
    assert result["_meta"]["pendingApprovals"] == 1


@pytest.mark.asyncio
async def test_no_notice_no_field() -> None:
    server = UnifiedMcpServer(adapters=[_AlphaServer()], caller=Caller.LOCAL)
    result = await _call(server)
    assert "metaforge/notices" not in result["_meta"] and len(result["content"]) == 1


@pytest.mark.asyncio
async def test_every_result_says_which_tool_set_it_came_from() -> None:
    from tests.unit.test_unified_mcp_server import _BetaServer

    server = UnifiedMcpServer(adapters=[_AlphaServer()], caller=Caller.LOCAL)
    listed = json.loads(await server.handle_request(_request("tools/list")))["result"]
    digest = listed["_meta"]["metaforge/toolSet"]
    assert len(digest) == 16
    assert (await _call(server))["_meta"]["metaforge/toolSet"] == digest
    # Another tool set (a deploy added tools) is another digest.
    bigger = UnifiedMcpServer(adapters=[_AlphaServer(), _BetaServer()], caller=Caller.LOCAL)
    again = json.loads(await bigger.handle_request(_request("tools/list")))["result"]
    assert again["_meta"]["metaforge/toolSet"] != digest


@pytest.mark.asyncio
async def test_a_server_that_was_never_listed_works_the_digest_out_itself() -> None:
    import asyncio

    server = UnifiedMcpServer(adapters=[_AlphaServer()], caller=Caller.LOCAL)
    first = await _call(server)
    assert "metaforge/toolSet" not in first["_meta"]  # being worked out, never blocking
    await asyncio.sleep(0.05)
    assert len((await _call(server))["_meta"]["metaforge/toolSet"]) == 16
