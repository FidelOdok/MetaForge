"""Tests for the FreeCAD MCP tool adapter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.freecad.adapter import FreecadServer
from tool_registry.tools.freecad.config import FreecadConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> FreecadServer:
    """Bare FreeCAD server (no mocks on internal methods)."""
    return FreecadServer()


@pytest.fixture()
def server_with_mocks() -> FreecadServer:
    """Server with mocked internal methods for testing."""
    s = FreecadServer()
    s._execute_export = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/freecad/bracket.stl",
            "file_size_bytes": 245760,
            "format": "stl",
        }
    )
    s._execute_meshing = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "mesh_file": "/tmp/freecad/bracket.inp",
            "num_nodes": 12500,
            "num_elements": 48000,
            "element_types": ["C3D10", "C3D4"],
            "quality_metrics": {
                "min_angle": 18.5,
                "max_aspect_ratio": 4.2,
                "avg_quality": 0.87,
            },
        }
    )
    s._execute_boolean = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/freecad/body_union.step",
            "operation": "union",
            "result_volume": 1250.5,
            "result_area": 890.3,
        }
    )
    s._execute_analysis = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "file": "/models/bracket.step",
            "properties": {
                "volume": 1250.5,
                "surface_area": 890.3,
                "center_of_mass": {"x": 10.0, "y": 5.0, "z": 3.2},
                "bounding_box": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "min_z": 0.0,
                    "max_x": 20.0,
                    "max_y": 10.0,
                    "max_z": 6.4,
                },
            },
        }
    )
    return s


# ---------------------------------------------------------------------------
# TestFreecadConfig
# ---------------------------------------------------------------------------


class TestFreecadConfig:
    def test_default_config(self) -> None:
        cfg = FreecadConfig()
        assert cfg.freecad_binary == "freecadcmd"
        assert cfg.work_dir == "/tmp/freecad"
        assert cfg.max_operation_time == 300
        assert cfg.max_memory_mb == 2048
        assert cfg.supported_import_formats == ["step", "stp", "stl", "iges", "igs", "brep"]
        assert cfg.supported_export_formats == ["step", "stp", "stl", "obj", "brep"]
        assert cfg.default_mesh_algorithm == "netgen"

    def test_custom_config(self) -> None:
        cfg = FreecadConfig(
            freecad_binary="/usr/local/bin/freecadcmd",
            work_dir="/data/freecad",
            max_operation_time=120,
            max_memory_mb=4096,
            supported_import_formats=["step", "stl"],
            supported_export_formats=["step"],
            default_mesh_algorithm="gmsh",
        )
        assert cfg.freecad_binary == "/usr/local/bin/freecadcmd"
        assert cfg.work_dir == "/data/freecad"
        assert cfg.max_operation_time == 120
        assert cfg.max_memory_mb == 4096
        assert cfg.supported_import_formats == ["step", "stl"]
        assert cfg.supported_export_formats == ["step"]
        assert cfg.default_mesh_algorithm == "gmsh"

    def test_supported_formats(self) -> None:
        cfg = FreecadConfig()
        # Import should include STEP, STL, IGES, BREP
        assert "step" in cfg.supported_import_formats
        assert "stl" in cfg.supported_import_formats
        assert "iges" in cfg.supported_import_formats
        assert "brep" in cfg.supported_import_formats
        # Export should include STEP, STL, OBJ, BREP
        assert "step" in cfg.supported_export_formats
        assert "stl" in cfg.supported_export_formats
        assert "obj" in cfg.supported_export_formats
        assert "brep" in cfg.supported_export_formats


# ---------------------------------------------------------------------------
# TestFreecadServer
# ---------------------------------------------------------------------------


class TestFreecadServer:
    def test_server_adapter_id(self, server: FreecadServer) -> None:
        assert server.adapter_id == "freecad"

    def test_server_version(self, server: FreecadServer) -> None:
        assert server.version == "0.1.0"

    def test_registers_four_tools(self, server: FreecadServer) -> None:
        assert len(server.tool_ids) == 4

    def test_tool_ids(self, server: FreecadServer) -> None:
        expected = {
            "freecad.export_geometry",
            "freecad.generate_mesh",
            "freecad.boolean_operation",
            "freecad.get_properties",
        }
        assert set(server.tool_ids) == expected


# ---------------------------------------------------------------------------
# TestExportGeometry
# ---------------------------------------------------------------------------


class TestExportGeometry:
    async def test_export_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.export_geometry(
            {
                "input_file": "/models/bracket.step",
                "output_format": "stl",
                "output_path": "/tmp/freecad/bracket.stl",
            }
        )
        assert result["output_file"] == "/tmp/freecad/bracket.stl"
        assert result["file_size_bytes"] == 245760
        assert result["format"] == "stl"

    async def test_export_missing_input_file_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.export_geometry(
                {"input_file": "", "output_format": "stl"}
            )

    async def test_export_unsupported_format_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported export format"):
            await server_with_mocks.export_geometry(
                {"input_file": "/models/bracket.step", "output_format": "fbx"}
            )


# ---------------------------------------------------------------------------
# TestGenerateMesh
# ---------------------------------------------------------------------------


class TestGenerateMesh:
    async def test_generate_mesh_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.generate_mesh(
            {
                "input_file": "/models/bracket.step",
                "element_size": 0.5,
                "algorithm": "netgen",
                "output_format": "inp",
            }
        )
        assert result["mesh_file"] == "/tmp/freecad/bracket.inp"
        assert result["num_nodes"] == 12500
        assert result["num_elements"] == 48000
        assert result["element_types"] == ["C3D10", "C3D4"]
        assert result["quality_metrics"]["min_angle"] == 18.5
        assert result["quality_metrics"]["max_aspect_ratio"] == 4.2
        assert result["quality_metrics"]["avg_quality"] == 0.87

    async def test_generate_mesh_default_params(
        self, server_with_mocks: FreecadServer
    ) -> None:
        """generate_mesh uses default element_size, algorithm, and output_format."""
        result = await server_with_mocks.generate_mesh(
            {"input_file": "/models/bracket.step"}
        )
        assert result["num_nodes"] == 12500
        # Verify _execute_meshing was called with default values
        call_args = server_with_mocks._execute_meshing.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == 1.0  # default element_size
        assert call_args[0][2] == "netgen"  # default algorithm
        assert call_args[0][3] == "inp"  # default output_format

    async def test_generate_mesh_missing_input_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.generate_mesh({"input_file": ""})

    async def test_generate_mesh_unsupported_algorithm_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported meshing algorithm"):
            await server_with_mocks.generate_mesh(
                {"input_file": "/models/bracket.step", "algorithm": "tetgen"}
            )


# ---------------------------------------------------------------------------
# TestBooleanOperation
# ---------------------------------------------------------------------------


class TestBooleanOperation:
    async def test_boolean_union_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.boolean_operation(
            {
                "input_file_a": "/models/body.step",
                "input_file_b": "/models/flange.step",
                "operation": "union",
            }
        )
        assert result["output_file"] == "/tmp/freecad/body_union.step"
        assert result["operation"] == "union"
        assert result["result_volume"] == 1250.5
        assert result["result_area"] == 890.3

    async def test_boolean_subtract_success(self, server_with_mocks: FreecadServer) -> None:
        # Reconfigure mock for subtract
        server_with_mocks._execute_boolean = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "output_file": "/tmp/freecad/body_subtract.step",
                "operation": "subtract",
                "result_volume": 800.0,
                "result_area": 650.2,
            }
        )
        result = await server_with_mocks.boolean_operation(
            {
                "input_file_a": "/models/body.step",
                "input_file_b": "/models/hole.step",
                "operation": "subtract",
            }
        )
        assert result["operation"] == "subtract"
        assert result["result_volume"] == 800.0

    async def test_boolean_missing_files_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file_a is required"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "",
                    "input_file_b": "/models/flange.step",
                    "operation": "union",
                }
            )
        with pytest.raises(ValueError, match="input_file_b is required"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "/models/body.step",
                    "input_file_b": "",
                    "operation": "union",
                }
            )

    async def test_boolean_invalid_operation_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported boolean operation"):
            await server_with_mocks.boolean_operation(
                {
                    "input_file_a": "/models/body.step",
                    "input_file_b": "/models/flange.step",
                    "operation": "xor",
                }
            )


# ---------------------------------------------------------------------------
# TestGetProperties
# ---------------------------------------------------------------------------


class TestGetProperties:
    async def test_get_properties_success(self, server_with_mocks: FreecadServer) -> None:
        result = await server_with_mocks.get_properties(
            {
                "input_file": "/models/bracket.step",
                "properties": ["volume", "area", "center_of_mass", "bounding_box"],
            }
        )
        assert result["file"] == "/models/bracket.step"
        assert result["properties"]["volume"] == 1250.5
        assert result["properties"]["surface_area"] == 890.3
        assert result["properties"]["center_of_mass"]["x"] == 10.0
        assert result["properties"]["bounding_box"]["max_x"] == 20.0

    async def test_get_properties_default_fields(
        self, server_with_mocks: FreecadServer
    ) -> None:
        """get_properties uses default property list when not specified."""
        result = await server_with_mocks.get_properties(
            {"input_file": "/models/bracket.step"}
        )
        assert result["file"] == "/models/bracket.step"
        # Verify _execute_analysis was called with default properties
        call_args = server_with_mocks._execute_analysis.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == ["volume", "area", "center_of_mass", "bounding_box"]

    async def test_get_properties_missing_file_raises(
        self, server_with_mocks: FreecadServer
    ) -> None:
        with pytest.raises(ValueError, match="input_file is required"):
            await server_with_mocks.get_properties({"input_file": ""})


# ---------------------------------------------------------------------------
# TestUnmockedMethodsRaise
# ---------------------------------------------------------------------------


class TestUnmockedMethodsRaise:
    """Verify that calling internal methods without mocks raises NotImplementedError."""

    async def test_export_not_implemented(self, server: FreecadServer) -> None:
        with pytest.raises(NotImplementedError, match="freecadcmd binary"):
            await server._execute_export("/models/test.step", "stl", "/tmp/out.stl")

    async def test_mesh_not_implemented(self, server: FreecadServer) -> None:
        with pytest.raises(NotImplementedError, match="freecadcmd binary"):
            await server._execute_meshing("/models/test.step", 1.0, "netgen", "inp")

    async def test_boolean_not_implemented(self, server: FreecadServer) -> None:
        with pytest.raises(NotImplementedError, match="freecadcmd binary"):
            await server._execute_boolean(
                "/models/a.step", "/models/b.step", "union", "/tmp/out.step"
            )

    async def test_analysis_not_implemented(self, server: FreecadServer) -> None:
        with pytest.raises(NotImplementedError, match="freecadcmd binary"):
            await server._execute_analysis("/models/test.step", ["volume"])


# ---------------------------------------------------------------------------
# TestJsonRpcIntegration
# ---------------------------------------------------------------------------


def _make_jsonrpc(
    method: str,
    params: dict[str, Any] | None = None,
    request_id: str = "1",
) -> str:
    """Helper to build a JSON-RPC 2.0 request string."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    return json.dumps(msg)


class TestJsonRpcIntegration:
    async def test_tool_list_returns_four_tools(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert len(response["result"]["tools"]) == 4

    async def test_tool_call_export(self, server_with_mocks: FreecadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "freecad.export_geometry",
                "arguments": {
                    "input_file": "/models/bracket.step",
                    "output_format": "stl",
                    "output_path": "/tmp/freecad/bracket.stl",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert response["result"]["status"] == "success"
        assert response["result"]["tool_id"] == "freecad.export_geometry"
        data = response["result"]["data"]
        assert data["output_file"] == "/tmp/freecad/bracket.stl"
        assert "duration_ms" in response["result"]

    async def test_tool_call_mesh(self, server_with_mocks: FreecadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "freecad.generate_mesh",
                "arguments": {
                    "input_file": "/models/bracket.step",
                    "element_size": 0.5,
                    "algorithm": "netgen",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["num_nodes"] == 12500

    async def test_health_check(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("health/check")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["adapter_id"] == "freecad"
        assert response["result"]["status"] == "healthy"
        assert response["result"]["version"] == "0.1.0"
        assert response["result"]["tools_available"] == 4

    async def test_tool_list_filter_by_capability(self, server: FreecadServer) -> None:
        request = _make_jsonrpc("tool/list", {"capability": "cad_export"})
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tools = response["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "freecad.export_geometry"
