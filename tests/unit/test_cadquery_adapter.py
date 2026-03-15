"""Unit tests for the CadQuery tool adapter."""

from __future__ import annotations

import json

import pytest

from tool_registry.tools.cadquery.adapter import CadqueryServer
from tool_registry.tools.cadquery.config import CadqueryConfig

# --- Config tests ---


class TestCadqueryConfig:
    """Tests for CadqueryConfig defaults and validation."""

    def test_defaults(self):
        config = CadqueryConfig()
        assert config.work_dir == "/tmp/cadquery"
        assert config.max_operation_time == 300
        assert config.max_memory_mb == 2048
        assert config.max_script_lines == 200
        assert config.sandbox_enabled is True
        assert "step" in config.supported_export_formats
        assert "svg" in config.supported_export_formats

    def test_custom_values(self):
        config = CadqueryConfig(
            work_dir="/custom",
            max_operation_time=120,
            max_script_lines=100,
        )
        assert config.work_dir == "/custom"
        assert config.max_operation_time == 120
        assert config.max_script_lines == 100


# --- Server registration tests ---


class TestCadqueryServer:
    """Tests for CadqueryServer tool registration."""

    def test_registers_seven_tools(self):
        server = CadqueryServer()
        assert len(server.tool_ids) == 7

    def test_tool_ids_correct(self):
        server = CadqueryServer()
        expected = {
            "cadquery.create_parametric",
            "cadquery.boolean_operation",
            "cadquery.get_properties",
            "cadquery.export_geometry",
            "cadquery.execute_script",
            "cadquery.create_assembly",
            "cadquery.generate_enclosure",
        }
        assert set(server.tool_ids) == expected

    def test_adapter_id_and_version(self):
        server = CadqueryServer()
        assert server.adapter_id == "cadquery"
        assert server.version == "0.1.0"

    def test_custom_config(self):
        config = CadqueryConfig(work_dir="/custom", max_script_lines=50)
        server = CadqueryServer(config=config)
        assert server.config.work_dir == "/custom"
        assert server.config.max_script_lines == 50


# --- Handler argument validation tests ---


class TestCreateParametric:
    """Tests for cadquery.create_parametric handler validation."""

    async def test_missing_shape_type(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="shape_type is required"):
            await server.create_parametric({"parameters": {}, "output_path": "/out.step"})

    async def test_unsupported_shape_type(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="Unsupported shape type"):
            await server.create_parametric({
                "shape_type": "gearbox",
                "parameters": {"width": 10},
                "output_path": "/out.step",
            })

    async def test_missing_parameters(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="parameters is required"):
            await server.create_parametric({
                "shape_type": "box",
                "parameters": {},
                "output_path": "/out.step",
            })

    async def test_missing_output_path(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="output_path is required"):
            await server.create_parametric({
                "shape_type": "box",
                "parameters": {"length": 10},
            })


class TestBooleanOperation:
    """Tests for cadquery.boolean_operation handler validation."""

    async def test_missing_input_a(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="input_file_a is required"):
            await server.boolean_operation({
                "input_file_b": "b.step",
                "operation": "union",
            })

    async def test_unsupported_operation(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="Unsupported boolean operation"):
            await server.boolean_operation({
                "input_file_a": "a.step",
                "input_file_b": "b.step",
                "operation": "xor",
            })


class TestGetProperties:
    """Tests for cadquery.get_properties handler validation."""

    async def test_missing_input_file(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="input_file is required"):
            await server.get_properties({})


class TestExportGeometry:
    """Tests for cadquery.export_geometry handler validation."""

    async def test_missing_input_file(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="input_file is required"):
            await server.export_geometry({"output_format": "stl"})

    async def test_unsupported_format(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="Unsupported export format"):
            await server.export_geometry({
                "input_file": "model.step",
                "output_format": "dwg",
            })


class TestExecuteScript:
    """Tests for cadquery.execute_script handler validation."""

    async def test_missing_script(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="script is required"):
            await server.execute_script({})


class TestCreateAssembly:
    """Tests for cadquery.create_assembly handler validation."""

    async def test_empty_parts(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="parts list is required"):
            await server.create_assembly({"parts": []})

    async def test_part_missing_name(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="missing 'name'"):
            await server.create_assembly({
                "parts": [{"file": "a.step"}],
            })

    async def test_part_missing_file(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="missing 'file'"):
            await server.create_assembly({
                "parts": [{"name": "base"}],
            })


class TestGenerateEnclosure:
    """Tests for cadquery.generate_enclosure handler validation."""

    async def test_zero_pcb_length(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="pcb_length must be positive"):
            await server.generate_enclosure({"pcb_length": 0, "pcb_width": 50})

    async def test_negative_pcb_width(self):
        server = CadqueryServer()
        with pytest.raises(ValueError, match="pcb_width must be positive"):
            await server.generate_enclosure({"pcb_length": 80, "pcb_width": -5})


# --- JSON-RPC integration tests ---


class TestJsonRpcIntegration:
    """Tests for JSON-RPC protocol handling."""

    async def test_tool_list(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tool/list",
            "params": {},
        })

        response = json.loads(await server.handle_request(request))

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == "1"
        tools = response["result"]["tools"]
        assert len(tools) == 7

    async def test_tool_list_filter_by_capability(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tool/list",
            "params": {"capability": "cad_generation"},
        })

        response = json.loads(await server.handle_request(request))

        tools = response["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "cadquery.create_parametric"

    async def test_health_check(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "3",
            "method": "health/check",
            "params": {},
        })

        response = json.loads(await server.handle_request(request))

        result = response["result"]
        assert result["adapter_id"] == "cadquery"
        assert result["status"] == "healthy"
        assert result["tools_available"] == 7

    async def test_unknown_method(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "4",
            "method": "unknown/method",
            "params": {},
        })

        response = json.loads(await server.handle_request(request))

        assert "error" in response
        assert response["error"]["code"] == -32601

    async def test_invalid_json(self):
        server = CadqueryServer()

        response = json.loads(await server.handle_request("not json"))

        assert "error" in response
        assert response["error"]["code"] == -32600

    async def test_tool_call_unknown_tool(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "5",
            "method": "tool/call",
            "params": {"tool_id": "cadquery.nonexistent", "arguments": {}},
        })

        response = json.loads(await server.handle_request(request))

        assert "error" in response

    async def test_tool_call_validation_error(self):
        server = CadqueryServer()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "6",
            "method": "tool/call",
            "params": {
                "tool_id": "cadquery.create_parametric",
                "arguments": {},  # Missing required args
            },
        })

        response = json.loads(await server.handle_request(request))

        assert "error" in response
        assert response["error"]["code"] == -32001
