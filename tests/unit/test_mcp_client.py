"""Tests for mcp_core: schemas, protocol, and client."""

from __future__ import annotations

import json

import pytest

from mcp_core.client import InMemoryTransport, McpClient
from mcp_core.protocol import (
    INVALID_REQUEST,
    TOOL_EXECUTION_ERROR,
    TOOL_TIMEOUT,
    TOOL_UNAVAILABLE,
    McpError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolUnavailableError,
    create_error_response,
    create_request,
    create_success_response,
    deserialize_request,
    deserialize_response,
    serialize_message,
)
from mcp_core.schemas import (
    HealthStatus,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    ToolCallRequest,
    ToolCallResult,
    ToolManifest,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestMcpSchemas:
    """Tests for Pydantic message schemas."""

    def test_tool_call_request_defaults(self) -> None:
        req = ToolCallRequest(tool_id="calculix.run_fea")
        assert req.tool_id == "calculix.run_fea"
        assert req.arguments == {}
        assert req.timeout_seconds == 120
        assert req.trace_id is None

    def test_tool_call_request_validation(self) -> None:
        """timeout_seconds must be between 1 and 3600."""
        with pytest.raises(Exception):  # noqa: B017
            ToolCallRequest(tool_id="x", timeout_seconds=0)
        with pytest.raises(Exception):  # noqa: B017
            ToolCallRequest(tool_id="x", timeout_seconds=3601)

    def test_tool_manifest_creation(self) -> None:
        manifest = ToolManifest(
            tool_id="calculix.run_fea",
            adapter_id="calculix",
            name="Run FEA",
            description="Finite element analysis",
            capability="fea",
        )
        assert manifest.tool_id == "calculix.run_fea"
        assert manifest.adapter_id == "calculix"
        assert manifest.phase == 1
        assert manifest.resource_limits.max_memory_mb == 1024
        assert manifest.input_schema == {}

    def test_json_rpc_request_serialization(self) -> None:
        req = JsonRpcRequest(id="abc-123", method="tool/call", params={"x": 1})
        data = json.loads(req.model_dump_json())
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "abc-123"
        assert data["method"] == "tool/call"
        assert data["params"] == {"x": 1}

    def test_health_status_optional_fields(self) -> None:
        status = HealthStatus(
            adapter_id="calculix",
            status="healthy",
            version="1.0.0",
            tools_available=3,
            uptime_seconds=1234.5,
        )
        assert status.last_invocation is None
        assert status.uptime_seconds == 1234.5

    def test_tool_call_result_defaults(self) -> None:
        result = ToolCallResult(tool_id="x", status="success")
        assert result.data == {}
        assert result.duration_ms == 0
        assert result.output_files == []

    def test_json_rpc_success_response(self) -> None:
        resp = JsonRpcSuccessResponse(id="r1", result={"status": "ok"})
        assert resp.jsonrpc == "2.0"
        assert resp.result == {"status": "ok"}

    def test_json_rpc_error_response(self) -> None:
        resp = JsonRpcErrorResponse(
            id="r1",
            error={"code": -32001, "message": "fail"},
        )
        assert resp.error["code"] == -32001


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class TestMcpProtocol:
    """Tests for the wire protocol layer."""

    def test_create_request(self) -> None:
        req = create_request("tool/call", {"tool_id": "x"}, request_id="r1")
        assert req.method == "tool/call"
        assert req.id == "r1"
        assert req.params == {"tool_id": "x"}
        assert req.jsonrpc == "2.0"

    def test_create_request_auto_id(self) -> None:
        req = create_request("tool/list")
        assert len(req.id) > 0  # UUID generated

    def test_create_success_response(self) -> None:
        resp = create_success_response("r1", {"status": "ok"})
        assert resp.id == "r1"
        assert resp.result == {"status": "ok"}
        assert resp.jsonrpc == "2.0"

    def test_create_error_response(self) -> None:
        resp = create_error_response("r1", -32001, "Tool failed", {"detail": "x"})
        assert resp.id == "r1"
        assert resp.error["code"] == -32001
        assert resp.error["message"] == "Tool failed"
        assert resp.error["data"] == {"detail": "x"}

    def test_create_error_response_no_data(self) -> None:
        resp = create_error_response("r1", -32600, "Bad request")
        assert "data" not in resp.error

    def test_serialize_and_deserialize_request(self) -> None:
        original = create_request("tool/call", {"tool_id": "fea"}, request_id="r1")
        raw = serialize_message(original)
        restored = deserialize_request(raw)
        assert restored.id == "r1"
        assert restored.method == "tool/call"
        assert restored.params == {"tool_id": "fea"}

    def test_deserialize_success_response(self) -> None:
        resp = create_success_response("r1", {"value": 42})
        raw = serialize_message(resp)
        parsed = deserialize_response(raw)
        assert isinstance(parsed, JsonRpcSuccessResponse)
        assert parsed.result["value"] == 42

    def test_deserialize_error_response(self) -> None:
        resp = create_error_response("r1", -32001, "fail")
        raw = serialize_message(resp)
        parsed = deserialize_response(raw)
        assert isinstance(parsed, JsonRpcErrorResponse)
        assert parsed.error["code"] == -32001

    def test_deserialize_invalid_json_raises(self) -> None:
        with pytest.raises(McpError) as exc_info:
            deserialize_request("not json{{{")
        assert exc_info.value.code == INVALID_REQUEST

    def test_deserialize_request_not_jsonrpc(self) -> None:
        with pytest.raises(McpError) as exc_info:
            deserialize_request('{"id": "1", "method": "x"}')
        assert exc_info.value.code == INVALID_REQUEST

    def test_deserialize_response_invalid_json_raises(self) -> None:
        with pytest.raises(McpError):
            deserialize_response("<<<not json>>>")

    def test_tool_execution_error(self) -> None:
        err = ToolExecutionError("calc.fea", "Mesh failed", duration_ms=123.4)
        assert err.code == TOOL_EXECUTION_ERROR
        assert err.message == "Tool execution failed"
        assert err.data is not None
        assert err.data.tool_id == "calc.fea"
        assert err.data.details == "Mesh failed"
        assert err.data.duration_ms == 123.4

    def test_tool_timeout_error(self) -> None:
        err = ToolTimeoutError("calc.fea", timeout_seconds=60)
        assert err.code == TOOL_TIMEOUT
        assert "60s" in err.message
        assert err.data is not None
        assert err.data.tool_id == "calc.fea"

    def test_tool_unavailable_error(self) -> None:
        err = ToolUnavailableError("calc.fea")
        assert err.code == TOOL_UNAVAILABLE
        assert err.data is not None
        assert err.data.tool_id == "calc.fea"
        assert "unavailable" in err.message.lower()


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


def _make_manifest(
    tool_id: str = "calculix.run_fea",
    adapter_id: str = "calculix",
) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name="Run FEA",
        description="Run FEA analysis",
        capability="fea",
    )


def _success_response_json(
    request_id: str = "ignored",
    result: dict[str, object] | None = None,
) -> str:
    resp = JsonRpcSuccessResponse(
        id=request_id,
        result=result or {"status": "success", "data": {"max_stress": 150.0}},
    )
    return resp.model_dump_json()


def _error_response_json(request_id: str = "ignored") -> str:
    resp = JsonRpcErrorResponse(
        id=request_id,
        error={"code": -32001, "message": "Tool execution failed"},
    )
    return resp.model_dump_json()


class TestMcpClient:
    """Tests for the MCP client."""

    async def test_connect_and_disconnect(self) -> None:
        client = McpClient()
        transport = InMemoryTransport()

        await client.connect("calculix", transport)
        assert transport.is_connected()

        await client.disconnect("calculix")
        assert not transport.is_connected()

    async def test_disconnect_unknown_adapter(self) -> None:
        """Disconnecting an unknown adapter should not raise."""
        client = McpClient()
        await client.disconnect("nonexistent")

    async def test_call_tool_success(self) -> None:
        client = McpClient()
        transport = InMemoryTransport()
        await client.connect("calculix", transport)
        client.register_manifest(_make_manifest())

        transport.queue_response(_success_response_json())

        result = await client.call_tool(
            ToolCallRequest(
                tool_id="calculix.run_fea",
                arguments={"mesh_file": "part.inp"},
            )
        )

        assert isinstance(result, ToolCallResult)
        assert result.tool_id == "calculix.run_fea"
        assert result.status == "success"
        assert result.data == {"max_stress": 150.0}
        assert result.duration_ms > 0

    async def test_call_tool_unknown_tool_raises(self) -> None:
        client = McpClient()
        with pytest.raises(ToolUnavailableError):
            await client.call_tool(ToolCallRequest(tool_id="unknown.tool"))

    async def test_call_tool_disconnected_raises(self) -> None:
        """Calling a tool on a disconnected transport should raise."""
        client = McpClient()
        transport = InMemoryTransport()
        await client.connect("calculix", transport)
        client.register_manifest(_make_manifest())
        await client.disconnect("calculix")

        # Re-register manifest to simulate stale state with the tool still
        # mapped but the transport gone.
        client.register_manifest(_make_manifest())

        with pytest.raises(ToolUnavailableError):
            await client.call_tool(ToolCallRequest(tool_id="calculix.run_fea"))

    async def test_call_tool_error_response_raises(self) -> None:
        client = McpClient()
        transport = InMemoryTransport()
        await client.connect("calculix", transport)
        client.register_manifest(_make_manifest())
        transport.queue_response(_error_response_json())

        with pytest.raises(ToolExecutionError) as exc_info:
            await client.call_tool(ToolCallRequest(tool_id="calculix.run_fea"))
        assert exc_info.value.code == TOOL_EXECUTION_ERROR

    async def test_list_tools(self) -> None:
        client = McpClient()
        m1 = _make_manifest("calculix.run_fea", "calculix")
        m2 = _make_manifest("freecad.export_step", "freecad")
        client.register_manifest(m1)
        client.register_manifest(m2)

        tools = await client.list_tools()
        assert len(tools) == 2
        tool_ids = {t.tool_id for t in tools}
        assert tool_ids == {"calculix.run_fea", "freecad.export_step"}

    async def test_list_tools_filtered_by_adapter(self) -> None:
        client = McpClient()
        m1 = _make_manifest("calculix.run_fea", "calculix")
        m2 = _make_manifest("freecad.export_step", "freecad")
        client.register_manifest(m1)
        client.register_manifest(m2)

        tools = await client.list_tools(adapter_id="calculix")
        assert len(tools) == 1
        assert tools[0].tool_id == "calculix.run_fea"

    async def test_health_check_healthy(self) -> None:
        client = McpClient()
        transport = InMemoryTransport()
        await client.connect("calculix", transport)

        health_data = {
            "adapter_id": "calculix",
            "status": "healthy",
            "version": "1.0.0",
            "tools_available": 3,
            "uptime_seconds": 600.0,
        }
        transport.queue_response(_success_response_json(result=health_data))

        status = await client.health_check("calculix")
        assert isinstance(status, HealthStatus)
        assert status.status == "healthy"
        assert status.version == "1.0.0"
        assert status.tools_available == 3

    async def test_health_check_disconnected(self) -> None:
        client = McpClient()
        status = await client.health_check("calculix")
        assert status.status == "unhealthy"
        assert status.version == "unknown"
        assert status.tools_available == 0

    async def test_health_check_error_response(self) -> None:
        """If the adapter returns an error, health should be unhealthy."""
        client = McpClient()
        transport = InMemoryTransport()
        await client.connect("calculix", transport)
        transport.queue_response(_error_response_json())

        status = await client.health_check("calculix")
        assert status.status == "unhealthy"

    async def test_in_memory_transport_no_response_raises(self) -> None:
        transport = InMemoryTransport()
        await transport.connect()
        with pytest.raises(McpError):
            await transport.send("hello")

    async def test_in_memory_transport_records_requests(self) -> None:
        transport = InMemoryTransport()
        await transport.connect()
        transport.queue_response('{"jsonrpc":"2.0","id":"1","result":{}}')
        await transport.send("test-message")
        assert transport.requests == ["test-message"]
