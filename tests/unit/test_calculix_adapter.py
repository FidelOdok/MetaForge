"""Tests for the CalculiX MCP tool adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.config import CalculixConfig

REAL_CCX_THERMAL_FRD = """\
    1C
    1UMAT    1STEEL
    2C                             8                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 1.00000E+01 0.00000E+00 0.00000E+00
 -3
    1PSTEP                         1           1           1
  100CL  101 1.000000000           8                     0    1           1
 -4  NDTEMP      1    1
 -5  T           1    1    0    0
 -1         1 2.00000E+01
 -1         2 2.00000E+01
 -1         3 2.00000E+01
 -1         4 2.00000E+01
 -1         5 1.00000E+02
 -1         6 1.00000E+02
 -1         7 1.00000E+02
 -1         8 1.00000E+02
 -3
 9999
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> CalculixServer:
    """Bare CalculiX server (no mocks on solver methods)."""
    return CalculixServer()


@pytest.fixture()
def server_with_mocks() -> CalculixServer:
    """Server with mocked solver methods for testing."""
    s = CalculixServer()
    s._execute_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_von_mises": {"bracket_body": 145.2, "bracket_mount": 89.7},
            "solver_time": 12.5,
            "mesh_elements": 45000,
        }
    )
    s._execute_thermal_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "max_temperature": 85.3,
            "min_temperature": 22.1,
            "temperature_distribution": {"zone_a": 85.3, "zone_b": 45.6},
            "solver_time": 8.2,
        }
    )
    s._validate_mesh_file = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "valid": True,
            "element_count": 45000,
            "node_count": 12000,
            "max_aspect_ratio": 3.2,
            "issues": [],
        }
    )
    return s


# ---------------------------------------------------------------------------
# TestCalculixConfig
# ---------------------------------------------------------------------------


class TestCalculixConfig:
    def test_default_config(self) -> None:
        cfg = CalculixConfig()
        assert cfg.ccx_binary == "ccx"
        assert cfg.work_dir == "/tmp/calculix"
        assert cfg.max_solve_time == 600
        assert cfg.max_memory_mb == 2048
        assert cfg.supported_analysis_types == ["static_stress", "thermal", "modal"]

    def test_custom_config(self) -> None:
        cfg = CalculixConfig(
            ccx_binary="/usr/local/bin/ccx",
            work_dir="/data/calculix",
            max_solve_time=300,
            max_memory_mb=4096,
            supported_analysis_types=["static_stress"],
        )
        assert cfg.ccx_binary == "/usr/local/bin/ccx"
        assert cfg.work_dir == "/data/calculix"
        assert cfg.max_solve_time == 300
        assert cfg.max_memory_mb == 4096
        assert cfg.supported_analysis_types == ["static_stress"]


# ---------------------------------------------------------------------------
# TestCalculixServer
# ---------------------------------------------------------------------------


class TestCalculixServer:
    def test_server_registers_four_tools(self, server: CalculixServer) -> None:
        assert len(server.tool_ids) == 4

    def test_tool_ids(self, server: CalculixServer) -> None:
        expected = {
            "calculix.run_fea",
            "calculix.run_thermal",
            "calculix.validate_mesh",
            "calculix.extract_results",
        }
        assert set(server.tool_ids) == expected

    def test_adapter_id_and_version(self, server: CalculixServer) -> None:
        assert server.adapter_id == "calculix"
        assert server.version == "0.1.0"

    def test_custom_config_propagated(self) -> None:
        cfg = CalculixConfig(max_solve_time=120)
        s = CalculixServer(config=cfg)
        assert s.config.max_solve_time == 120


# ---------------------------------------------------------------------------
# TestRunFea
# ---------------------------------------------------------------------------


class TestRunFea:
    async def test_run_fea_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "gravity_1g",
                "analysis_type": "static_stress",
            }
        )
        assert result["max_von_mises"]["bracket_body"] == 145.2
        assert result["solver_time"] == 12.5
        assert result["mesh_elements"] == 45000

    async def test_run_fea_modal_analysis(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_fea(
            {
                "mesh_file": "/models/bracket.inp",
                "load_case": "vibration",
                "analysis_type": "modal",
            }
        )
        assert "max_von_mises" in result

    async def test_run_fea_missing_mesh_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.run_fea(
                {"mesh_file": "", "load_case": "lc1", "analysis_type": "static_stress"}
            )

    async def test_run_fea_missing_load_case_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="load_case is required"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "",
                    "analysis_type": "static_stress",
                }
            )

    async def test_run_fea_invalid_analysis_type_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported analysis type"):
            await server_with_mocks.run_fea(
                {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "lc1",
                    "analysis_type": "buckling",
                }
            )


# ---------------------------------------------------------------------------
# TestRunThermal
# ---------------------------------------------------------------------------


class TestRunThermal:
    async def test_run_thermal_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.run_thermal(
            {
                "mesh_file": "/models/heatsink.inp",
                "boundary_conditions": {"ambient_temp": 25.0, "heat_flux": 100.0},
                "analysis_mode": "steady_state",
            }
        )
        assert result["max_temperature"] == 85.3
        assert result["min_temperature"] == 22.1
        assert "temperature_distribution" in result
        assert result["solver_time"] == 8.2

    async def test_run_thermal_missing_mesh_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.run_thermal(
                {
                    "mesh_file": "",
                    "boundary_conditions": {"ambient_temp": 25.0},
                }
            )

    async def test_run_thermal_missing_boundary_conditions_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="boundary_conditions is required"):
            await server_with_mocks.run_thermal(
                {
                    "mesh_file": "/models/heatsink.inp",
                    "boundary_conditions": {},
                }
            )


# ---------------------------------------------------------------------------
# TestValidateMesh
# ---------------------------------------------------------------------------


class TestValidateMesh:
    async def test_validate_mesh_success(self, server_with_mocks: CalculixServer) -> None:
        result = await server_with_mocks.validate_mesh(
            {"mesh_file": "/models/bracket.inp", "max_aspect_ratio": 10.0}
        )
        assert result["valid"] is True
        assert result["element_count"] == 45000
        assert result["node_count"] == 12000
        assert result["max_aspect_ratio"] == 3.2
        assert result["issues"] == []

    async def test_validate_mesh_missing_file_raises(
        self, server_with_mocks: CalculixServer
    ) -> None:
        with pytest.raises(ValueError, match="mesh_file is required"):
            await server_with_mocks.validate_mesh({"mesh_file": ""})

    async def test_validate_mesh_default_aspect_ratio(
        self, server_with_mocks: CalculixServer
    ) -> None:
        """validate_mesh uses default max_aspect_ratio of 10.0 when not provided."""
        result = await server_with_mocks.validate_mesh({"mesh_file": "/models/bracket.inp"})
        assert result["valid"] is True
        # Verify _validate_mesh_file was called with default value
        call_args = server_with_mocks._validate_mesh_file.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == 10.0


# ---------------------------------------------------------------------------
# TestUnmockedSolverRaisesNotImplemented
# ---------------------------------------------------------------------------


class TestExecuteThermalSolverParsesRealResults:
    """MET-661 follow-up: _execute_thermal_solver previously discarded the
    real solver output and always returned hardcoded max/min_temperature=0.0
    and an empty distribution, regardless of what ccx actually solved."""

    async def test_returns_real_parsed_temperatures_not_hardcoded_zeros(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frd_path = tmp_path / "test_thermal.frd"
        frd_path.write_text(REAL_CCX_THERMAL_FRD, encoding="utf-8")

        async def _fake_solver_run_fea(**_kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 0.01, "result_files": [str(frd_path)]}

        monkeypatch.setattr(
            "tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver_run_fea
        )

        result = await server._execute_thermal_solver(
            "/models/test.inp", {"ambient_temp": 20.0}, "steady_state"
        )

        assert result["max_temperature"] == pytest.approx(100.0)
        assert result["min_temperature"] == pytest.approx(20.0)
        assert result["temperature_distribution"], "must contain real per-node data"


class TestUnmockedSolverRaisesOnMissingFiles:
    """Verify that calling solver methods without mocks raises on missing files."""

    async def test_execute_solver_raises(self, server: CalculixServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server._execute_solver("/models/test.inp", "static_stress")

    async def test_execute_thermal_solver_raises(self, server: CalculixServer) -> None:
        with pytest.raises((FileNotFoundError, NotImplementedError)):
            await server._execute_thermal_solver("/models/test.inp", {"temp": 25.0}, "steady_state")

    async def test_validate_mesh_file_raises(self, server: CalculixServer) -> None:
        with pytest.raises((FileNotFoundError, NotImplementedError)):
            await server._validate_mesh_file("/models/test.inp", 10.0)


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
    async def test_tool_list_via_handle_request(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert len(response["result"]["tools"]) == 4

    async def test_tool_list_contains_expected_ids(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tool_ids = {t["tool_id"] for t in response["result"]["tools"]}
        assert tool_ids == {
            "calculix.run_fea",
            "calculix.run_thermal",
            "calculix.validate_mesh",
            "calculix.extract_results",
        }

    async def test_tool_call_fea_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_fea",
                "arguments": {
                    "mesh_file": "/models/bracket.inp",
                    "load_case": "gravity_1g",
                    "analysis_type": "static_stress",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert response["result"]["status"] == "success"
        assert response["result"]["tool_id"] == "calculix.run_fea"
        data = response["result"]["data"]
        assert data["max_von_mises"]["bracket_body"] == 145.2
        assert "duration_ms" in response["result"]

    async def test_tool_call_thermal_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_thermal",
                "arguments": {
                    "mesh_file": "/models/heatsink.inp",
                    "boundary_conditions": {"ambient_temp": 25.0},
                    "analysis_mode": "transient",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["max_temperature"] == 85.3

    async def test_tool_call_validate_mesh_via_handle_request(
        self, server_with_mocks: CalculixServer
    ) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.validate_mesh",
                "arguments": {"mesh_file": "/models/bracket.inp"},
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["valid"] is True

    async def test_health_check_via_handle_request(self, server: CalculixServer) -> None:
        request = _make_jsonrpc("health/check")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["adapter_id"] == "calculix"
        assert response["result"]["status"] == "healthy"
        assert response["result"]["version"] == "0.1.0"
        assert response["result"]["tools_available"] == 4

    async def test_tool_call_unknown_tool(self, server: CalculixServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {"tool_id": "calculix.nonexistent", "arguments": {}},
        )
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "error" in response
        assert response["error"]["code"] == -32601
        assert response["error"]["data"]["tool_id"] == "calculix.nonexistent"

    async def test_tool_call_validation_error_returns_execution_error(
        self, server: CalculixServer
    ) -> None:
        """When a handler raises ValueError, it should be wrapped as a tool execution error."""
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "calculix.run_fea",
                "arguments": {
                    "mesh_file": "",
                    "load_case": "lc1",
                    "analysis_type": "static_stress",
                },
            },
        )
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "error" in response
        assert response["error"]["code"] == -32001
        assert response["error"]["data"]["error_type"] == "TOOL_EXECUTION_ERROR"
        assert response["error"]["data"]["tool_id"] == "calculix.run_fea"
