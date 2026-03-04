"""KiCad PCB/schematic tool adapter -- MCP server for EDA validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.kicad.config import KicadConfig

logger = structlog.get_logger()


class KicadServer(McpToolServer):
    """KiCad tool adapter for PCB/schematic validation via MCP.

    Provides 6 read-only tools (Phase 1 -- no write capabilities):
    - kicad.run_erc: Electrical Rules Check on schematic
    - kicad.run_drc: Design Rules Check on PCB layout
    - kicad.export_bom: Export Bill of Materials from schematic
    - kicad.export_gerber: Export Gerber manufacturing files
    - kicad.export_netlist: Export netlist from schematic
    - kicad.get_pin_mapping: Extract pin mapping from schematic
    """

    def __init__(self, config: KicadConfig | None = None) -> None:
        super().__init__(adapter_id="kicad", version="0.1.0")
        self.config = config or KicadConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all KiCad tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.run_erc",
                adapter_id="kicad",
                name="Run ERC",
                description="Run Electrical Rules Check on a KiCad schematic",
                capability="erc_validation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {
                            "type": "string",
                            "description": "Path to KiCad schematic file (.kicad_sch)",
                        },
                        "severity_filter": {
                            "type": "string",
                            "enum": ["all", "error", "warning"],
                            "default": "all",
                            "description": "Filter violations by severity",
                        },
                    },
                    "required": ["schematic_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {"type": "string"},
                        "total_violations": {"type": "integer"},
                        "errors": {"type": "integer"},
                        "warnings": {"type": "integer"},
                        "violations": {"type": "array"},
                        "passed": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.run_erc,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.run_drc",
                adapter_id="kicad",
                name="Run DRC",
                description="Run Design Rules Check on a KiCad PCB layout",
                capability="drc_validation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pcb_file": {
                            "type": "string",
                            "description": "Path to KiCad PCB file (.kicad_pcb)",
                        },
                        "severity_filter": {
                            "type": "string",
                            "enum": ["all", "error", "warning"],
                            "default": "all",
                            "description": "Filter violations by severity",
                        },
                        "rule_set": {
                            "type": "string",
                            "description": "Optional custom rule set file",
                        },
                    },
                    "required": ["pcb_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "pcb_file": {"type": "string"},
                        "total_violations": {"type": "integer"},
                        "errors": {"type": "integer"},
                        "warnings": {"type": "integer"},
                        "violations": {"type": "array"},
                        "passed": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.run_drc,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.export_bom",
                adapter_id="kicad",
                name="Export BOM",
                description="Export Bill of Materials from a KiCad schematic",
                capability="bom_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {
                            "type": "string",
                            "description": "Path to KiCad schematic file (.kicad_sch)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["csv", "xml", "json"],
                            "default": "csv",
                            "description": "BOM output format",
                        },
                        "group_by": {
                            "type": "string",
                            "enum": ["value", "footprint", "reference"],
                            "default": "value",
                            "description": "Group components by field",
                        },
                    },
                    "required": ["schematic_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_file": {"type": "string"},
                        "total_components": {"type": "integer"},
                        "unique_parts": {"type": "integer"},
                        "format": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.export_bom,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.export_gerber",
                adapter_id="kicad",
                name="Export Gerber",
                description="Export Gerber manufacturing files from a KiCad PCB",
                capability="gerber_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pcb_file": {
                            "type": "string",
                            "description": "Path to KiCad PCB file (.kicad_pcb)",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Optional output directory for Gerber files",
                        },
                        "layers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Layers to export (default: all copper"
                                " + mask + silk + edge)"
                            ),
                        },
                    },
                    "required": ["pcb_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_dir": {"type": "string"},
                        "files_generated": {"type": "array"},
                        "total_files": {"type": "integer"},
                        "layers_exported": {"type": "array"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.export_gerber,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.export_netlist",
                adapter_id="kicad",
                name="Export Netlist",
                description="Export netlist from a KiCad schematic",
                capability="netlist_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {
                            "type": "string",
                            "description": "Path to KiCad schematic file (.kicad_sch)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["kicad", "spice", "cadstar"],
                            "default": "kicad",
                            "description": "Netlist output format",
                        },
                    },
                    "required": ["schematic_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_file": {"type": "string"},
                        "total_nets": {"type": "integer"},
                        "total_components": {"type": "integer"},
                        "format": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.export_netlist,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.get_pin_mapping",
                adapter_id="kicad",
                name="Get Pin Mapping",
                description="Extract pin mapping from a KiCad schematic",
                capability="pin_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {
                            "type": "string",
                            "description": "Path to KiCad schematic file (.kicad_sch)",
                        },
                        "component_filter": {
                            "type": "string",
                            "description": "Filter by reference prefix (e.g. 'U', 'R')",
                        },
                    },
                    "required": ["schematic_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "schematic_file": {"type": "string"},
                        "components": {"type": "array"},
                        "total_components": {"type": "integer"},
                        "total_pins": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=120, max_disk_mb=256
                ),
            ),
            handler=self.get_pin_mapping,
        )

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def run_erc(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run Electrical Rules Check on a KiCad schematic.

        In production, this invokes kicad-cli. For now, it validates
        arguments and delegates to _execute_erc().
        """
        schematic_file = arguments.get("schematic_file", "")
        severity_filter = arguments.get("severity_filter", "all")

        if not schematic_file:
            raise ValueError("schematic_file is required")
        if severity_filter not in ("all", "error", "warning"):
            raise ValueError(f"Unsupported severity filter: {severity_filter}")

        logger.info(
            "Running ERC",
            schematic_file=schematic_file,
            severity_filter=severity_filter,
        )

        result = await self._execute_erc(schematic_file, severity_filter)
        return result

    async def run_drc(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run Design Rules Check on a KiCad PCB layout."""
        pcb_file = arguments.get("pcb_file", "")
        severity_filter = arguments.get("severity_filter", "all")
        rule_set = arguments.get("rule_set")

        if not pcb_file:
            raise ValueError("pcb_file is required")
        if severity_filter not in ("all", "error", "warning"):
            raise ValueError(f"Unsupported severity filter: {severity_filter}")

        logger.info(
            "Running DRC",
            pcb_file=pcb_file,
            severity_filter=severity_filter,
            rule_set=rule_set,
        )

        result = await self._execute_drc(pcb_file, severity_filter, rule_set)
        return result

    async def export_bom(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export Bill of Materials from a KiCad schematic."""
        schematic_file = arguments.get("schematic_file", "")
        output_format = arguments.get("output_format", "csv")
        group_by = arguments.get("group_by", "value")

        if not schematic_file:
            raise ValueError("schematic_file is required")
        if output_format not in ("csv", "xml", "json"):
            raise ValueError(f"Unsupported BOM format: {output_format}")
        if group_by not in ("value", "footprint", "reference"):
            raise ValueError(f"Unsupported group_by field: {group_by}")

        logger.info(
            "Exporting BOM",
            schematic_file=schematic_file,
            output_format=output_format,
            group_by=group_by,
        )

        result = await self._execute_bom_export(
            schematic_file, output_format, group_by
        )
        return result

    async def export_gerber(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export Gerber manufacturing files from a KiCad PCB."""
        pcb_file = arguments.get("pcb_file", "")
        output_dir = arguments.get("output_dir", "")
        layers = arguments.get(
            "layers",
            [
                "F.Cu",
                "B.Cu",
                "F.Mask",
                "B.Mask",
                "F.SilkS",
                "B.SilkS",
                "Edge.Cuts",
            ],
        )

        if not pcb_file:
            raise ValueError("pcb_file is required")

        if not output_dir:
            stem = Path(pcb_file).stem
            output_dir = f"{self.config.work_dir}/{stem}_gerber"

        logger.info(
            "Exporting Gerber",
            pcb_file=pcb_file,
            output_dir=output_dir,
            layers=layers,
        )

        result = await self._execute_gerber_export(pcb_file, output_dir, layers)
        return result

    async def export_netlist(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export netlist from a KiCad schematic."""
        schematic_file = arguments.get("schematic_file", "")
        output_format = arguments.get("output_format", "kicad")

        if not schematic_file:
            raise ValueError("schematic_file is required")
        if output_format not in ("kicad", "spice", "cadstar"):
            raise ValueError(f"Unsupported netlist format: {output_format}")

        logger.info(
            "Exporting netlist",
            schematic_file=schematic_file,
            output_format=output_format,
        )

        result = await self._execute_netlist_export(schematic_file, output_format)
        return result

    async def get_pin_mapping(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Extract pin mapping from a KiCad schematic."""
        schematic_file = arguments.get("schematic_file", "")
        component_filter = arguments.get("component_filter")

        if not schematic_file:
            raise ValueError("schematic_file is required")

        logger.info(
            "Getting pin mapping",
            schematic_file=schematic_file,
            component_filter=component_filter,
        )

        result = await self._execute_pin_mapping(schematic_file, component_filter)
        return result

    # ------------------------------------------------------------------
    # Internal execution methods (mockable in tests)
    # ------------------------------------------------------------------

    async def _execute_erc(
        self, schematic_file: str, severity_filter: str
    ) -> dict[str, Any]:
        """Execute KiCad ERC. In production, runs kicad-cli.

        This method is designed to be easily mockable in tests.
        The actual implementation would:
        1. Run: kicad-cli sch erc --output <report> <schematic_file>
        2. Parse the ERC report
        3. Return structured results
        """
        # This would be the actual kicad-cli call in production:
        # result = await asyncio.create_subprocess_exec(
        #     self.config.kicad_cli, "sch", "erc",
        #     "--output", report_path, schematic_file,
        #     cwd=self.config.work_dir,
        #     stdout=asyncio.subprocess.PIPE,
        #     stderr=asyncio.subprocess.PIPE,
        # )

        raise NotImplementedError(
            "KiCad ERC requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )

    async def _execute_drc(
        self, pcb_file: str, severity_filter: str, rule_set: str | None
    ) -> dict[str, Any]:
        """Execute KiCad DRC. See _execute_erc for notes."""
        raise NotImplementedError(
            "KiCad DRC requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )

    async def _execute_bom_export(
        self, schematic_file: str, output_format: str, group_by: str
    ) -> dict[str, Any]:
        """Execute KiCad BOM export. See _execute_erc for notes."""
        raise NotImplementedError(
            "KiCad BOM export requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )

    async def _execute_gerber_export(
        self, pcb_file: str, output_dir: str, layers: list[str]
    ) -> dict[str, Any]:
        """Execute KiCad Gerber export. See _execute_erc for notes."""
        raise NotImplementedError(
            "KiCad Gerber export requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )

    async def _execute_netlist_export(
        self, schematic_file: str, output_format: str
    ) -> dict[str, Any]:
        """Execute KiCad netlist export. See _execute_erc for notes."""
        raise NotImplementedError(
            "KiCad netlist export requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )

    async def _execute_pin_mapping(
        self, schematic_file: str, component_filter: str | None
    ) -> dict[str, Any]:
        """Execute KiCad pin mapping extraction. See _execute_erc for notes."""
        raise NotImplementedError(
            "KiCad pin mapping requires the kicad-cli binary. "
            "Use mock in tests or install KiCad for production use."
        )
