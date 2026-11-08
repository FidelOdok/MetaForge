"""``files.*`` from the sidecar: the gateway's routes, links on the sidecar's address."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from metaforge.mcp.remote_files import PUBLIC_BASE, RemoteFilesService, public_base
from tool_registry.tools.files.adapter import FilesServer


def _service(seen: list[httpx.Request], reply: dict[str, Any]) -> RemoteFilesService:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RemoteFilesService("http://gateway:8000", client=client)


async def test_a_link_is_made_absolute_on_the_address_the_client_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METAFORGE_MCP_PUBLIC_URL", raising=False)
    seen: list[httpx.Request] = []
    files = _service(seen, {"via": "gateway", "path": "/v1/links/tok.sig", "url": None})
    token = PUBLIC_BASE.set("https://mcp.example.com")
    try:
        out = await FilesServer(files).link({"work_product_id": "wp-1"})
    finally:
        PUBLIC_BASE.reset(token)
    assert out["url"] == "https://mcp.example.com/v1/links/tok.sig"
    assert seen[0].url.path == "/v1/files/wp-1/link"


async def test_the_configured_public_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_MCP_PUBLIC_URL", "https://forge.example.com/")
    token = PUBLIC_BASE.set("http://10.0.0.5:8765")
    try:
        assert public_base() == "https://forge.example.com"
    finally:
        PUBLIC_BASE.reset(token)


async def test_read_sends_its_query_and_import_sends_who_asked() -> None:
    seen: list[httpx.Request] = []
    files = _service(seen, {"id": "u1", "status": "pending"})
    await files.read("wp-1", "board/a b.kicad_pcb", 100)
    assert seen[0].url.params["path"] == "board/a b.kicad_pcb"
    assert seen[0].url.params["max_bytes"] == "100"
    out = await FilesServer(files).import_files({"files": [{"name": "a.md", "text": "x"}]})
    body = json.loads(seen[1].content)
    assert body["requested_by"] and body["files"][0]["name"] == "a.md"
    assert out["approval_id"] == "upload:u1"


async def test_local_paths_are_refused_away_from_the_local_plugin() -> None:
    files = _service([], {})
    assert "files.import_local" not in FilesServer(files).tool_ids
    with pytest.raises(ValueError, match="local plugin"):
        await files.stage_local({"paths": ["a.step"]})


async def test_the_agent_is_named_to_the_gateway_on_staging_and_deciding() -> None:
    from mcp_core.context import McpCallContext, with_context

    seen: list[httpx.Request] = []
    reply = {"id": "u1", "upload_id": "u1", "approval_id": "upload:u1", "path": "/x"}
    files = _service(seen, reply)
    with with_context(McpCallContext(agent="claude-code-e2e", on_behalf_of="fidel")):
        await FilesServer(files).request_upload({})
    assert seen[0].headers["X-MetaForge-Agent"] == "claude-code-e2e"
    assert "agent" not in json.loads(seen[0].content)
    await files.decide_upload("u1", "approve", "", "claude-code-e2e", "fidel")
    decide = seen[1]
    assert decide.url.path == "/v1/approvals/upload:u1/decision"
    assert decide.headers["X-MetaForge-Surface"] == "agent"
    assert decide.headers["X-MetaForge-Agent"] == "claude-code-e2e"
    assert decide.headers["X-MetaForge-On-Behalf-Of"] == "fidel"
    assert json.loads(decide.content) == {"decision": "approve", "reason": ""}
