"""Tests for the KiCad MCP tool adapter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.kicad.adapter import KicadServer
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

    async def test_erc_missing_schematic_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.run_erc({"schematic_file": ""})

    async def test_erc_invalid_severity_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
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

    async def test_drc_missing_pcb_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="pcb_file is required"):
            await server_with_mocks.run_drc({"pcb_file": ""})

    async def test_drc_invalid_severity_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
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

    async def test_bom_missing_schematic_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.export_bom({"schematic_file": ""})

    async def test_bom_invalid_format_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
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
    async def test_gerber_export_success(
        self, server_with_mocks: KicadServer
    ) -> None:
        result = await server_with_mocks.export_gerber(
            {"pcb_file": "/designs/power_supply.kicad_pcb"}
        )
        assert result["output_dir"] == "/tmp/kicad/power_supply_gerber"
        assert result["total_files"] == 7
        assert len(result["files_generated"]) == 7
        assert "F.Cu" in result["layers_exported"]

    async def test_gerber_missing_pcb_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="pcb_file is required"):
            await server_with_mocks.export_gerber({"pcb_file": ""})

    async def test_gerber_default_output_dir(
        self, server_with_mocks: KicadServer
    ) -> None:
        """export_gerber auto-generates output_dir from pcb_file stem."""
        await server_with_mocks.export_gerber(
            {"pcb_file": "/designs/board.kicad_pcb"}
        )
        call_args = server_with_mocks._execute_gerber_export.call_args  # type: ignore[attr-defined]
        assert call_args[0][1] == "/tmp/kicad/board_gerber"


# ---------------------------------------------------------------------------
# TestExportNetlist
# ---------------------------------------------------------------------------


class TestExportNetlist:
    async def test_netlist_export_success(
        self, server_with_mocks: KicadServer
    ) -> None:
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

    async def test_netlist_missing_schematic_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
        with pytest.raises(ValueError, match="schematic_file is required"):
            await server_with_mocks.export_netlist({"schematic_file": ""})

    async def test_netlist_invalid_format_raises(
        self, server_with_mocks: KicadServer
    ) -> None:
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
    async def test_pin_mapping_success(
        self, server_with_mocks: KicadServer
    ) -> None:
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

    async def test_pin_mapping_with_filter(
        self, server_with_mocks: KicadServer
    ) -> None:
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
# TestUnmockedMethodsRaise
# ---------------------------------------------------------------------------


class TestUnmockedMethodsRaise:
    """Verify that calling internal methods without mocks raises NotImplementedError."""

    async def test_erc_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_erc("/designs/test.kicad_sch", "all")

    async def test_drc_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_drc("/designs/test.kicad_pcb", "all", None)

    async def test_bom_export_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_bom_export(
                "/designs/test.kicad_sch", "csv", "value"
            )

    async def test_gerber_export_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_gerber_export(
                "/designs/test.kicad_pcb", "/tmp/gerber", ["F.Cu"]
            )

    async def test_netlist_export_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_netlist_export(
                "/designs/test.kicad_sch", "kicad"
            )

    async def test_pin_mapping_not_implemented(self, server: KicadServer) -> None:
        with pytest.raises(NotImplementedError, match="kicad-cli binary"):
            await server._execute_pin_mapping(
                "/designs/test.kicad_sch", None
            )


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

    async def test_tool_list_filter_by_capability(
        self, server: KicadServer
    ) -> None:
        request = _make_jsonrpc("tool/list", {"capability": "erc_validation"})
        raw_response = await server.handle_request(request)
        response = json.loads(raw_response)
        tools = response["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "kicad.run_erc"
