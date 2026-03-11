"""Tests for the KiCad MCP tool adapter."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tool_registry.tools.kicad.adapter import (
    KicadCliError,
    KicadCliNotFoundError,
    KicadCliTimeoutError,
    KicadServer,
    _check_kicad_cli,
    _count_drc_unconnected,
    _extract_field,
    _list_gerber_files,
    _parse_bom_csv,
    _parse_drc_violations,
    _parse_erc_violations,
    _parse_netlist_stats,
    _parse_pin_mapping_from_netlist,
    _run_kicad_cli,
)
from tool_registry.tools.kicad.config import KicadConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> KicadServer:
    """Bare KiCad server (no mocks on internal methods)."""
    return KicadServer()


@pytest.fixture()
def server_with_mocks() -> KicadServer:
    """Server with mocked internal methods for testing."""
    s = KicadServer()
    s._execute_erc = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "schematic_file": "/designs/power_supply.kicad_sch",
            "total_violations": 3,
            "errors": 1,
            "warnings": 2,
            "violations": [
                {
                    "rule_id": "ERC001",
                    "severity": "error",
                    "message": "Pin unconnected",
                    "sheet": "Root",
                    "component": "U1",
                    "pin": "VCC",
                },
                {
                    "rule_id": "ERC002",
                    "severity": "warning",
                    "message": "Power pin not driven",
                    "sheet": "Root",
                    "component": "U2",
                    "pin": "VDDIO",
                },
                {
                    "rule_id": "ERC003",
                    "severity": "warning",
                    "message": "Duplicate net name",
                    "sheet": "Sheet1",
                    "component": "R1",
                    "pin": "1",
                },
            ],
            "passed": False,
        }
    )
    s._execute_drc = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "pcb_file": "/designs/power_supply.kicad_pcb",
            "total_violations": 2,
            "errors": 1,
            "warnings": 1,
            "violations": [
                {
                    "rule_id": "DRC001",
                    "severity": "error",
                    "message": "Clearance violation",
                    "location": {"x": 10.5, "y": 20.3, "layer": "F.Cu"},
                    "clearance_required": 0.2,
                    "clearance_actual": 0.15,
                },
                {
                    "rule_id": "DRC002",
                    "severity": "warning",
                    "message": "Silk over pad",
                    "location": {"x": 30.0, "y": 15.0, "layer": "F.SilkS"},
                    "clearance_required": 0.1,
                    "clearance_actual": 0.05,
                },
            ],
            "passed": False,
        }
    )
    s._execute_bom_export = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/kicad/power_supply_bom.csv",
            "total_components": 47,
            "unique_parts": 18,
            "format": "csv",
        }
    )
    s._execute_gerber_export = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_dir": "/tmp/kicad/power_supply_gerber",
            "files_generated": [
                "power_supply-F_Cu.gtl",
                "power_supply-B_Cu.gbl",
                "power_supply-F_Mask.gts",
                "power_supply-B_Mask.gbs",
                "power_supply-F_SilkS.gto",
                "power_supply-B_SilkS.gbo",
                "power_supply-Edge_Cuts.gm1",
            ],
            "total_files": 7,
            "layers_exported": [
                "F.Cu",
                "B.Cu",
                "F.Mask",
                "B.Mask",
                "F.SilkS",
                "B.SilkS",
                "Edge.Cuts",
            ],
        }
    )
    s._execute_netlist_export = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "output_file": "/tmp/kicad/power_supply.net",
            "total_nets": 85,
            "total_components": 47,
            "format": "kicad",
        }
    )
    s._execute_pin_mapping = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "schematic_file": "/designs/power_supply.kicad_sch",
            "components": [
                {
                    "reference": "U1",
                    "value": "STM32F405",
                    "footprint": "LQFP-64",
                    "pins": [
                        {
                            "number": "1",
                            "name": "VBAT",
                            "type": "power_in",
                            "net": "+3V3",
                        },
                        {
                            "number": "2",
                            "name": "PC13",
                            "type": "bidirectional",
                            "net": "LED_STATUS",
                        },
                    ],
                },
                {
                    "reference": "U2",
                    "value": "LM1117-3.3",
                    "footprint": "SOT-223",
                    "pins": [
                        {
                            "number": "1",
                            "name": "GND",
                            "type": "power_in",
                            "net": "GND",
                        },
                        {
                            "number": "2",
                            "name": "VOUT",
                            "type": "power_out",
                            "net": "+3V3",
                        },
                        {
                            "number": "3",
                            "name": "VIN",
                            "type": "power_in",
                            "net": "+5V",
                        },
                    ],
                },
            ],
            "total_components": 2,
            "total_pins": 5,
        }
    )
    return s


# ---------------------------------------------------------------------------
# Helpers
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


# Sample KiCad ERC JSON report
SAMPLE_ERC_REPORT = {
    "source": "test.kicad_sch",
    "date": "2025-01-15",
    "kicad_version": "8.0.0",
    "sheets": [],
    "violations": [
        {
            "type": "pin_not_connected",
            "description": "Pin not connected",
            "severity": "error",
            "items": [
                {
                    "description": "U1: pin VCC not connected",
                    "sheet": "/Root",
                    "pos": {"x": 100.0, "y": 50.0},
                },
                {
                    "description": "VCC",
                    "sheet": "/Root",
                    "pos": {"x": 100.0, "y": 50.0},
                },
            ],
        },
        {
            "type": "power_pin_not_driven",
            "description": "Power pin not driven",
            "severity": "warning",
            "items": [
                {
                    "description": "U2: power pin VDDIO",
                    "sheet": "/Sheet1",
                    "pos": {"x": 200.0, "y": 75.0},
                },
            ],
        },
        {
            "type": "duplicate_net",
            "description": "Duplicate net name",
            "severity": "warning",
            "items": [
                {
                    "description": "NET1",
                    "sheet": "/Root",
                    "pos": {"x": 50.0, "y": 25.0},
                },
            ],
        },
    ],
}

# Sample KiCad DRC JSON report
SAMPLE_DRC_REPORT = {
    "source": "test.kicad_pcb",
    "date": "2025-01-15",
    "kicad_version": "8.0.0",
    "violations": [
        {
            "type": "clearance",
            "description": "Clearance violation between track and pad",
            "severity": "error",
            "items": [
                {
                    "description": "Track on F.Cu",
                    "pos": {"x": 10.5, "y": 20.3},
                    "layer": "F.Cu",
                },
            ],
        },
        {
            "type": "silk_over_pad",
            "description": "Silk text overlaps pad",
            "severity": "warning",
            "items": [
                {
                    "description": "Text on F.SilkS",
                    "pos": {"x": 30.0, "y": 15.0},
                    "layer": "F.SilkS",
                },
            ],
        },
    ],
    "unresolved": [
        {
            "type": "unconnected",
            "description": "Unconnected pad",
            "severity": "error",
            "items": [],
        },
    ],
}

# Sample BOM CSV content
SAMPLE_BOM_CSV = (
    '"Reference";"Value";"Footprint";"Qty"\n'
    '"R1, R2, R3";"10k";"0402";"3"\n'
    '"C1, C2";"100nF";"0402";"2"\n'
    '"U1";"STM32F405";"LQFP-64";"1"\n'
)


def _make_mock_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# TestKicadConfig
# ---------------------------------------------------------------------------


class TestKicadConfig:
    def test_default_config(self) -> None:
        cfg = KicadConfig()
        assert cfg.kicad_cli == "kicad-cli"
        assert cfg.work_dir == "/tmp/kicad"
        assert cfg.max_operation_time == 120
        assert cfg.max_memory_mb == 1024
        assert cfg.supported_versions == ["7", "8"]

    def test_custom_config(self) -> None:
        cfg = KicadConfig(
            kicad_cli="/usr/local/bin/kicad-cli",
            work_dir="/data/kicad",
            max_operation_time=60,
            max_memory_mb=2048,
            supported_versions=["8"],
        )
        assert cfg.kicad_cli == "/usr/local/bin/kicad-cli"
        assert cfg.work_dir == "/data/kicad"
        assert cfg.max_operation_time == 60
        assert cfg.max_memory_mb == 2048
        assert cfg.supported_versions == ["8"]

    def test_validation_constraints(self) -> None:
        # max_operation_time must be >= 1
        with pytest.raises(Exception):
            KicadConfig(max_operation_time=0)
        # max_memory_mb must be >= 256
        with pytest.raises(Exception):
            KicadConfig(max_memory_mb=128)


# ---------------------------------------------------------------------------
# TestKicadServer
# ---------------------------------------------------------------------------


class TestKicadServer:
    def test_server_adapter_id(self, server: KicadServer) -> None:
        assert server.adapter_id == "kicad"

    def test_server_version(self, server: KicadServer) -> None:
        assert server.version == "0.1.0"

    def test_registers_six_tools(self, server: KicadServer) -> None:
        assert len(server.tool_ids) == 6
        expected = {
            "kicad.run_erc",
            "kicad.run_drc",
            "kicad.export_bom",
            "kicad.export_gerber",
            "kicad.export_netlist",
            "kicad.get_pin_mapping",
        }
        assert set(server.tool_ids) == expected


# ---------------------------------------------------------------------------
# TestRunErc
# ---------------------------------------------------------------------------


class TestRunErc:
    async def test_erc_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.run_erc(
            {
                "schematic_file": "/designs/power_supply.kicad_sch",
                "severity_filter": "all",
            }
        )
        assert result["schematic_file"] == "/designs/power_supply.kicad_sch"
        assert result["total_violations"] == 3
        assert result["errors"] == 1
        assert result["warnings"] == 2
        assert result["passed"] is False
        assert len(result["violations"]) == 3

    async def test_erc_missing_schematic_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.run_erc({"schematic_file": ""})

    async def test_erc_invalid_severity_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported severity filter"):
            await server_with_mocks.run_erc(
                {
                    "schematic_file": "/designs/test.kicad_sch",
                    "severity_filter": "critical",
                }
            )


# ---------------------------------------------------------------------------
# TestRunDrc
# ---------------------------------------------------------------------------


class TestRunDrc:
    async def test_drc_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.run_drc(
            {
                "pcb_file": "/designs/power_supply.kicad_pcb",
                "severity_filter": "all",
            }
        )
        assert result["pcb_file"] == "/designs/power_supply.kicad_pcb"
        assert result["total_violations"] == 2
        assert result["errors"] == 1
        assert result["warnings"] == 1
        assert result["passed"] is False
        assert result["violations"][0]["location"]["layer"] == "F.Cu"

    async def test_drc_missing_pcb_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="pcb_file is required"):
            await server_with_mocks.run_drc({"pcb_file": ""})

    async def test_drc_invalid_severity_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported severity filter"):
            await server_with_mocks.run_drc(
                {
                    "pcb_file": "/designs/test.kicad_pcb",
                    "severity_filter": "info",
                }
            )


# ---------------------------------------------------------------------------
# TestExportBom
# ---------------------------------------------------------------------------


class TestExportBom:
    async def test_bom_export_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.export_bom(
            {
                "schematic_file": "/designs/power_supply.kicad_sch",
                "output_format": "csv",
                "group_by": "value",
            }
        )
        assert result["output_file"] == "/tmp/kicad/power_supply_bom.csv"
        assert result["total_components"] == 47
        assert result["unique_parts"] == 18
        assert result["format"] == "csv"

    async def test_bom_missing_schematic_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.export_bom({"schematic_file": ""})

    async def test_bom_invalid_format_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported BOM format"):
            await server_with_mocks.export_bom(
                {
                    "schematic_file": "/designs/test.kicad_sch",
                    "output_format": "xlsx",
                }
            )


# ---------------------------------------------------------------------------
# TestExportGerber
# ---------------------------------------------------------------------------


class TestExportGerber:
    async def test_gerber_export_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.export_gerber(
            {"pcb_file": "/designs/power_supply.kicad_pcb"}
        )
        assert result["output_dir"] == "/tmp/kicad/power_supply_gerber"
        assert result["total_files"] == 7
        assert len(result["files_generated"]) == 7
        assert "F.Cu" in result["layers_exported"]

    async def test_gerber_missing_pcb_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="pcb_file is required"):
            await server_with_mocks.export_gerber({"pcb_file": ""})

    async def test_gerber_default_output_dir(self, server_with_mocks: KicadServer) -> None:
        """export_gerber auto-generates output_dir from pcb_file stem."""
        await server_with_mocks.export_gerber({"pcb_file": "/designs/board.kicad_pcb"})
        call_args = server_with_mocks._execute_gerber_export.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == "/tmp/kicad/board_gerber"


# ---------------------------------------------------------------------------
# TestExportNetlist
# ---------------------------------------------------------------------------


class TestExportNetlist:
    async def test_netlist_export_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.export_netlist(
            {
                "schematic_file": "/designs/power_supply.kicad_sch",
                "output_format": "kicad",
            }
        )
        assert result["output_file"] == "/tmp/kicad/power_supply.net"
        assert result["total_nets"] == 85
        assert result["total_components"] == 47
        assert result["format"] == "kicad"

    async def test_netlist_missing_schematic_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.export_netlist({"schematic_file": ""})

    async def test_netlist_invalid_format_raises(self, server_with_mocks: KicadServer) -> None:
        with pytest.raises(ValueError, match="Unsupported netlist format"):
            await server_with_mocks.export_netlist(
                {
                    "schematic_file": "/designs/test.kicad_sch",
                    "output_format": "orcad",
                }
            )


# ---------------------------------------------------------------------------
# TestGetPinMapping
# ---------------------------------------------------------------------------


class TestGetPinMapping:
    async def test_pin_mapping_success(self, server_with_mocks: KicadServer) -> None:
        result = await server_with_mocks.get_pin_mapping(
            {"schematic_file": "/designs/power_supply.kicad_sch"}
        )
        assert result["schematic_file"] == "/designs/power_supply.kicad_sch"
        assert result["total_components"] == 2
        assert result["total_pins"] == 5
        assert len(result["components"]) == 2
        assert result["components"][0]["reference"] == "U1"
        assert result["components"][0]["pins"][0]["net"] == "+3V3"

    async def test_pin_mapping_missing_schematic_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.get_pin_mapping({"schematic_file": ""})

    async def test_pin_mapping_with_filter(self, server_with_mocks: KicadServer) -> None:
        """get_pin_mapping passes component_filter to _execute_pin_mapping."""
        await server_with_mocks.get_pin_mapping(
            {
                "schematic_file": "/designs/power_supply.kicad_sch",
                "component_filter": "U",
            }
        )
        call_args = server_with_mocks._execute_pin_mapping.call_args  # type: ignore[attr-defined]
        assert call_args[0][0] == "/designs/power_supply.kicad_sch"
        assert call_args[0][1] == "U"


# ---------------------------------------------------------------------------
# TestKicadCliNotAvailable
# ---------------------------------------------------------------------------


class TestKicadCliNotAvailable:
    """Verify that real execute methods raise KicadCliNotFoundError when kicad-cli is missing."""

    async def test_erc_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_erc("/designs/test.kicad_sch", "all")

    async def test_drc_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_drc("/designs/test.kicad_pcb", "all", None)

    async def test_bom_export_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_bom_export("/designs/test.kicad_sch", "csv", "value")

    async def test_gerber_export_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_gerber_export("/designs/test.kicad_pcb", "/tmp/gerber", ["F.Cu"])

    async def test_netlist_export_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_netlist_export("/designs/test.kicad_sch", "kicad")

    async def test_pin_mapping_cli_not_found(self, server: KicadServer) -> None:
        with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
            await server._execute_pin_mapping("/designs/test.kicad_sch", None)


# ---------------------------------------------------------------------------
# TestJsonRpcIntegration
# ---------------------------------------------------------------------------


class TestJsonRpcIntegration:
    async def test_tool_list_returns_six_tools(self, server: KicadServer) -> None:
        request = _make_jsonrpc("tool/list")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert len(response["result"]["tools"]) == 6

    async def test_tool_call_erc(self, server_with_mocks: KicadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "kicad.run_erc",
                "arguments": {
                    "schematic_file": "/designs/power_supply.kicad_sch",
                    "severity_filter": "all",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert "result" in response
        assert response["result"]["status"] == "success"
        assert response["result"]["tool_id"] == "kicad.run_erc"
        data = response["result"]["data"]
        assert data["total_violations"] == 3
        assert "duration_ms" in response["result"]

    async def test_tool_call_drc(self, server_with_mocks: KicadServer) -> None:
        request = _make_jsonrpc(
            "tool/call",
            {
                "tool_id": "kicad.run_drc",
                "arguments": {
                    "pcb_file": "/designs/power_supply.kicad_pcb",
                    "severity_filter": "all",
                },
            },
        )
        raw_response = await server_with_mocks.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["status"] == "success"
        assert response["result"]["data"]["total_violations"] == 2

    async def test_health_check(self, server: KicadServer) -> None:
        request = _make_jsonrpc("health/check")
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        assert response["result"]["adapter_id"] == "kicad"
        assert response["result"]["status"] == "healthy"
        assert response["result"]["version"] == "0.1.0"
        assert response["result"]["tools_available"] == 6

    async def test_tool_list_filter_by_capability(self, server: KicadServer) -> None:
        request = _make_jsonrpc("tool/list", {"capability": "erc_validation"})
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tools = response["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "kicad.run_erc"


# ---------------------------------------------------------------------------
# TestRealKicadExecution - tests with mocked subprocess
# ---------------------------------------------------------------------------


class TestRealKicadExecution:
    """Tests for real kicad-cli execution with mocked subprocess calls."""

    # -- Helper functions tests --

    async def test_check_kicad_cli_success(self) -> None:
        """_check_kicad_cli returns path when binary exists."""
        mock_proc = _make_mock_process(0, b"KiCad 8.0.0\n")

        with patch(
            "tool_registry.tools.kicad.adapter.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await _check_kicad_cli("kicad-cli")
            assert result == "kicad-cli"

    async def test_check_kicad_cli_not_found(self) -> None:
        """_check_kicad_cli raises KicadCliNotFoundError when binary missing."""
        with patch(
            "tool_registry.tools.kicad.adapter.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("No such file"),
        ):
            with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
                await _check_kicad_cli("kicad-cli")

    async def test_run_kicad_cli_success(self) -> None:
        """_run_kicad_cli returns stdout/stderr from successful execution."""
        mock_proc = _make_mock_process(0, b"output data", b"")

        with patch(
            "tool_registry.tools.kicad.adapter.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            rc, stdout, stderr = await _run_kicad_cli("kicad-cli", ["sch", "erc"], 120.0)
            assert rc == 0
            assert stdout == "output data"
            assert stderr == ""

    async def test_run_kicad_cli_not_found(self) -> None:
        """_run_kicad_cli raises KicadCliNotFoundError when binary missing."""
        with patch(
            "tool_registry.tools.kicad.adapter.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("No such file"),
        ):
            with pytest.raises(KicadCliNotFoundError):
                await _run_kicad_cli("kicad-cli", ["sch", "erc"], 120.0)

    async def test_run_kicad_cli_timeout(self) -> None:
        """_run_kicad_cli raises KicadCliTimeoutError on timeout."""
        mock_proc = _make_mock_process(0)
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        # After kill, communicate should succeed
        mock_proc.communicate.side_effect = [TimeoutError(), (b"", b"")]

        async def mock_communicate_with_timeout():
            raise TimeoutError()

        mock_proc = _make_mock_process(0)

        with patch(
            "tool_registry.tools.kicad.adapter.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with patch(
                "tool_registry.tools.kicad.adapter.asyncio.wait_for", side_effect=TimeoutError()
            ):
                with pytest.raises(KicadCliTimeoutError, match="timed out"):
                    await _run_kicad_cli("kicad-cli", ["sch", "erc"], 5.0)

    # -- ERC parsing tests --

    def test_parse_erc_violations_all(self) -> None:
        """Parse all ERC violations from sample report."""
        violations = _parse_erc_violations(SAMPLE_ERC_REPORT, "all")
        assert len(violations) == 3
        assert violations[0]["severity"] == "error"
        assert violations[0]["rule_id"] == "pin_not_connected"
        assert violations[0]["message"] == "Pin not connected"
        assert violations[1]["severity"] == "warning"
        assert violations[2]["severity"] == "warning"

    def test_parse_erc_violations_error_only(self) -> None:
        """Parse only error-severity ERC violations."""
        violations = _parse_erc_violations(SAMPLE_ERC_REPORT, "error")
        assert len(violations) == 1
        assert violations[0]["severity"] == "error"

    def test_parse_erc_violations_warning_only(self) -> None:
        """Parse only warning-severity ERC violations."""
        violations = _parse_erc_violations(SAMPLE_ERC_REPORT, "warning")
        assert len(violations) == 2
        assert all(v["severity"] == "warning" for v in violations)

    def test_parse_erc_violations_empty_report(self) -> None:
        """Empty report returns no violations."""
        violations = _parse_erc_violations({}, "all")
        assert len(violations) == 0

    # -- DRC parsing tests --

    def test_parse_drc_violations_all(self) -> None:
        """Parse all DRC violations from sample report."""
        violations = _parse_drc_violations(SAMPLE_DRC_REPORT, "all")
        assert len(violations) == 2
        assert violations[0]["severity"] == "error"
        assert violations[0]["rule_id"] == "clearance"
        assert violations[0]["location"]["x"] == 10.5
        assert violations[0]["location"]["layer"] == "F.Cu"
        assert violations[1]["severity"] == "warning"

    def test_parse_drc_violations_error_only(self) -> None:
        """Parse only error-severity DRC violations."""
        violations = _parse_drc_violations(SAMPLE_DRC_REPORT, "error")
        assert len(violations) == 1
        assert violations[0]["severity"] == "error"

    def test_count_drc_unconnected(self) -> None:
        """Count unconnected items from DRC report."""
        assert _count_drc_unconnected(SAMPLE_DRC_REPORT) == 1
        assert _count_drc_unconnected({}) == 0

    # -- BOM parsing tests --

    def test_parse_bom_csv(self, tmp_path) -> None:
        """Parse BOM CSV to count components."""
        bom_file = tmp_path / "bom.csv"
        bom_file.write_text(SAMPLE_BOM_CSV, encoding="utf-8")
        total, unique = _parse_bom_csv(str(bom_file))
        assert total == 6  # 3 + 2 + 1
        assert unique == 3  # 10k, 100nF, STM32F405

    def test_parse_bom_csv_missing_file(self) -> None:
        """Missing BOM file returns zeros."""
        total, unique = _parse_bom_csv("/nonexistent/bom.csv")
        assert total == 0
        assert unique == 0

    # -- Gerber file listing tests --

    def test_list_gerber_files(self, tmp_path) -> None:
        """List Gerber files in output directory."""
        (tmp_path / "board-F_Cu.gtl").touch()
        (tmp_path / "board-B_Cu.gbl").touch()
        (tmp_path / "board.drl").touch()
        (tmp_path / "readme.txt").touch()  # should be excluded
        files = _list_gerber_files(str(tmp_path))
        assert len(files) == 3
        assert "board-F_Cu.gtl" in files
        assert "readme.txt" not in files

    def test_list_gerber_files_missing_dir(self) -> None:
        """Missing directory returns empty list."""
        files = _list_gerber_files("/nonexistent/dir")
        assert files == []

    # -- Netlist stats tests --

    def test_parse_netlist_stats(self, tmp_path) -> None:
        """Parse netlist to count nets and components."""
        netlist = tmp_path / "test.net"
        content = (
            "(export (version D)\n"
            "  (components\n"
            "    (comp (ref U1) (value STM32))\n"
            "    (comp (ref R1) (value 10k))\n"
            "  )\n"
            "  (nets\n"
            "    (net (code 1) (name VCC))\n"
            "    (net (code 2) (name GND))\n"
            "    (net (code 3) (name SDA))\n"
            "  )\n"
            ")\n"
        )
        netlist.write_text(content, encoding="utf-8")
        nets, comps = _parse_netlist_stats(str(netlist))
        assert nets == 3
        assert comps == 2

    def test_parse_netlist_stats_missing_file(self) -> None:
        """Missing netlist file returns zeros."""
        nets, comps = _parse_netlist_stats("/nonexistent/file.net")
        assert nets == 0
        assert comps == 0

    # -- Pin mapping parser tests --

    def test_parse_pin_mapping_from_netlist(self, tmp_path) -> None:
        """Parse pin mapping from a KiCad netlist."""
        netlist = tmp_path / "test.net"
        content = (
            "(export (version D)\n"
            "  (components\n"
            "    (comp (ref U1) (value STM32) (footprint LQFP-64))\n"
            "    (comp (ref R1) (value 10k) (footprint 0402))\n"
            "  )\n"
            "  (nets\n"
            '    (net (code 1) (name "VCC")\n'
            '      (node (ref U1) (pin 1) (pinfunction "VCC") (pintype "power_in")))\n'
            '    (net (code 2) (name "GND")\n'
            '      (node (ref U1) (pin 2) (pinfunction "GND") (pintype "power_in"))\n'
            '      (node (ref R1) (pin 1) (pinfunction "1") (pintype "passive")))\n'
            "  )\n"
            ")\n"
        )
        netlist.write_text(content, encoding="utf-8")
        components = _parse_pin_mapping_from_netlist(str(netlist), None)
        assert len(components) == 2
        assert components[0]["reference"] == "U1"
        assert len(components[0]["pins"]) == 2
        assert components[0]["pins"][0]["net"] == "VCC"

    def test_parse_pin_mapping_with_filter(self, tmp_path) -> None:
        """Pin mapping with component filter only returns matching components."""
        netlist = tmp_path / "test.net"
        content = (
            "(export (version D)\n"
            "  (components\n"
            "    (comp (ref U1) (value STM32) (footprint LQFP-64))\n"
            "    (comp (ref R1) (value 10k) (footprint 0402))\n"
            "  )\n"
            "  (nets\n"
            '    (net (code 1) (name "VCC")\n'
            '      (node (ref U1) (pin 1) (pinfunction "VCC") (pintype "power_in")))\n'
            "  )\n"
            ")\n"
        )
        netlist.write_text(content, encoding="utf-8")
        components = _parse_pin_mapping_from_netlist(str(netlist), "U")
        assert len(components) == 1
        assert components[0]["reference"] == "U1"

    def test_parse_pin_mapping_missing_file(self) -> None:
        """Missing netlist file returns empty list."""
        components = _parse_pin_mapping_from_netlist("/nonexistent/file.net", None)
        assert components == []

    # -- S-expression field extraction tests --

    def test_extract_field_quoted(self) -> None:
        """Extract a quoted field value."""
        text = '(ref "U1") (value "STM32")'
        assert _extract_field(text, "ref") == "U1"
        assert _extract_field(text, "value") == "STM32"

    def test_extract_field_unquoted(self) -> None:
        """Extract an unquoted field value."""
        text = "(ref U1) (value 10k)"
        assert _extract_field(text, "ref") == "U1"
        assert _extract_field(text, "value") == "10k"

    def test_extract_field_missing(self) -> None:
        """Missing field returns empty string."""
        assert _extract_field("(ref U1)", "value") == ""

    # -- Full ERC execution with mocked subprocess --

    async def test_execute_erc_with_mocked_subprocess(self, tmp_path) -> None:
        """Full ERC execution with mocked kicad-cli subprocess."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        # Mock _check_kicad_cli to succeed
        mock_check = AsyncMock(return_value="kicad-cli")

        # Mock _run_kicad_cli to succeed
        async def mock_run(cli, args, timeout, cwd=None):
            # Write the JSON report to the output file path
            # The output path is in args after --output
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            with open(output_path, "w") as f:
                json.dump(SAMPLE_ERC_REPORT, f)
            return 0, "", ""

        with patch("tool_registry.tools.kicad.adapter._check_kicad_cli", mock_check):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                result = await server._execute_erc("/designs/test.kicad_sch", "all")

        assert result["schematic_file"] == "/designs/test.kicad_sch"
        assert result["total_violations"] == 3
        assert result["errors"] == 1
        assert result["warnings"] == 2
        assert result["passed"] is False
        assert len(result["violations"]) == 3

    async def test_execute_erc_severity_filter(self, tmp_path) -> None:
        """ERC with severity filter returns only matching violations."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def mock_run(cli, args, timeout, cwd=None):
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            with open(output_path, "w") as f:
                json.dump(SAMPLE_ERC_REPORT, f)
            return 0, "", ""

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                result = await server._execute_erc("/designs/test.kicad_sch", "error")

        assert result["total_violations"] == 1
        assert result["errors"] == 1
        assert result["warnings"] == 0

    # -- Full DRC execution with mocked subprocess --

    async def test_execute_drc_with_mocked_subprocess(self, tmp_path) -> None:
        """Full DRC execution with mocked kicad-cli subprocess."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def mock_run(cli, args, timeout, cwd=None):
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            with open(output_path, "w") as f:
                json.dump(SAMPLE_DRC_REPORT, f)
            return 0, "", ""

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                result = await server._execute_drc("/designs/test.kicad_pcb", "all", None)

        assert result["pcb_file"] == "/designs/test.kicad_pcb"
        assert result["total_violations"] == 2
        assert result["errors"] == 1
        assert result["warnings"] == 1
        assert result["unconnected_items"] == 1
        assert result["passed"] is False

    # -- Full BOM export execution with mocked subprocess --

    async def test_execute_bom_export_with_mocked_subprocess(self, tmp_path) -> None:
        """Full BOM export with mocked kicad-cli subprocess."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def mock_run(cli, args, timeout, cwd=None):
            # Write the BOM CSV to the output file path
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            with open(output_path, "w") as f:
                f.write(SAMPLE_BOM_CSV)
            return 0, "", ""

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                result = await server._execute_bom_export("/designs/test.kicad_sch", "csv", "value")

        assert result["format"] == "csv"
        assert result["total_components"] == 6
        assert result["unique_parts"] == 3

    # -- Full Gerber export execution with mocked subprocess --

    async def test_execute_gerber_export_with_mocked_subprocess(self, tmp_path) -> None:
        """Full Gerber export with mocked kicad-cli subprocess."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))
        output_dir = str(tmp_path / "gerber_out")
        os.makedirs(output_dir, exist_ok=True)

        async def mock_run(cli, args, timeout, cwd=None):
            # Create some fake Gerber files
            for name in ["board-F_Cu.gtl", "board-B_Cu.gbl", "board.drl"]:
                (tmp_path / "gerber_out" / name).touch()
            return 0, "", ""

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                result = await server._execute_gerber_export(
                    "/designs/test.kicad_pcb", output_dir, ["F.Cu", "B.Cu"]
                )

        assert result["output_dir"] == output_dir
        assert result["total_files"] == 3
        assert "board-F_Cu.gtl" in result["files_generated"]
        assert result["layers_exported"] == ["F.Cu", "B.Cu"]

    # -- kicad-cli not found during execution --

    async def test_execute_erc_cli_not_found(self) -> None:
        """ERC raises KicadCliNotFoundError when kicad-cli is missing."""
        server = KicadServer()

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            side_effect=KicadCliNotFoundError("kicad-cli"),
        ):
            with pytest.raises(KicadCliNotFoundError, match="kicad-cli not found"):
                await server._execute_erc("/designs/test.kicad_sch", "all")

    # -- kicad-cli timeout during execution --

    async def test_execute_erc_timeout(self, tmp_path) -> None:
        """ERC raises KicadCliTimeoutError when kicad-cli times out."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch(
                "tool_registry.tools.kicad.adapter._run_kicad_cli",
                side_effect=KicadCliTimeoutError(120.0),
            ):
                with pytest.raises(KicadCliTimeoutError, match="timed out"):
                    await server._execute_erc("/designs/test.kicad_sch", "all")

    # -- kicad-cli non-zero exit with no output file --

    async def test_execute_erc_nonzero_exit_no_output(self, tmp_path) -> None:
        """ERC raises KicadCliError on non-zero exit with no output file."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def mock_run(cli, args, timeout, cwd=None):
            # Don't write any output file
            return 1, "", "Fatal error: invalid schematic"

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                with pytest.raises(KicadCliError, match="exited with code 1"):
                    await server._execute_erc("/designs/test.kicad_sch", "all")

    # -- kicad-cli non-zero exit with partial output --

    async def test_execute_erc_nonzero_exit_with_partial_output(self, tmp_path) -> None:
        """ERC returns partial results when kicad-cli exits non-zero but produces output."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        partial_report = {
            "violations": [
                {
                    "type": "pin_not_connected",
                    "description": "Pin not connected",
                    "severity": "error",
                    "items": [
                        {"description": "U1: pin VCC", "sheet": "/Root", "pos": {"x": 0, "y": 0}}
                    ],
                }
            ]
        }

        async def mock_run(cli, args, timeout, cwd=None):
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            with open(output_path, "w") as f:
                json.dump(partial_report, f)
            return 1, "", "Warning: some errors encountered"

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                # Should succeed since we got valid JSON output
                result = await server._execute_erc("/designs/test.kicad_sch", "all")

        assert result["total_violations"] == 1
        assert result["passed"] is False

    # -- DRC non-zero exit --

    async def test_execute_drc_nonzero_exit_no_output(self, tmp_path) -> None:
        """DRC raises KicadCliError on non-zero exit with no output."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def mock_run(cli, args, timeout, cwd=None):
            return 1, "", "Fatal error: invalid PCB"

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                with pytest.raises(KicadCliError):
                    await server._execute_drc("/designs/test.kicad_pcb", "all", None)

    # -- BOM export non-zero exit --

    async def test_execute_bom_export_nonzero_exit(self, tmp_path) -> None:
        """BOM export raises KicadCliError on non-zero exit."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch(
                "tool_registry.tools.kicad.adapter._run_kicad_cli",
                AsyncMock(return_value=(1, "", "Error")),
            ):
                with pytest.raises(KicadCliError):
                    await server._execute_bom_export("/designs/test.kicad_sch", "csv", "value")

    # -- Gerber export non-zero exit --

    async def test_execute_gerber_export_nonzero_exit(self, tmp_path) -> None:
        """Gerber export raises KicadCliError on non-zero exit."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch(
                "tool_registry.tools.kicad.adapter._run_kicad_cli",
                AsyncMock(return_value=(1, "", "Error")),
            ):
                with pytest.raises(KicadCliError):
                    await server._execute_gerber_export(
                        "/designs/test.kicad_pcb", str(tmp_path / "out"), ["F.Cu"]
                    )

    # -- Custom error types --

    def test_kicad_cli_not_found_error_message(self) -> None:
        """KicadCliNotFoundError has helpful message."""
        err = KicadCliNotFoundError("/usr/bin/kicad-cli")
        assert "/usr/bin/kicad-cli" in str(err)
        assert "install KiCad" in str(err)

    def test_kicad_cli_error_message(self) -> None:
        """KicadCliError includes return code and stderr."""
        err = KicadCliError(2, "something went wrong")
        assert "code 2" in str(err)
        assert "something went wrong" in str(err)

    def test_kicad_cli_timeout_error_message(self) -> None:
        """KicadCliTimeoutError includes timeout value."""
        err = KicadCliTimeoutError(120.0)
        assert "120.0" in str(err)
        assert "timed out" in str(err)

    # -- ERC temp file cleanup --

    async def test_execute_erc_cleans_up_temp_file(self, tmp_path) -> None:
        """ERC cleans up temporary report file even on success."""
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))
        created_files: list[str] = []

        async def mock_run(cli, args, timeout, cwd=None):
            output_idx = args.index("--output") + 1
            output_path = args[output_idx]
            created_files.append(output_path)
            with open(output_path, "w") as f:
                json.dump({"violations": []}, f)
            return 0, "", ""

        with patch(
            "tool_registry.tools.kicad.adapter._check_kicad_cli",
            AsyncMock(return_value="kicad-cli"),
        ):
            with patch("tool_registry.tools.kicad.adapter._run_kicad_cli", mock_run):
                await server._execute_erc("/designs/test.kicad_sch", "all")

        # Temp file should have been cleaned up
        assert len(created_files) == 1
        assert not os.path.exists(created_files[0])
