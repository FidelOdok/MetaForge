"""Tests for MCP tool server template (tool_registry.mcp_server)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tool_registry.mcp_server.handlers import (
    ResourceLimits,
    ToolHandlerError,
    ToolManifest,
    ToolNotFoundError,
    handle_health_check,
    handle_tool_call,
    handle_tool_list,
    make_error,
    make_success,
)
from tool_registry.mcp_server.server import McpToolServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_manifest(
    tool_id: str = "calc.run_fea",
    adapter_id: str = "calculix",
    capability: str = "fea_analysis",
    **overrides: Any,
) -> ToolManifest:
    defaults: dict[str, Any] = {
        "tool_id": tool_id,
        "adapter_id": adapter_id,
        "name": "Run FEA",
        "description": "Run finite element analysis",
        "capability": capability,
    }
    defaults.update(overrides)
    return ToolManifest(**defaults)


async def _echo_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """Simple handler that echoes arguments back."""
    return {"echo": arguments}


async def _failing_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handler that always raises."""
    raise RuntimeError("something went wrong")


@pytest.fixture()
def manifest() -> ToolManifest:
    return _make_manifest()


@pytest.fixture()
def server() -> McpToolServer:
    return McpToolServer(adapter_id="calculix", version="0.1.0")


@pytest.fixture()
def server_with_tools() -> McpToolServer:
    srv = McpToolServer(adapter_id="calculix", version="0.1.0")
    srv.register_tool(
        manifest=_make_manifest(tool_id="calc.run_fea", capability="fea_analysis"),
        handler=_echo_handler,
    )
    srv.register_tool(
        manifest=_make_manifest(
            tool_id="calc.mesh",
            capability="meshing",
            name="Mesh Model",
            description="Generate mesh",
        ),
        handler=_echo_handler,
    )
    return srv


# ---------------------------------------------------------------------------
# TestToolManifest
# ---------------------------------------------------------------------------


class TestToolManifest:
    def test_manifest_creation(self) -> None:
        m = _make_manifest(
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert m.tool_id == "calc.run_fea"
        assert m.adapter_id == "calculix"
        assert m.capability == "fea_analysis"
        assert m.input_schema == {"type": "object"}
        assert m.output_schema == {"type": "object"}

    def test_manifest_defaults(self) -> None:
        m = _make_manifest()
        assert m.phase == 1
        assert m.input_schema == {}
        assert m.output_schema == {}
        assert m.resource_limits.max_memory_mb == 1024
        assert m.resource_limits.max_cpu_seconds == 300
        assert m.resource_limits.max_disk_mb == 256


class TestResourceLimits:
    def test_custom_limits(self) -> None:
        limits = ResourceLimits(max_memory_mb=2048, max_cpu_seconds=600, max_disk_mb=512)
        assert limits.max_memory_mb == 2048
        assert limits.max_cpu_seconds == 600
        assert limits.max_disk_mb == 512


# ---------------------------------------------------------------------------
# TestToolRegistration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_register_tool(self, server: McpToolServer) -> None:
        m = _make_manifest()
        server.register_tool(manifest=m, handler=_echo_handler)
        assert "calc.run_fea" in server.tool_ids

    def test_register_duplicate_raises(self, server: McpToolServer) -> None:
        m = _make_manifest()
        server.register_tool(manifest=m, handler=_echo_handler)
        with pytest.raises(ValueError, match="already registered"):
            server.register_tool(manifest=m, handler=_echo_handler)


# ---------------------------------------------------------------------------
# TestHandleToolList
# ---------------------------------------------------------------------------


class TestHandleToolList:
    async def test_list_all_tools(self, server_with_tools: McpToolServer) -> None:
        result = await handle_tool_list(server_with_tools._tools, {})
        assert len(result["tools"]) == 2

    async def test_list_filtered_by_capability(
        self, server_with_tools: McpToolServer
    ) -> None:
        result = await handle_tool_list(
            server_with_tools._tools, {"capability": "meshing"}
        )
        assert len(result["tools"]) == 1
        assert result["tools"][0]["tool_id"] == "calc.mesh"

    async def test_list_empty(self, server: McpToolServer) -> None:
        result = await handle_tool_list(server._tools, {})
        assert result["tools"] == []


# ---------------------------------------------------------------------------
# TestHandleToolCall
# ---------------------------------------------------------------------------


class TestHandleToolCall:
    async def test_call_registered_tool(
        self, server_with_tools: McpToolServer
    ) -> None:
        result = await handle_tool_call(
            server_with_tools._tools,
            {"tool_id": "calc.run_fea", "arguments": {"load": 100}},
        )
        assert result["tool_id"] == "calc.run_fea"
        assert result["status"] == "success"
        assert result["data"] == {"echo": {"load": 100}}
        assert "duration_ms" in result

    async def test_call_unknown_tool_raises(self, server: McpToolServer) -> None:
        with pytest.raises(ToolNotFoundError, match="no_such_tool"):
            await handle_tool_call(
                server._tools, {"tool_id": "no_such_tool", "arguments": {}}
            )

    async def test_call_handler_error_raises(self) -> None:
        srv = McpToolServer(adapter_id="test", version="0.0.1")
        srv.register_tool(manifest=_make_manifest(), handler=_failing_handler)
        with pytest.raises(ToolHandlerError, match="something went wrong"):
            await handle_tool_call(
                srv._tools, {"tool_id": "calc.run_fea", "arguments": {}}
            )


# ---------------------------------------------------------------------------
# TestHandleHealthCheck
# ---------------------------------------------------------------------------


class TestHandleHealthCheck:
    async def test_health_check_response(
        self, server_with_tools: McpToolServer
    ) -> None:
        result = await handle_health_check(
            adapter_id=server_with_tools.adapter_id,
            version=server_with_tools.version,
            tools=server_with_tools._tools,
            start_time=server_with_tools._start_time,
        )
        assert result["adapter_id"] == "calculix"
        assert result["status"] == "healthy"
        assert result["version"] == "0.1.0"
        assert result["tools_available"] == 2
        assert isinstance(result["uptime_seconds"], float)


# ---------------------------------------------------------------------------
# TestJsonRpcHelpers
# ---------------------------------------------------------------------------


class TestJsonRpcHelpers:
    def test_make_success(self) -> None:
        resp = make_success("req-1", {"ok": True})
        assert resp == {
            "jsonrpc": "2.0",
            "id": "req-1",
            "result": {"ok": True},
        }

    def test_make_error_without_data(self) -> None:
        resp = make_error("req-2", -32600, "Bad request")
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Bad request"
        assert "data" not in resp["error"]

    def test_make_error_with_data(self) -> None:
        resp = make_error("req-3", -32001, "Fail", {"detail": "x"})
        assert resp["error"]["data"] == {"detail": "x"}


# ---------------------------------------------------------------------------
# TestMcpToolServer (end-to-end via handle_request)
# ---------------------------------------------------------------------------


class TestMcpToolServer:
    async def test_handle_valid_tool_list_request(
        self, server_with_tools: McpToolServer
    ) -> None:
        request = json.dumps(
            {"jsonrpc": "2.0", "id": "1", "method": "tool/list", "params": {}}
        )
        raw_response = await server_with_tools.handle_request(request)
        result = json.loads(raw_response)
        assert "result" in result
        assert "tools" in result["result"]
        assert len(result["result"]["tools"]) == 2

    async def test_handle_valid_tool_call_request(
        self, server_with_tools: McpToolServer
    ) -> None:
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tool/call",
                "params": {
                    "tool_id": "calc.run_fea",
                    "arguments": {"mesh_size": 0.5},
                },
            }
        )
        raw_response = await server_with_tools.handle_request(request)
        result = json.loads(raw_response)
        assert result["id"] == "2"
        assert result["result"]["status"] == "success"
        assert result["result"]["data"] == {"echo": {"mesh_size": 0.5}}

    async def test_handle_health_check_request(
        self, server_with_tools: McpToolServer
    ) -> None:
        request = json.dumps(
            {"jsonrpc": "2.0", "id": "3", "method": "health/check", "params": {}}
        )
        raw_response = await server_with_tools.handle_request(request)
        result = json.loads(raw_response)
        assert result["result"]["status"] == "healthy"
        assert result["result"]["adapter_id"] == "calculix"

    async def test_handle_unknown_method(self, server: McpToolServer) -> None:
        request = json.dumps(
            {"jsonrpc": "2.0", "id": "4", "method": "unknown/method", "params": {}}
        )
        raw_response = await server.handle_request(request)
        result = json.loads(raw_response)
        assert "error" in result
        assert result["error"]["code"] == -32601
        assert "Unknown method" in result["error"]["message"]

    async def test_handle_invalid_json(self, server: McpToolServer) -> None:
        raw_response = await server.handle_request("not-valid-json{{{")
        result = json.loads(raw_response)
        assert "error" in result
        assert result["error"]["code"] == -32600
        assert "Invalid JSON" in result["error"]["message"]

    async def test_handle_invalid_jsonrpc_version(
        self, server: McpToolServer
    ) -> None:
        request = json.dumps(
            {"jsonrpc": "1.0", "id": "5", "method": "tool/list", "params": {}}
        )
        raw_response = await server.handle_request(request)
        result = json.loads(raw_response)
        assert "error" in result
        assert result["error"]["code"] == -32600
        assert "JSON-RPC 2.0" in result["error"]["message"]

    async def test_tool_ids_property(self, server_with_tools: McpToolServer) -> None:
        ids = server_with_tools.tool_ids
        assert sorted(ids) == ["calc.mesh", "calc.run_fea"]

    async def test_handle_tool_call_not_found(self, server: McpToolServer) -> None:
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "6",
                "method": "tool/call",
                "params": {"tool_id": "nonexistent", "arguments": {}},
            }
        )
        raw_response = await server.handle_request(request)
        result = json.loads(raw_response)
        assert "error" in result
        assert result["error"]["code"] == -32601
        assert result["error"]["data"]["tool_id"] == "nonexistent"

    async def test_handle_tool_call_handler_error(self) -> None:
        srv = McpToolServer(adapter_id="test", version="0.0.1")
        srv.register_tool(manifest=_make_manifest(), handler=_failing_handler)
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "7",
                "method": "tool/call",
                "params": {"tool_id": "calc.run_fea", "arguments": {}},
            }
        )
        raw_response = await srv.handle_request(request)
        result = json.loads(raw_response)
        assert "error" in result
        assert result["error"]["code"] == -32001
        assert result["error"]["data"]["error_type"] == "TOOL_EXECUTION_ERROR"
        assert result["error"]["data"]["tool_id"] == "calc.run_fea"
