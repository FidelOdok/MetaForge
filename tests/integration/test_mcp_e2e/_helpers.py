"""Shared helpers for the MCP e2e suite (MET-477 follow-up).

Tests use these three primitives instead of touching ``httpx`` directly
so the suite stays uniform: every test sends the same JSON-RPC envelope
shape and unwraps tool results through the same path.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

MCP_PATH = "/mcp"


class McpRpcError(RuntimeError):
    """Raised when an RPC call returns a JSON-RPC error envelope."""

    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data or {}


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    rpc_id: int = 1,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST one JSON-RPC request, return the parsed ``result`` envelope.

    Raises :class:`McpRpcError` when the response carries a JSON-RPC
    ``error`` field. Network failures propagate as ``httpx`` exceptions
    so tests can mark themselves ``xfail`` on transport issues.
    """
    req = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
    response = await client.post(MCP_PATH, json=req, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        err = body["error"]
        raise McpRpcError(
            code=int(err.get("code", -32000)),
            message=str(err.get("message", "")),
            data=err.get("data"),
        )
    return body.get("result", {})


async def call_tool(
    client: httpx.AsyncClient,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    rpc_id: int = 1,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Convenience wrapper: ``tools/call`` + auto-unwrap the inner JSON.

    MCP wraps every tool result in ``result.content = [{type:"text",
    text: <serialized JSON>}]``. This helper unpacks that double envelope
    so tests can assert on the actual tool payload directly.
    """
    result = await rpc(
        client,
        "tools/call",
        {"name": tool_name, "arguments": arguments or {}},
        rpc_id=rpc_id,
        timeout=timeout,
    )
    return parse_tool_result(result)


def parse_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the ``content[0].text`` JSON string MCP tools return.

    Falls through to the raw ``result`` when the shape isn't the
    expected wrapped form — keeps tests honest when a tool deviates.
    """
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw_text": text}
    return result
