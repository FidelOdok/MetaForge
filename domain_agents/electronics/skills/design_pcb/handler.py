"""Handler for the design_pcb skill (FORGE-683)."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import DesignPcbInput, DesignPcbOutput


class DesignPcbHandler(SkillBase[DesignPcbInput, DesignPcbOutput]):
    """Schematic, then board, then routing; each step only after the last passed.

    Each verdict is KiCad's: ERC and the exported netlist for the schematic,
    placement for the board, DRC with schematic parity for the routing. A
    step that did not run stays ``None``; nothing here turns it into a pass.
    """

    input_type = DesignPcbInput
    output_type = DesignPcbOutput

    async def validate_preconditions(self, input_data: DesignPcbInput) -> list[str]:
        errors: list[str] = []
        wp = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if wp is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        if not await self.context.mcp.is_available("kicad.generate_schematic"):
            errors.append("kicad.generate_schematic is not available (the kicad adapter is down)")
        return errors

    async def execute(self, input_data: DesignPcbInput) -> DesignPcbOutput:
        circuit = input_data.circuit.model_dump()
        sch: dict[str, Any] = await self.context.mcp.invoke(
            "kicad.generate_schematic", {"circuit": circuit}, timeout=300
        )
        out = DesignPcbOutput(
            work_product_id=input_data.work_product_id,
            schematic_verdict=sch.get("verdict", "not_established"),
            schematic_file=str(sch.get("schematic_file", "")),
            erc_errors=int((sch.get("erc") or {}).get("errors") or 0),
            netlist_mismatches=list((sch.get("netlist_check") or {}).get("mismatched", [])),
        )
        if out.schematic_verdict != "pass":
            out.reason = "schematic did not pass ERC and the netlist check; board not generated"
            return out
        if input_data.circuit.board is None:
            out.reason = "no board in the circuit; schematic only"
            return out
        pcb: dict[str, Any] = await self.context.mcp.invoke(
            "kicad.generate_pcb",
            {"circuit": circuit, "schematic_file": out.schematic_file},
            timeout=600,
        )
        out.pcb_verdict = pcb.get("verdict", "not_established")
        out.pcb_file = pcb.get("pcb_file")
        out.unplaced = list(pcb.get("unplaced", []))
        if out.pcb_verdict != "pass" or not input_data.route or not out.pcb_file:
            if out.unplaced:
                out.reason = f"parts did not fit on the board: {', '.join(out.unplaced)}"
            return out
        routed: dict[str, Any] = await self.context.mcp.invoke(
            "kicad.autoroute_pcb",
            {
                "pcb_file": out.pcb_file,
                "passes": input_data.passes,
                "ground_pour": input_data.circuit.board.ground_pour,
            },
            timeout=1200,
        )
        out.route_verdict = routed.get("verdict", "not_established")
        out.routed_pcb_file = routed.get("pcb_file")
        out.unrouted = routed.get("unrouted")
        out.drc_errors = (routed.get("drc") or {}).get("errors")
        out.schematic_parity = list(routed.get("schematic_parity", []))
        out.tracks, out.vias = routed.get("tracks"), routed.get("vias")
        if routed.get("reason"):
            out.reason = str(routed["reason"])
        return out

    async def validate_output(self, output: DesignPcbOutput) -> list[str]:
        if output.route_verdict == "pass" and (output.unrouted or output.drc_errors):
            return ["routing reported pass with unrouted connections or DRC errors"]
        return []
