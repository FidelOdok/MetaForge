"""Phase 4 — MCP error-path coverage (MET-477).

Every error from the unified MCP server must return a JSON-RPC error
envelope with a numeric ``-32xxx`` ``code`` and structured ``data``.

Error code map (see ``metaforge/mcp/server.py``):

* ``-32600`` ``INVALID_REQUEST`` — malformed JSON, wrong ``jsonrpc`` version
* ``-32601`` ``METHOD_NOT_FOUND`` — unknown RPC method, unknown tool name
* ``-32001`` ``TOOL_EXECUTION_ERROR`` — handler raised; ``data`` carries
  ``error_type / tool_id / details / duration_ms``

The MET-450 stdio readline cap is now a 16 MiB default (env-tunable
via ``METAFORGE_MCP_MAX_LINE_BYTES``); see
``tests/unit/test_mcp_stdio_max_line_bytes.py`` for the regression
guard. The HTTP transport here doesn't have a body-size cap of its
own (uvicorn defaults to ``--limit-max-requests`` style controls), so
the oversize-payload test in this file stays a documented skip — the
real cap is exercised on the stdio side.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ._helpers import MCP_PATH, McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# -32600 INVALID_REQUEST
# ---------------------------------------------------------------------------


async def test_invalid_json_returns_minus_32600(mcp_client: httpx.AsyncClient) -> None:
    """A POST body that isn't valid JSON yields a clean error envelope."""
    response = await mcp_client.post(
        MCP_PATH,
        content="{not valid json",
        headers={"content-type": "application/json"},
    )
    response.raise_for_status()
    body = response.json()
    assert "error" in body, body
    assert body["error"]["code"] == -32600
    assert "json" in body["error"]["message"].lower()


async def test_wrong_jsonrpc_version_returns_minus_32600(
    mcp_client: httpx.AsyncClient,
) -> None:
    """A body without ``jsonrpc: "2.0"`` is rejected as INVALID_REQUEST."""
    req = {"id": 1, "method": "initialize", "params": {}}
    response = await mcp_client.post(MCP_PATH, json=req)
    response.raise_for_status()
    body = response.json()
    assert body.get("error", {}).get("code") == -32600


# ---------------------------------------------------------------------------
# -32601 METHOD_NOT_FOUND
# ---------------------------------------------------------------------------


async def test_unknown_rpc_method_returns_minus_32601(
    mcp_client: httpx.AsyncClient,
) -> None:
    """An unknown JSON-RPC method (not ``initialize`` / ``tools/*``) errors out cleanly."""
    with pytest.raises(McpRpcError) as exc_info:
        await rpc(mcp_client, "does/not/exist")
    assert exc_info.value.code == -32601
    assert "unknown method" in exc_info.value.message.lower()


async def test_unknown_tool_name_returns_minus_32601(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``tools/call`` on a tool that doesn't exist surfaces structured data."""
    with pytest.raises(McpRpcError) as exc_info:
        await call_tool(mcp_client, "nonexistent.tool", {})
    assert exc_info.value.code == -32601
    # ``ToolNotFoundError`` adds ``tool_id`` to the error envelope's data.
    assert exc_info.value.data.get("tool_id") == "nonexistent.tool"


# ---------------------------------------------------------------------------
# -32001 TOOL_EXECUTION_ERROR — handler raised ValueError / TypeError / etc.
# ---------------------------------------------------------------------------


async def test_missing_required_arg_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``twin.get_node`` without ``node_id`` raises inside the handler."""
    with pytest.raises(McpRpcError) as exc_info:
        await call_tool(mcp_client, "twin.get_node", {})
    assert exc_info.value.code == -32001
    data = exc_info.value.data
    assert data.get("tool_id") == "twin.get_node"
    assert data.get("error_type") == "TOOL_EXECUTION_ERROR"
    # `details` is the original handler error message.
    assert "node_id" in str(data.get("details", "")).lower()


async def test_invalid_uuid_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``twin.get_node`` with a non-UUID node_id surfaces structured data."""
    with pytest.raises(McpRpcError) as exc_info:
        await call_tool(mcp_client, "twin.get_node", {"node_id": "not-a-uuid"})
    assert exc_info.value.code == -32001
    assert "uuid" in str(exc_info.value.data.get("details", "")).lower()


async def test_invalid_enum_value_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``cadquery.create_parametric`` with an unknown ``shape_type`` enum errors out."""
    with pytest.raises(McpRpcError) as exc_info:
        await call_tool(
            mcp_client,
            "cadquery.create_parametric",
            {
                "shape_type": "not-a-real-shape",
                "parameters": {"length": 10},
                "output_path": "/tmp/x.step",
            },
        )
    assert exc_info.value.code == -32001
    assert exc_info.value.data.get("tool_id") == "cadquery.create_parametric"
    details = str(exc_info.value.data.get("details", ""))
    assert "shape" in details.lower()


async def test_mutating_cypher_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``twin.query_cypher`` is read-only by default; mutations surface a clean error."""
    with pytest.raises(McpRpcError) as exc_info:
        await call_tool(
            mcp_client,
            "twin.query_cypher",
            {"cypher": "CREATE (x:Thing {a: 1}) RETURN x"},
        )
    assert exc_info.value.code == -32001
    details = str(exc_info.value.data.get("details", "")).lower()
    assert "mutating" in details or "read-only" in details


# ---------------------------------------------------------------------------
# Error envelope shape contract — every error returned by /mcp must satisfy
# the JSON-RPC 2.0 shape: top-level ``jsonrpc / id / error{code, message}``,
# no ``result`` key, and ``error.code`` is a negative int.
# ---------------------------------------------------------------------------


async def test_error_envelope_shape_is_jsonrpc20_compliant(
    mcp_client: httpx.AsyncClient,
) -> None:
    """The error envelope keys + types are stable for client SDKs."""
    response = await mcp_client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 42, "method": "totally/fake/method"},
    )
    response.raise_for_status()
    body = response.json()
    assert body.get("jsonrpc") == "2.0"
    # id round-trip — MCP echoes the request id even on errors.
    assert body.get("id") in (42, "42")
    assert "result" not in body
    err = body["error"]
    assert isinstance(err.get("code"), int) and err["code"] < 0
    assert isinstance(err.get("message"), str) and err["message"]


# ---------------------------------------------------------------------------
# MET-450 — stdio readline 64 KiB guard
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "MET-450 lifted the asyncio.StreamReader default 64 KiB cap to "
        "16 MiB (env-tunable via METAFORGE_MCP_MAX_LINE_BYTES); see "
        "tests/unit/test_mcp_stdio_max_line_bytes.py for the regression "
        "guard. The HTTP transport here has no equivalent body cap, so "
        "this placeholder stays as documentation."
    )
)
async def test_stdio_64kb_payload_guard() -> None:
    """Placeholder — MET-450 fix lives in
    ``tests/unit/test_mcp_stdio_max_line_bytes.py``. The HTTP transport
    in this suite doesn't have an equivalent cap to exercise; we keep
    the marker so the readme map of error-path coverage stays
    self-documenting."""


# ---------------------------------------------------------------------------
# Bonus: empty / null params should not crash dispatch.
# ---------------------------------------------------------------------------


async def test_initialize_with_no_params_succeeds(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``initialize`` accepts an empty params object (the spec allows this)."""
    result = await rpc(mcp_client, "initialize", {})
    assert "protocolVersion" in result
    assert "capabilities" in result


async def test_tools_call_without_arguments_key_uses_empty_dict(
    mcp_client: httpx.AsyncClient,
) -> None:
    """Omitted ``arguments`` defaults to ``{}`` rather than failing the dispatch.

    The tool itself will then reject for missing required args (-32001),
    not the dispatcher (-32600/-32602).
    """
    response = await mcp_client.post(
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "twin.get_node"},  # no "arguments" key
        },
    )
    response.raise_for_status()
    body = response.json()
    # Must be an error, but with code -32001 (handler-raised) not -32600
    # (request-level malformed). Anything else means the dispatcher
    # crashed when it shouldn't have.
    assert "error" in body, body
    assert body["error"]["code"] == -32001
    # The unwrapped helper would also see this — sanity check via the
    # same path tests use.
    parsed = json.loads(json.dumps(body))
    assert parsed["error"]["data"].get("tool_id") == "twin.get_node"
