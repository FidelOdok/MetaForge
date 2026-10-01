"""Phase 4 — MCP error-path coverage (MET-477).

Every error from the unified MCP server must return a JSON-RPC error
envelope with a numeric ``-32xxx`` ``code`` and structured ``data``.

Error code map (see ``metaforge/mcp/server.py``):

* ``-32600`` ``INVALID_REQUEST`` — malformed JSON, wrong ``jsonrpc`` version
* ``-32601`` ``METHOD_NOT_FOUND`` — unknown RPC method, unknown tool name

A handler that **ran and refused** is not in that list any more (FORGE-419).
It returns a normal result with ``isError: true``, the reason as the text
content, and ``_meta.error`` carrying ``toolId / details / durationMs``.
That is the MCP-recommended shape, and the reason it changed is concrete:
the refusal text lived in JSON-RPC ``error.data.details``, where Claude Code
shows only ``error.message`` — "Tool execution failed" — so an agent retried
blind, failed the same way and reported the tool as broken.

``call_tool`` raises :class:`McpToolError` for those, so a test reading "this
call must fail" still says that; the exception carries ``.text`` (what the
model sees) and ``.data`` (the structured detail).

The MET-450 stdio readline cap is now a 16 MiB default (env-tunable
via ``METAFORGE_MCP_MAX_LINE_BYTES``); see
``tests/unit/test_mcp_stdio_max_line_bytes.py`` for the regression
guard. The HTTP transport here doesn't have a body-size cap of its
own (uvicorn defaults to ``--limit-max-requests`` style controls), so
the oversize-payload test in this file stays a documented skip — the
real cap is exercised on the stdio side.
"""

from __future__ import annotations

import httpx
import pytest

from ._helpers import MCP_PATH, McpRpcError, McpToolError, call_tool, rpc

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
    with pytest.raises(McpToolError) as exc_info:
        await call_tool(mcp_client, "twin.get_node", {})
    # FORGE-419: the reason reaches the model, which is the whole point.
    assert "node_id" in exc_info.value.text.lower()
    assert "twin.get_node" in exc_info.value.text
    data = exc_info.value.data
    assert data.get("toolId") == "twin.get_node"
    assert "node_id" in str(data.get("details", "")).lower()


async def test_invalid_uuid_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``twin.get_node`` with a non-UUID node_id surfaces structured data."""
    with pytest.raises(McpToolError) as exc_info:
        await call_tool(mcp_client, "twin.get_node", {"node_id": "not-a-uuid"})
    assert "uuid" in exc_info.value.text.lower()


async def test_invalid_enum_value_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``cadquery.create_parametric`` with an unknown ``shape_type`` enum errors out."""
    with pytest.raises(McpToolError) as exc_info:
        await call_tool(
            mcp_client,
            "cadquery.create_parametric",
            {
                "shape_type": "not-a-real-shape",
                "parameters": {"length": 10},
                "output_path": "/tmp/x.step",
            },
        )
    assert exc_info.value.data.get("toolId") == "cadquery.create_parametric"
    assert "shape" in exc_info.value.text.lower()


async def test_mutating_cypher_returns_minus_32001(
    mcp_client: httpx.AsyncClient,
) -> None:
    """``twin.query_cypher`` is read-only by default; mutations surface a clean error."""
    with pytest.raises(McpToolError) as exc_info:
        await call_tool(
            mcp_client,
            "twin.query_cypher",
            {"cypher": "CREATE (x:Thing {a: 1}) RETURN x"},
        )
    text = exc_info.value.text.lower()
    assert "mutating" in text or "read-only" in text


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

    What this test is really about: the *dispatcher* must not fail. It used to
    prove that by asserting a -32001 came back from the handler rather than a
    -32600 from the request layer. Since FORGE-419 the handler's refusal is an
    ``isError`` result instead, so the assertion moves with it -- the thing
    being guarded (dispatch succeeded, the tool got to run and reject on its
    own terms) is unchanged.
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
    # The dispatcher did not fail: there is no JSON-RPC error envelope, and
    # the handler ran and rejected on its own terms.
    assert "error" not in body, body
    result = body["result"]
    assert result["isError"] is True
    assert result["_meta"]["error"]["toolId"] == "twin.get_node"
    assert "node_id" in result["content"][0]["text"].lower()
