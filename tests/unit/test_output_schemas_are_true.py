"""Declared output schemas are valid, and real results are checked against them (R10).

``tools/list`` does not publish ``outputSchema`` yet. A client that validates
would reject a result its schema does not describe, so before the schemas are
published they must be valid JSON Schema, and every live result is compared
with its tool's schema: a difference is logged and counted, never blocking.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

_SCHEMA = {
    "type": "object",
    "properties": {"rows": {"type": "array"}, "count": {"type": "integer"}},
    "required": ["rows"],
}


def _required_are_declared(node: Any, path: str = "") -> list[str]:
    """``required`` names a property the schema never declares."""
    found: list[str] = []
    if isinstance(node, dict):
        props = node.get("properties")
        for name in node.get("required") or []:
            if props is not None and name not in props:
                found.append(f"{path or '/'}: required {name!r} is not a property")
        for key, value in node.items():
            found += _required_are_declared(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _required_are_declared(value, f"{path}/{i}")
    return found


async def test_every_declared_output_schema_is_valid_and_an_object() -> None:
    registry = await bootstrap_tool_registry()
    problems: list[str] = []
    for tool in registry.list_tools():
        schema = getattr(tool, "output_schema", None)
        if not schema:
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001 -- collected and reported below
            problems.append(f"{tool.tool_id}: not valid JSON Schema: {exc}")
            continue
        if schema.get("type", "object") != "object":
            problems.append(f"{tool.tool_id}: structuredContent must be an object")
        problems += [f"{tool.tool_id} {p}" for p in _required_are_declared(schema)]
    assert problems == []


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="twin.list_rows",
                adapter_id="twin",
                name="rows",
                description="stub",
                capability="test",
                output_schema=_SCHEMA,
            ),
            self._handler,
        )

    async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"rows": [], "count": 0} if args.get("ok") else {"count": "three"}


class _Metrics:
    def __init__(self) -> None:
        self.mismatches: list[str] = []

    def record_mcp_tool_call(self, *_: Any, **__: Any) -> None:
        pass

    def record_mcp_output_schema_mismatch(self, tool_id: str) -> None:
        self.mismatches.append(tool_id)


async def _call(server: UnifiedMcpServer, args: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "twin.list_rows", "arguments": args},
            }
        )
    )
    return dict(json.loads(raw))


@pytest.mark.parametrize(("ok", "counted"), [(True, []), (False, ["twin.list_rows"])])
async def test_a_result_off_its_schema_is_counted_and_still_returned(
    ok: bool, counted: list[str]
) -> None:
    metrics = _Metrics()
    server = UnifiedMcpServer([_Adapter()], caller=Caller.LOCAL, exempt_local_writes=True)
    server._metrics = metrics  # type: ignore[assignment]
    reply = await _call(server, {"ok": ok})
    assert "error" not in reply and not reply["result"].get("isError")
    assert metrics.mismatches == counted
