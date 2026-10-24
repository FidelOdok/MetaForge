"""Skill-specific tests for design_pcb (FORGE-683)."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.electronics.skills.design_pcb.handler import DesignPcbHandler
from domain_agents.electronics.skills.design_pcb.schema import DesignPcbInput
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

CIRCUIT = {
    "name": "led",
    "components": [
        {
            "ref": "R1",
            "symbol": "Device:R",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "value": "1k",
        },
        {
            "ref": "D1",
            "symbol": "Device:LED",
            "footprint": "LED_SMD:LED_0603_1608Metric",
            "value": "red",
        },
    ],
    "nets": [{"name": "A", "nodes": ["R1.2", "D1.2"]}],
    "board": {"width_mm": 20, "height_mm": 15, "ground_pour": "GND"},
}


@pytest.fixture()
def ctx() -> SkillContext:
    c = MagicMock(spec=SkillContext)
    c.twin = AsyncMock()
    c.mcp = InMemoryMcpBridge()
    c.logger = MagicMock()
    c.logger.bind = MagicMock(return_value=c.logger)
    c.session_id = uuid4()
    c.branch = "main"
    for tool, cap in (
        ("kicad.generate_schematic", "schematic_generation"),
        ("kicad.generate_pcb", "pcb_generation"),
        ("kicad.autoroute_pcb", "pcb_autoroute"),
    ):
        c.mcp.register_tool(tool, cap)
    return c


def _sch(verdict: str = "pass") -> dict:
    return {
        "verdict": verdict,
        "schematic_file": "/ws/generated/led/led.kicad_sch",
        "erc": {"errors": 0 if verdict == "pass" else 2},
        "netlist_check": {"matched": ["A"], "mismatched": []},
    }


class TestDesignPcb:
    async def test_schematic_board_and_routing(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool_response("kicad.generate_schematic", _sch())
        ctx.mcp.register_tool_response(
            "kicad.generate_pcb",
            {"verdict": "pass", "pcb_file": "/ws/generated/led/led.kicad_pcb", "unplaced": []},
        )
        ctx.mcp.register_tool_response(
            "kicad.autoroute_pcb",
            {
                "verdict": "pass",
                "pcb_file": "/ws/generated/led-routed/led.kicad_pcb",
                "unrouted": 0,
                "drc": {"errors": 0},
                "schematic_parity": [],
                "tracks": 4,
                "vias": 0,
            },
        )
        out = await DesignPcbHandler(ctx).execute(
            DesignPcbInput(work_product_id=uuid4(), circuit=CIRCUIT)
        )
        assert out.schematic_verdict == "pass" and out.pcb_verdict == "pass"
        assert out.route_verdict == "pass" and out.unrouted == 0 and out.tracks == 4

    async def test_failed_schematic_stops_there(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool_response("kicad.generate_schematic", _sch("fail"))
        out = await DesignPcbHandler(ctx).execute(
            DesignPcbInput(work_product_id=uuid4(), circuit=CIRCUIT)
        )
        assert out.schematic_verdict == "fail" and out.erc_errors == 2
        assert out.pcb_verdict is None and "board not generated" in out.reason

    async def test_no_board_means_schematic_only(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool_response("kicad.generate_schematic", _sch())
        circuit = copy.deepcopy(CIRCUIT)
        del circuit["board"]
        out = await DesignPcbHandler(ctx).execute(
            DesignPcbInput(work_product_id=uuid4(), circuit=circuit)
        )
        assert out.pcb_verdict is None and out.reason == "no board in the circuit; schematic only"
