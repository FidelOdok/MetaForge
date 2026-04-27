"""End-to-end external-harness integration test (MET-340).

Verifies the complete L1+L2 stack: spawn ``python -m metaforge.mcp
--transport stdio`` as a subprocess (MET-337), connect to it via the
gateway-side ``McpClient`` + ``StdioTransport`` (MET-306), exercise the
unified tool surface end-to-end.

Opt in with ``pytest --integration``. CI runs this on every PR.

Per the loop file's MET-340 playbook, cadquery and knowledge handlers
that require heavy or backend-bound deps are gated:

* ``cadquery.create_parametric`` — handler call is skipped when the
  ``cadquery`` package isn't importable (CI doesn't ship it). The
  manifest-level assertion (tool present in ``tool/list``) still
  fires; that's the L2 verification this ticket targets.
* ``knowledge.search`` / ``knowledge.ingest`` — gated on a real
  ``KnowledgeService`` backend. The standalone server currently
  registers the knowledge adapter only when given a
  ``KnowledgeService`` instance; bootstrapping one inside the
  subprocess is non-trivial (Postgres + pgvector). Test skips the
  round-trip and notes the follow-up (MET-340-A).

Runtime budget < 60s; subprocess teardown deterministic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import AsyncIterator

import pytest

from mcp_core.client import McpClient
from mcp_core.schemas import ToolManifest
from mcp_core.transports import StdioTransport

pytestmark = pytest.mark.integration


_READY_SIGNAL = "metaforge-mcp ready"
_BOOT_TIMEOUT = 30.0


@pytest.fixture
async def mcp_client() -> AsyncIterator[tuple[McpClient, StdioTransport]]:
    """Spawn the standalone MCP server, connect, populate manifests.

    asyncio's ``BaseSubprocessTransport.__del__`` writes EOF on the
    event loop after teardown, so a benign ``Event loop is closed``
    `unraisable` warning fires. Filtered with the warnings filter
    scoped to this fixture; the test result is unaffected.
    """
    import warnings

    warnings.filterwarnings(
        "ignore",
        message=".*Event loop is closed.*",
        category=pytest.PytestUnraisableExceptionWarning,
    )
    transport = StdioTransport(
        command=[
            sys.executable,
            "-m",
            "metaforge.mcp",
            "--transport",
            "stdio",
            "--adapters",
            "cadquery,calculix",
        ],
        ready_signal=_READY_SIGNAL,
        ready_timeout=_BOOT_TIMEOUT,
    )
    await transport.connect()

    client = McpClient()
    await client.connect("metaforge", transport)
    await _discover_tools(client, transport)

    try:
        yield client, transport
    finally:
        await client.disconnect("metaforge")


async def _discover_tools(client: McpClient, transport: StdioTransport) -> None:
    """Pull manifests from the live server so ``call_tool`` can route."""
    request = json.dumps({"jsonrpc": "2.0", "id": "discover", "method": "tool/list", "params": {}})
    response_text = await transport.send(request)
    payload = json.loads(response_text)
    if "error" in payload:
        raise RuntimeError(f"tool/list failed during discovery: {payload['error']}")
    for tool in payload["result"]["tools"]:
        client.register_manifest(
            ToolManifest(
                tool_id=tool["tool_id"],
                adapter_id=tool.get("adapter_id", "metaforge"),
                name=tool["name"],
                description=tool.get("description", ""),
                capability=tool.get("capability", ""),
                input_schema=tool.get("input_schema", {}),
                output_schema=tool.get("output_schema", {}),
                phase=tool.get("phase", 1),
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_tool_list_returns_seven_or_more(
    mcp_client: tuple[McpClient, StdioTransport],
) -> None:
    """End-to-end ``tool/list`` round-trip — the L1 acceptance threshold."""
    client, _ = mcp_client
    tools = await client.list_tools()
    assert len(tools) >= 7, f"expected ≥7 tools, got {len(tools)}"
    tool_ids = sorted(t.tool_id for t in tools)
    # The default adapter set we asked for is cadquery + calculix.
    assert any(tid.startswith("cadquery.") for tid in tool_ids), tool_ids
    assert any(tid.startswith("calculix.") for tid in tool_ids), tool_ids


async def test_health_check_round_trip(
    mcp_client: tuple[McpClient, StdioTransport],
) -> None:
    """Server reports its roll-up health to an external client."""
    _, transport = mcp_client
    raw = await transport.send('{"jsonrpc":"2.0","id":"h","method":"health/check","params":{}}')
    body = json.loads(raw)
    assert body["result"]["service"] == "metaforge-mcp"
    assert body["result"]["status"] == "healthy"
    assert body["result"]["adapter_count"] >= 1
    assert body["result"]["tool_count"] >= 7


async def test_unknown_tool_returns_method_not_found(
    mcp_client: tuple[McpClient, StdioTransport],
) -> None:
    """Routing rejects unknown tool ids cleanly (no crash, no hang)."""
    _, transport = mcp_client
    raw = await transport.send(
        '{"jsonrpc":"2.0","id":"u","method":"tool/call",'
        '"params":{"tool_id":"nonexistent.tool","arguments":{}}}'
    )
    body = json.loads(raw)
    assert "error" in body
    assert body["error"]["code"] == -32601
    assert body["error"]["data"]["tool_id"] == "nonexistent.tool"


async def test_cadquery_create_parametric_box(
    mcp_client: tuple[McpClient, StdioTransport],
    tmp_path: pytest.TempPathFactory,
) -> None:
    """End-to-end CAD generation through the full external-harness path.

    Skipped when the ``cadquery`` package isn't installed (the manifest
    is still asserted in ``test_tool_list_returns_seven_or_more``; this
    test exercises the actual handler).
    """
    if importlib.util.find_spec("cadquery") is None:
        pytest.skip("cadquery package not installed; manifest covered elsewhere")

    client, _ = mcp_client
    output_path = str(tmp_path / "box.step")  # type: ignore[arg-type]
    from mcp_core.schemas import ToolCallRequest

    response = await asyncio.wait_for(
        client.call_tool(
            ToolCallRequest(
                tool_id="cadquery.create_parametric",
                arguments={
                    "shape_type": "box",
                    "parameters": {"width": 50, "length": 30, "height": 10},
                    "output_path": output_path,
                },
                timeout_seconds=30,
            )
        ),
        timeout=45,
    )
    assert response.status == "success"
    assert response.data.get("cad_file"), response.data
    # CadQuery may write to its own resolved path, not the literal one
    # we passed; just check the response carries something file-shaped.
    import os

    assert os.path.exists(response.data["cad_file"]), response.data["cad_file"]


@pytest.mark.skip(
    reason=(
        "knowledge.* tools require a live KnowledgeService inside the standalone "
        "server. Bootstrapping one in the subprocess is a follow-up (MET-340-A)."
    )
)
async def test_knowledge_round_trip(
    mcp_client: tuple[McpClient, StdioTransport],
) -> None:  # pragma: no cover — pending follow-up
    """Knowledge ingest + search through the external harness."""
    _, transport = mcp_client
    ingest = await transport.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "i",
                "method": "tool/call",
                "params": {
                    "tool_id": "knowledge.ingest",
                    "arguments": {
                        "content": "The SR-7 mounting bracket uses titanium grade 5.",
                        "source_path": "test://e2e/sr7-bracket",
                        "knowledge_type": "design_decision",
                    },
                },
            }
        )
    )
    body = json.loads(ingest)
    assert body["result"]["status"] == "success"

    search = await transport.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "s",
                "method": "tool/call",
                "params": {
                    "tool_id": "knowledge.search",
                    "arguments": {"query": "what material is the SR-7 bracket?", "top_k": 3},
                },
            }
        )
    )
    body = json.loads(search)
    hits = body["result"]["data"]["hits"]
    assert any("titanium" in h.get("content", "").lower() for h in hits), hits
