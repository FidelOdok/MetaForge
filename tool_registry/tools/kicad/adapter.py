"""KiCad PCB/schematic tool adapter -- MCP server for EDA validation."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.config import KicadConfig

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.kicad")


#: Netlist formats: our name -> (kicad-cli 9 --format, file extension). ``kicad``
#: stays an alias of ``kicadsexpr``; kicad-cli 9 rejects it ("Invalid format").
#: BOM grouping: our ``group_by`` -> kicad-cli ``--group-by`` fields ("" = one line per symbol).
#: FORGE-694: MPN is grouped on too, so two parts that share a value and
#: footprint but not a part number never fold into one line.
BOM_GROUPING: dict[str, str] = {
    "value": "Value,Footprint,MPN",
    "footprint": "Footprint,MPN",
    "reference": "",
}

#: FORGE-694: the BOM carries each symbol's MPN and Manufacturer fields (empty
#: when the symbol has none), so an assembler's BOM can be checked line by line.
BOM_FIELDS = "Reference,Value,Footprint,${QUANTITY},${DNP},MPN,Manufacturer"
BOM_LABELS = "Refs,Value,Footprint,Qty,DNP,MPN,Manufacturer"

NETLIST_FORMATS: dict[str, tuple[str, str]] = {
    "kicad": ("kicadsexpr", "net"),
    "kicadsexpr": ("kicadsexpr", "net"),
    "kicadxml": ("kicadxml", "xml"),
    "spice": ("spice", "cir"),
    "spicemodel": ("spicemodel", "cir"),
    "cadstar": ("cadstar", "frp"),
    "orcadpcb2": ("orcadpcb2", "net"),
    "pads": ("pads", "asc"),
    "allegro": ("allegro", "txt"),
}


class KicadCliNotFoundError(RuntimeError):
    """Raised when kicad-cli binary is not available on the system."""

    def __init__(self, cli_path: str) -> None:
        self.cli_path = cli_path
        super().__init__(
            f"kicad-cli not found at '{cli_path}'. "
            "Please install KiCad 8+ (https://www.kicad.org/download/) "
            "and ensure kicad-cli is on your PATH."
        )


class KicadCliError(RuntimeError):
    """Raised when kicad-cli exits with a non-zero return code."""

    def __init__(
        self, returncode: int, stderr: str, partial_result: dict[str, Any] | None = None
    ) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.partial_result = partial_result
        super().__init__(f"kicad-cli exited with code {returncode}: {stderr[:500]}")


class KicadCliTimeoutError(RuntimeError):
    """Raised when kicad-cli exceeds the configured timeout."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"kicad-cli timed out after {timeout}s")


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


async def _check_kicad_cli(cli_path: str) -> str:
    """Check if kicad-cli is available and return the resolved path.

    Raises KicadCliNotFoundError if the binary cannot be found or executed.
    """
    with tracer.start_as_current_span("kicad.check_cli") as span:
        span.set_attribute("kicad.cli_path", cli_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                cli_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version_str = stdout.decode("utf-8", errors="replace").strip()
            span.set_attribute("kicad.version", version_str)
            logger.info("kicad-cli found", cli_path=cli_path, version=version_str)
            return cli_path
        except FileNotFoundError:
            span.record_exception(KicadCliNotFoundError(cli_path))
            raise KicadCliNotFoundError(cli_path)
        except Exception as exc:
            span.record_exception(exc)
            raise KicadCliNotFoundError(cli_path) from exc


async def _run_kicad_cli(
    cli_path: str, args: list[str], timeout: float, cwd: str | None = None
) -> tuple[int, str, str]:
    """Run a kicad-cli subprocess with the given arguments and timeout.

    Returns (returncode, stdout, stderr).
    Raises KicadCliNotFoundError if the binary is not found.
    Raises KicadCliTimeoutError if the process exceeds *timeout* seconds.
    """
    full_cmd = [cli_path, *args]
    logger.debug("Running kicad-cli", cmd=full_cmd, timeout=timeout, cwd=cwd)

    with tracer.start_as_current_span("kicad.run_cli") as span:
        span.set_attribute("kicad.command", " ".join(full_cmd))
        span.set_attribute("kicad.timeout", timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            raise KicadCliNotFoundError(cli_path)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            raise KicadCliTimeoutError(timeout)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        returncode = proc.returncode or 0

        span.set_attribute("kicad.returncode", returncode)
        logger.debug(
            "kicad-cli completed",
            returncode=returncode,
            stdout_len=len(stdout),
            stderr_len=len(stderr),
        )

        return returncode, stdout, stderr


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
        self._register_fabrication_tools()
        from tool_registry.tools.kicad.generation import register_generation_tools

        register_generation_tools(self)
        from tool_registry.tools.kicad.views import register_view_tools

        register_view_tools(self)

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
                            "description": (
                                "Optional custom design rules: a path to a "
                                ".kicad_dru file, or its text. DRC runs on a "
                                "copy of the board with these rules beside it"
                            ),
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
                        "unconnected_items": {"type": "integer"},
                        "rule_set_applied": {"type": "boolean"},
                        "passed": {
                            "type": "boolean",
                            "description": (
                                "No error-severity violation and nothing "
                                "unconnected, counted before severity_filter"
                            ),
                        },
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
                            "enum": ["csv", "json"],
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
                        "lines_without_mpn": {
                            "type": "integer",
                            "description": (
                                "Placed (not DNP) lines whose symbols have no MPN field: "
                                "lines a fab house cannot buy against (FORGE-694)"
                            ),
                        },
                        "components_without_mpn": {"type": "integer"},
                        "references_without_mpn": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
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
                description=(
                    "Export Gerber manufacturing files from a KiCad PCB, the paste (stencil)"
                    " layers included by default. layers_exported lists only the layers a"
                    " file was written for; a requested layer with no file is in"
                    " layers_missing."
                ),
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
                                "Layers to export (default: all copper + paste + mask + silk"
                                " + edge)"
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
                        # Only layers a file was written for; a requested
                        # layer with no file is in layers_missing (FORGE-703).
                        "layers_exported": {"type": "array"},
                        "layers_missing": {"type": "array"},
                        "layer_files": {"type": "object"},
                        "paste_layers": {"type": "array"},
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
                            "enum": sorted(NETLIST_FORMATS),
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

    def _register_fabrication_tools(self) -> None:
        """Drill and position files, which complete the fabrication set (FORGE-680)."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.export_drill",
                adapter_id="kicad",
                name="Export Drill Files",
                description=(
                    "Export the board's drill files (Excellon, plated and non-plated "
                    "holes in separate files, millimetres) with kicad-cli, into a new "
                    "folder on the adapter workspace. Returns the files and the hole "
                    "count per file. A missing board is refused by name."
                ),
                capability="drill_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pcb_file": {"type": "string", "description": "Path to .kicad_pcb"},
                        "output_dir": {"type": "string", "description": "Output folder"},
                    },
                    "required": ["pcb_file"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
            ),
            handler=self.export_drill,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="kicad.export_pos",
                adapter_id="kicad",
                name="Export Component Positions",
                description=(
                    "Export the pick-and-place file (CSV, millimetres, both sides) with "
                    "kicad-cli. Returns the file and each placed component's reference, "
                    "value, package, position, rotation and side. A missing board is "
                    "refused by name."
                ),
                capability="position_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pcb_file": {"type": "string", "description": "Path to .kicad_pcb"},
                        "output_file": {"type": "string", "description": "Output CSV path"},
                    },
                    "required": ["pcb_file"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
            ),
            handler=self.export_pos,
        )

    async def export_drill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pcb_file = self._existing(str(arguments.get("pcb_file") or ""), "board")
        stem = Path(pcb_file).stem
        output_dir = self._fresh_dir(str(arguments.get("output_dir") or ""), stem, "drill")
        with tracer.start_as_current_span("kicad.export_drill") as span:
            span.set_attribute("kicad.pcb_file", pcb_file)
            await _check_kicad_cli(self.config.kicad_cli)
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            args = [
                "pcb",
                "export",
                "drill",
                "--format",
                "excellon",
                "--excellon-units",
                "mm",
                "--excellon-separate-th",
                "--output",
                output_dir.rstrip("/") + "/",
                pcb_file,
            ]
            rc, _out, err = await _run_kicad_cli(
                self.config.kicad_cli, args, float(self.config.max_operation_time)
            )
            if rc != 0:
                raise KicadCliError(rc, err)
            files = sorted(p for p in Path(output_dir).iterdir() if p.suffix.lower() == ".drl")
            holes = {f.name: _count_excellon_holes(f) for f in files}
            logger.info("Drill export completed", output_dir=output_dir, files=len(files))
            return {
                "output_dir": output_dir,
                "files_generated": [str(f) for f in files],
                "holes_per_file": holes,
                "total_holes": sum(holes.values()),
            }

    async def export_pos(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pcb_file = self._existing(str(arguments.get("pcb_file") or ""), "board")
        stem = Path(pcb_file).stem
        output_file = self._fresh_file(
            str(arguments.get("output_file") or ""), stem, "pos", f"{stem}_pos.csv"
        )
        with tracer.start_as_current_span("kicad.export_pos") as span:
            span.set_attribute("kicad.pcb_file", pcb_file)
            await _check_kicad_cli(self.config.kicad_cli)
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            args = [
                "pcb",
                "export",
                "pos",
                "--format",
                "csv",
                "--units",
                "mm",
                "--side",
                "both",
                "--output",
                output_file,
                pcb_file,
            ]
            rc, _out, err = await _run_kicad_cli(
                self.config.kicad_cli, args, float(self.config.max_operation_time)
            )
            if rc != 0:
                raise KicadCliError(rc, err)
            placements = _parse_pos_csv(output_file)
            logger.info("Position export completed", output_file=output_file, n=len(placements))
            return {
                "output_file": output_file,
                "placements": placements,
                "total_components": len(placements),
            }

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
        schematic_file = self._existing(schematic_file, "schematic")

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
        pcb_file = self._existing(pcb_file, "board")

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
        if output_format not in ("csv", "json"):
            raise ValueError(
                f"Unsupported BOM format: {output_format}; kicad-cli writes csv, and json is "
                "the same rows as a JSON file"
            )
        if group_by not in ("value", "footprint", "reference"):
            raise ValueError(f"Unsupported group_by field: {group_by}")
        schematic_file = self._existing(schematic_file, "schematic")

        logger.info(
            "Exporting BOM",
            schematic_file=schematic_file,
            output_format=output_format,
            group_by=group_by,
        )

        result = await self._execute_bom_export(schematic_file, output_format, group_by)
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
                # The stencil layers: an assembled board needs them (FORGE-703).
                "F.Paste",
                "B.Paste",
                "F.Mask",
                "B.Mask",
                "F.SilkS",
                "B.SilkS",
                "Edge.Cuts",
            ],
        )

        if not pcb_file:
            raise ValueError("pcb_file is required")
        pcb_file = self._existing(pcb_file, "board")
        if "layers" not in arguments:
            # A multilayer board's inner copper belongs in the default set too,
            # or the fab gets a 4-layer job without its planes.
            layers = [*layers[:1], *_inner_copper_layers(pcb_file), *layers[1:]]

        output_dir = self._fresh_dir(output_dir, Path(pcb_file).stem, "gerber")

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
        if output_format not in NETLIST_FORMATS:
            raise ValueError(
                f"Unsupported netlist format: {output_format}; "
                f"one of {', '.join(sorted(NETLIST_FORMATS))}"
            )
        schematic_file = self._existing(schematic_file, "schematic")

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
        schematic_file = self._existing(schematic_file, "schematic")

        logger.info(
            "Getting pin mapping",
            schematic_file=schematic_file,
            component_filter=component_filter,
        )

        result = await self._execute_pin_mapping(schematic_file, component_filter)
        return result

    def _existing(self, path: str, kind: str) -> str:
        """The path, resolved against the workspace, or a refusal naming what is missing.

        kicad-cli answers a missing file with a bare exit code ("Failed to
        load board"), which read like a broken design; a schematic with no
        board is a normal state and is said plainly (FORGE-172).
        """
        p = Path(path)
        if not p.is_absolute():
            p = Path(self.config.work_dir) / p
        if not p.is_file():
            suffix = {"board": ".kicad_pcb", "schematic": ".kicad_sch"}[kind]
            hint = (
                f"; this project has no {suffix} board yet, so there is nothing to check"
                if kind == "board"
                else ""
            )
            raise FileNotFoundError(f"no {kind} file at {path}{hint}")
        return str(p)

    def _fresh_dir(self, requested: str, stem: str, kind: str) -> str:
        """A folder an export can fill without overwriting anything (FORGE-692).

        Unnamed, it is a new folder of its own. Named, it must be new or
        empty. An export that cannot overwrite or remove is additive, which is
        what lets a design flow's phase task run it at all.
        """
        if requested:
            p = Path(requested)
            if p.exists() and (not p.is_dir() or any(p.iterdir())):
                raise ValueError(
                    f"{requested} already holds files and an export never overwrites: "
                    "name a new or empty folder, or leave it out for a new one"
                )
            return requested
        return f"{self.config.work_dir}/exports/{stem}_{kind}-{uuid.uuid4().hex[:8]}"

    def _fresh_file(self, requested: str, stem: str, kind: str, name: str) -> str:
        """A file path an export can write without overwriting anything (FORGE-692)."""
        if requested:
            if Path(requested).exists():
                raise ValueError(
                    f"{requested} already exists and an export never overwrites: "
                    "name a new file, or leave it out for a new folder"
                )
            return requested
        return f"{self._fresh_dir('', stem, kind)}/{name}"

    # ------------------------------------------------------------------
    # Internal execution methods
    # ------------------------------------------------------------------

    async def _execute_erc(self, schematic_file: str, severity_filter: str) -> dict[str, Any]:
        """Execute KiCad ERC via kicad-cli.

        Runs: kicad-cli sch erc --format json --severity-all --output <tmp> <schematic>
        Parses the JSON report and returns structured results.
        """
        with tracer.start_as_current_span("kicad.execute_erc") as span:
            span.set_attribute("kicad.schematic_file", schematic_file)
            span.set_attribute("kicad.severity_filter", severity_filter)

            await _check_kicad_cli(self.config.kicad_cli)

            report_fd, report_path = tempfile.mkstemp(suffix=".json", prefix="kicad_erc_")
            os.close(report_fd)

            try:
                cli_args = [
                    "sch",
                    "erc",
                    "--format",
                    "json",
                    "--severity-all",
                    "--output",
                    report_path,
                    schematic_file,
                ]
                timeout = float(self.config.max_operation_time)

                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                # Parse JSON report (kicad-cli may exit non-zero but still produce output)
                report_data: dict[str, Any] = {}
                try:
                    with open(report_path) as f:
                        report_data = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError) as parse_exc:
                    if returncode != 0:
                        raise KicadCliError(returncode, stderr) from parse_exc
                    raise

                # Extract violations from KiCad JSON report format
                violations = _parse_erc_violations(report_data, severity_filter)
                # FORGE-551: the verdict is taken before the severity filter
                # (filtering errors out must not turn a failing sheet into a
                # pass), and warnings do not fail it, matching the run_erc skill.
                all_errors = sum(
                    1 for v in _parse_erc_violations(report_data, "all") if v["severity"] == "error"
                )

                errors = sum(1 for v in violations if v["severity"] == "error")
                warnings = sum(1 for v in violations if v["severity"] == "warning")

                result = {
                    "schematic_file": schematic_file,
                    "total_violations": len(violations),
                    "errors": errors,
                    "warnings": warnings,
                    "violations": violations,
                    "passed": all_errors == 0,
                }

                span.set_attribute("kicad.erc.total_violations", len(violations))
                span.set_attribute("kicad.erc.passed", result["passed"])
                logger.info(
                    "ERC completed",
                    schematic_file=schematic_file,
                    total_violations=len(violations),
                    passed=result["passed"],
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError):
                raise
            except KicadCliError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise
            finally:
                if os.path.exists(report_path):
                    os.unlink(report_path)

    async def _execute_drc(
        self,
        pcb_file: str,
        severity_filter: str,
        rule_set: str | None,
        schematic_parity: bool = False,
    ) -> dict[str, Any]:
        """Execute KiCad DRC via kicad-cli.

        Runs: kicad-cli pcb drc --format json --severity-all --output <tmp> <pcb>
        Parses the JSON report and returns structured results.
        """
        with tracer.start_as_current_span("kicad.execute_drc") as span:
            span.set_attribute("kicad.pcb_file", pcb_file)
            span.set_attribute("kicad.severity_filter", severity_filter)

            await _check_kicad_cli(self.config.kicad_cli)

            report_fd, report_path = tempfile.mkstemp(suffix=".json", prefix="kicad_drc_")
            os.close(report_fd)
            rules_dir: tempfile.TemporaryDirectory[str] | None = None
            board = pcb_file
            if rule_set:
                # FORGE-551: rule_set was accepted and never reached KiCad.
                # kicad-cli has no rules flag; it applies the <stem>.kicad_dru
                # beside the board, so DRC runs on a copy with the rules there.
                rules_dir = tempfile.TemporaryDirectory(prefix="kicad_drc_rules_")
                board = _stage_board_with_rules(pcb_file, rule_set, rules_dir.name)
                span.set_attribute("kicad.drc.rule_set_applied", True)

            try:
                cli_args = [
                    "pcb",
                    "drc",
                    *(["--schematic-parity"] if schematic_parity else []),
                    "--format",
                    "json",
                    "--severity-all",
                    "--output",
                    report_path,
                    board,
                ]
                timeout = float(self.config.max_operation_time)

                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                # Parse JSON report
                report_data: dict[str, Any] = {}
                try:
                    with open(report_path) as f:
                        report_data = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError) as parse_exc:
                    if returncode != 0:
                        raise KicadCliError(returncode, stderr) from parse_exc
                    raise

                violations = _parse_drc_violations(report_data, severity_filter)
                unconnected = _count_drc_unconnected(report_data)
                parity_issues = [
                    {"type": v.get("type", ""), "message": v.get("description", "")}
                    for v in report_data.get("schematic_parity", [])
                ]
                all_errors = sum(
                    1 for v in _parse_drc_violations(report_data, "all") if v["severity"] == "error"
                )

                errors = sum(1 for v in violations if v["severity"] == "error")
                warnings = sum(1 for v in violations if v["severity"] == "warning")

                result = {
                    "pcb_file": pcb_file,
                    "total_violations": len(violations),
                    "errors": errors,
                    "warnings": warnings,
                    "violations": violations,
                    "unconnected_items": unconnected,
                    "rule_set_applied": bool(rule_set),
                    # Before the severity filter, warnings allowed (FORGE-551).
                    "passed": all_errors == 0 and unconnected == 0,
                }
                if schematic_parity:
                    result["schematic_parity"] = parity_issues
                    result["passed"] = result["passed"] and not parity_issues

                span.set_attribute("kicad.drc.total_violations", len(violations))
                span.set_attribute("kicad.drc.unconnected", unconnected)
                span.set_attribute("kicad.drc.passed", result["passed"])
                logger.info(
                    "DRC completed",
                    pcb_file=pcb_file,
                    total_violations=len(violations),
                    unconnected_items=unconnected,
                    passed=result["passed"],
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError):
                raise
            except KicadCliError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise
            finally:
                if os.path.exists(report_path):
                    os.unlink(report_path)
                if rules_dir is not None:
                    rules_dir.cleanup()

    async def _execute_bom_export(
        self, schematic_file: str, output_format: str, group_by: str
    ) -> dict[str, Any]:
        """Execute KiCad BOM export via kicad-cli.

        Runs: kicad-cli sch export bom --output <output_path> <schematic>
        Parses the CSV output into structured BOM entries.
        """
        with tracer.start_as_current_span("kicad.execute_bom_export") as span:
            span.set_attribute("kicad.schematic_file", schematic_file)
            span.set_attribute("kicad.output_format", output_format)
            span.set_attribute("kicad.group_by", group_by)

            await _check_kicad_cli(self.config.kicad_cli)

            stem = Path(schematic_file).stem
            folder = self._fresh_dir("", stem, "bom")
            Path(folder).mkdir(parents=True, exist_ok=True)
            csv_file = f"{folder}/{stem}_bom.csv"
            cli_args = [
                "sch",
                "export",
                "bom",
                "--output",
                csv_file,
                "--fields",
                BOM_FIELDS,
                "--labels",
                BOM_LABELS,
            ]
            group_fields = BOM_GROUPING[group_by]
            if group_fields:
                cli_args += ["--group-by", group_fields]
            cli_args.append(schematic_file)
            timeout = float(self.config.max_operation_time)
            try:
                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )
                if returncode != 0:
                    raise KicadCliError(returncode, stderr)
                parts = _parse_bom_rows(csv_file)
                total_components = sum(p["quantity"] for p in parts)
                unique_parts = len(parts)
                output_file = csv_file
                if output_format == "json":
                    output_file = f"{folder}/{stem}_bom.json"
                    Path(output_file).write_text(json.dumps(parts, indent=1), encoding="utf-8")
                unsourced = _lines_without_mpn(parts)
                result = {
                    "output_file": output_file,
                    "total_components": total_components,
                    "unique_parts": unique_parts,
                    "group_by": group_by,
                    "parts": parts,
                    "format": output_format,
                    # FORGE-694: placed lines an assembler cannot buy against.
                    "lines_without_mpn": len(unsourced),
                    "components_without_mpn": sum(p["quantity"] for p in unsourced),
                    "references_without_mpn": [r for p in unsourced for r in p["references"]],
                }
                span.set_attribute("kicad.bom.total_components", total_components)
                span.set_attribute("kicad.bom.unique_parts", unique_parts)
                logger.info(
                    "BOM export completed",
                    output_file=output_file,
                    total_components=total_components,
                    unique_parts=unique_parts,
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError, KicadCliError):
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _execute_gerber_export(
        self, pcb_file: str, output_dir: str, layers: list[str]
    ) -> dict[str, Any]:
        """Execute KiCad Gerber export via kicad-cli.

        Runs: kicad-cli pcb export gerbers --output <output_dir> <pcb>
        Lists generated Gerber files and returns structured results.
        """
        with tracer.start_as_current_span("kicad.execute_gerber_export") as span:
            span.set_attribute("kicad.pcb_file", pcb_file)
            span.set_attribute("kicad.output_dir", output_dir)
            span.set_attribute("kicad.layers", ",".join(layers))

            await _check_kicad_cli(self.config.kicad_cli)
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            cli_args = [
                "pcb",
                "export",
                "gerbers",
                "--output",
                output_dir,
                pcb_file,
            ]
            # kicad-cli 9 takes one comma-separated list; a repeated --layers
            # keeps only the last one.
            if layers:
                cli_args.extend(["--layers", ",".join(layers)])

            timeout = float(self.config.max_operation_time)

            try:
                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                if returncode != 0:
                    raise KicadCliError(returncode, stderr)

                # List generated files
                files_generated = _list_gerber_files(output_dir)
                # FORGE-703: report the layers from the files on disk, never
                # from the request. kicad-cli skips a layer name it does not
                # know and still exits 0.
                layer_files, layers_missing = _match_gerber_layers(layers, files_generated)

                result = {
                    "output_dir": output_dir,
                    "files_generated": files_generated,
                    "total_files": len(files_generated),
                    "layers_exported": list(layer_files),
                    "layers_missing": layers_missing,
                    "layer_files": layer_files,
                    "paste_layers": [
                        layer for layer in layer_files if _canonical_layer(layer).endswith(".Paste")
                    ],
                }

                span.set_attribute("kicad.gerber.total_files", len(files_generated))
                span.set_attribute("kicad.gerber.layers_missing", len(layers_missing))
                if layers_missing:
                    logger.warning(
                        "Gerber layers requested but not written",
                        output_dir=output_dir,
                        layers_missing=layers_missing,
                    )
                logger.info(
                    "Gerber export completed",
                    output_dir=output_dir,
                    total_files=len(files_generated),
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError, KicadCliError):
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _execute_netlist_export(
        self, schematic_file: str, output_format: str
    ) -> dict[str, Any]:
        """Execute KiCad netlist export via kicad-cli.

        Runs: kicad-cli sch export netlist --output <output_path> <schematic>
        """
        with tracer.start_as_current_span("kicad.execute_netlist_export") as span:
            span.set_attribute("kicad.schematic_file", schematic_file)
            span.set_attribute("kicad.output_format", output_format)

            await _check_kicad_cli(self.config.kicad_cli)

            cli_format, ext = NETLIST_FORMATS[output_format]
            stem = Path(schematic_file).stem
            folder = self._fresh_dir("", stem, "netlist")
            Path(folder).mkdir(parents=True, exist_ok=True)
            output_file = f"{folder}/{stem}.{ext}"

            cli_args = [
                "sch",
                "export",
                "netlist",
                "--format",
                cli_format,
                "--output",
                output_file,
                schematic_file,
            ]
            timeout = float(self.config.max_operation_time)

            try:
                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                if returncode != 0:
                    raise KicadCliError(returncode, stderr)

                # Counted from KiCad's own S-expression format only; another
                # format's counts are unknown, not zero.
                stats: tuple[int | None, int | None] = (None, None)
                if cli_format == "kicadsexpr":
                    stats = _parse_netlist_stats(output_file)
                total_nets, total_components = stats

                result = {
                    "output_file": output_file,
                    "total_nets": total_nets,
                    "total_components": total_components,
                    "format": output_format,
                }

                if total_nets is not None and total_components is not None:
                    span.set_attribute("kicad.netlist.total_nets", total_nets)
                    span.set_attribute("kicad.netlist.total_components", total_components)
                logger.info(
                    "Netlist export completed",
                    output_file=output_file,
                    total_nets=total_nets,
                    total_components=total_components,
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError, KicadCliError):
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def _execute_pin_mapping(
        self, schematic_file: str, component_filter: str | None
    ) -> dict[str, Any]:
        """Execute KiCad pin mapping extraction via kicad-cli.

        Exports a netlist and parses it to extract pin-to-net mappings for
        all components (or those matching component_filter).
        """
        with tracer.start_as_current_span("kicad.execute_pin_mapping") as span:
            span.set_attribute("kicad.schematic_file", schematic_file)
            if component_filter:
                span.set_attribute("kicad.component_filter", component_filter)

            await _check_kicad_cli(self.config.kicad_cli)

            # Export netlist in KiCad format for parsing
            netlist_fd, netlist_path = tempfile.mkstemp(suffix=".net", prefix="kicad_pinmap_")
            os.close(netlist_fd)

            try:
                cli_args = [
                    "sch",
                    "export",
                    "netlist",
                    "--format",
                    "kicadsexpr",
                    "--output",
                    netlist_path,
                    schematic_file,
                ]
                timeout = float(self.config.max_operation_time)

                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                if returncode != 0:
                    raise KicadCliError(returncode, stderr)

                # Parse the netlist for pin mapping
                components = _parse_pin_mapping_from_netlist(netlist_path, component_filter)

                total_pins = sum(len(c["pins"]) for c in components)

                result = {
                    "schematic_file": schematic_file,
                    "components": components,
                    "total_components": len(components),
                    "total_pins": total_pins,
                }

                span.set_attribute("kicad.pinmap.total_components", len(components))
                span.set_attribute("kicad.pinmap.total_pins", total_pins)
                logger.info(
                    "Pin mapping extraction completed",
                    schematic_file=schematic_file,
                    total_components=len(components),
                    total_pins=total_pins,
                )
                return result

            except (KicadCliNotFoundError, KicadCliTimeoutError, KicadCliError):
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise
            finally:
                if os.path.exists(netlist_path):
                    os.unlink(netlist_path)


# ------------------------------------------------------------------
# Report parsing helpers
# ------------------------------------------------------------------


def _parse_erc_violations(report: dict[str, Any], severity_filter: str) -> list[dict[str, Any]]:
    """Parse ERC violations from kicad-cli JSON report.

    KiCad 8 and 9 group violations by sheet (FORGE-686):
    {
        "source": "...",
        "kicad_version": "9.0.2",
        "sheets": [
            {
                "path": "/",
                "violations": [
                    {
                        "type": "pin_not_connected",
                        "description": "Pin not connected",
                        "severity": "error"|"warning",
                        "items": [
                            {"description": "Symbol U4 Pin 1 [VIN, Power input, Line]",
                             "pos": {"x": ..., "y": ...}, "uuid": "..."}
                        ]
                    }
                ]
            }
        ]
    }

    A top-level ``violations`` list is read as well, under the root sheet.
    Reading only that list made every report parse as clean.
    """
    groups: list[tuple[str, list[dict[str, Any]]]] = [
        (s.get("path", ""), s.get("violations", [])) for s in report.get("sheets", [])
    ]
    groups.append(("", report.get("violations", [])))
    result: list[dict[str, Any]] = []

    for sheet_path, raw_violations in groups:
        for v in raw_violations:
            severity = v.get("severity", "error")
            if severity_filter != "all" and severity != severity_filter:
                continue

            items = v.get("items", [])
            sheet = sheet_path
            component = ""
            pin = ""
            if items:
                first_item = items[0]
                sheet = first_item.get("sheet", sheet)
                component, pin = _erc_item_ref(first_item.get("description", ""))
                if len(items) > 1 and not pin:
                    pin = items[1].get("description", "")

            result.append(
                {
                    "rule_id": v.get("type", ""),
                    "severity": severity,
                    "message": v.get("description", ""),
                    "sheet": sheet,
                    "component": component,
                    "pin": pin,
                    "items": [i.get("description", "") for i in items],
                }
            )

    return result


_ERC_SYMBOL_PIN = re.compile(r"^Symbol\s+(\S+)\s+Pin\s+(\S+)")


def _erc_item_ref(desc: str) -> tuple[str, str]:
    """Component reference and pin number from an ERC item description.

    KiCad 9 writes ``Symbol U4 Pin 1 [VIN, Power input, Line]``; older
    reports used ``U1: pin VCC not connected``.
    """
    m = _ERC_SYMBOL_PIN.match(desc)
    if m:
        return m.group(1), m.group(2)
    if ":" in desc:
        return desc.split(":")[0].strip(), ""
    return "", ""


def _parse_drc_violations(report: dict[str, Any], severity_filter: str) -> list[dict[str, Any]]:
    """Parse DRC violations from kicad-cli JSON report.

    KiCad 8 JSON DRC report format:
    {
        "source": "...",
        "date": "...",
        "kicad_version": "...",
        "violations": [
            {
                "type": "...",
                "description": "...",
                "severity": "error"|"warning",
                "items": [
                    {
                        "description": "...",
                        "pos": {"x": ..., "y": ...},
                        "layer": "..."
                    }
                ]
            }
        ],
        "unresolved": [...]
    }
    """
    raw_violations = report.get("violations", [])
    result: list[dict[str, Any]] = []

    for v in raw_violations:
        severity = v.get("severity", "error")
        if severity_filter != "all" and severity != severity_filter:
            continue

        items = v.get("items", [])
        location: dict[str, Any] = {}
        if items:
            first_item = items[0]
            pos = first_item.get("pos", {})
            location = {
                "x": pos.get("x", 0),
                "y": pos.get("y", 0),
                "layer": first_item.get("layer", ""),
            }

        result.append(
            {
                "rule_id": v.get("type", ""),
                "severity": severity,
                "message": v.get("description", ""),
                "location": location,
            }
        )

    return result


def _inner_copper_layers(pcb_file: str) -> list[str]:
    """The inner copper layers (In1.Cu, ...) a ``.kicad_pcb`` declares, in order."""
    try:
        head = Path(pcb_file).read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return []
    found = re.findall(r'\(\s*\d+\s+"(In\d+\.Cu)"', head)
    return sorted(set(found), key=lambda n: int(n[2:-3]))


def _count_drc_unconnected(report: dict[str, Any]) -> int:
    """Count unconnected items from a DRC report.

    kicad-cli 8 and 9 write them under ``unconnected_items``; this read
    ``unresolved``, a key KiCad never writes, so an unrouted board always
    counted zero (FORGE-551). ``unresolved`` is still read for old fixtures.
    """
    items = report.get("unconnected_items")
    if items is None:
        items = report.get("unresolved", [])
    return len(items)


def _stage_board_with_rules(pcb_file: str, rule_set: str, directory: str) -> str:
    """Copy ``pcb_file`` into ``directory`` with ``rule_set`` as its ``.kicad_dru``.

    ``rule_set`` is a path to a rules file, or the rules text itself (it
    starts with ``(``). A project file beside the board is copied too, so
    its other settings still apply. Returns the staged board path.
    """
    source = Path(pcb_file)
    if not source.is_file():
        raise ValueError(f"pcb_file not found: {pcb_file}")
    text = rule_set.strip()
    if not text.startswith("("):
        rules_path = Path(rule_set)
        if not rules_path.is_file():
            raise ValueError(f"rule_set is neither a rules file nor rules text: {rule_set}")
        text = rules_path.read_text(encoding="utf-8")
    staged = Path(directory) / source.name
    shutil.copyfile(source, staged)
    project = source.with_suffix(".kicad_pro")
    if project.is_file():
        shutil.copyfile(project, staged.with_suffix(".kicad_pro"))
    staged.with_suffix(".kicad_dru").write_text(text, encoding="utf-8")
    return str(staged)


def _parse_bom_rows(file_path: str) -> list[dict[str, Any]]:
    """Rows of kicad-cli's BOM CSV (Refs, Value, Footprint, Qty, DNP).

    The parser used to read ``;``-separated rows; kicad-cli writes ``,``, so
    every row was one column and the totals were just the row count.
    """
    try:
        with open(file_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except (FileNotFoundError, UnicodeDecodeError):
        return []
    parts: list[dict[str, Any]] = []
    for row in rows:
        refs = [r.strip() for r in str(row.get("Refs", "")).split(",") if r.strip()]
        try:
            qty = int(row.get("Qty") or len(refs) or 1)
        except ValueError:
            qty = len(refs) or 1
        parts.append(
            {
                "references": refs,
                "value": row.get("Value", ""),
                "footprint": row.get("Footprint", ""),
                "quantity": qty,
                "dnp": bool(str(row.get("DNP", "")).strip()),
                "mpn": str(row.get("MPN") or "").strip(),
                "manufacturer": str(row.get("Manufacturer") or "").strip(),
            }
        )
    return parts


def _lines_without_mpn(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BOM lines that are placed (not DNP) and name no manufacturer part number.

    FORGE-694: a board's BOM passed every gate with its resistors and
    capacitors unsourced. A line a fab house is asked to place needs a part
    it can buy; the MPN symbol field is where the design says which.
    """
    return [p for p in parts if not p["dnp"] and not p.get("mpn")]


def _parse_bom_csv(file_path: str) -> tuple[int, int]:
    """(total components, BOM lines) of a kicad-cli BOM CSV."""
    parts = _parse_bom_rows(file_path)
    return sum(p["quantity"] for p in parts), len(parts)


def _list_gerber_files(output_dir: str) -> list[str]:
    """List Gerber files in the output directory."""
    gerber_extensions = {
        ".gtl",
        ".gbl",
        # Paste (stencil) layers: missing here, they were written and then
        # dropped from the export's file list (FORGE-703).
        ".gtp",
        ".gbp",
        ".gts",
        ".gbs",
        ".gto",
        ".gbo",
        ".gm1",
        ".gm2",
        ".drl",
        ".gbr",
    }
    try:
        files = []
        for entry in os.listdir(output_dir):
            ext = Path(entry).suffix.lower()
            # Inner copper comes out as .g1, .g2, ... (Protel extensions).
            if ext in gerber_extensions or re.fullmatch(r"\.g\d+", ext):
                files.append(entry)
        return sorted(files)
    except FileNotFoundError:
        return []


# kicad-cli writes the current name of a layer; older names still select it.
_LAYER_ALIASES = {
    "F.SilkS": "F.Silkscreen",
    "B.SilkS": "B.Silkscreen",
    "F.Adhes": "F.Adhesive",
    "B.Adhes": "B.Adhesive",
    "F.CrtYd": "F.Courtyard",
    "B.CrtYd": "B.Courtyard",
    "Dwgs.User": "User.Drawings",
    "Cmts.User": "User.Comments",
    "Eco1.User": "User.Eco1",
    "Eco2.User": "User.Eco2",
}


def _canonical_layer(name: str) -> str:
    return _LAYER_ALIASES.get(name, name)


def _gerber_file_layer(filename: str) -> str | None:
    """The layer a kicad-cli Gerber was plotted from.

    kicad-cli names each file ``<board>-<layer>.<ext>`` with the layer's dot
    written as an underscore: F.Paste -> ``board-F_Paste.gtp``.
    """
    stem = Path(filename).stem
    if "-" not in stem:
        return None
    token = stem.rsplit("-", 1)[1]
    if "_" not in token:
        return None
    return token.replace("_", ".", 1)


def _match_gerber_layers(layers: list[str], files: list[str]) -> tuple[dict[str, str], list[str]]:
    """({requested layer: file written for it}, requested layers with no file)."""
    written: dict[str, str] = {}
    for name in files:
        layer = _gerber_file_layer(name)
        if layer:
            written.setdefault(layer, name)
    found: dict[str, str] = {}
    missing: list[str] = []
    for layer in layers:
        name = written.get(_canonical_layer(layer))
        if name:
            found[layer] = name
        else:
            missing.append(layer)
    return found, missing


def _count_excellon_holes(path: Path) -> int:
    """Holes in an Excellon file: the coordinate lines after the header."""
    count = 0
    in_body = False
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line in ("%", "M95"):
            in_body = True
            continue
        if in_body and (line.startswith("X") or line.startswith("Y")):
            count += 1
    return count


def _parse_pos_csv(path: str) -> list[dict[str, Any]]:
    """kicad-cli's position CSV: Ref, Val, Package, PosX, PosY, Rot, Side."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                rows.append(
                    {
                        "reference": row["Ref"],
                        "value": row.get("Val", ""),
                        "package": row.get("Package", ""),
                        "x_mm": float(row["PosX"]),
                        "y_mm": float(row["PosY"]),
                        "rotation_deg": float(row["Rot"]),
                        "side": row.get("Side", ""),
                    }
                )
            except (KeyError, ValueError):
                continue
    return rows


def _read_netlist(file_path: str) -> list[Any] | None:
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return sexpr.loads(text)
    except sexpr.SExprError:
        return None


def _parse_netlist_stats(file_path: str) -> tuple[int, int]:
    """Nets and components in a KiCad S-expression netlist: (total_nets, total_components)."""
    tree = _read_netlist(file_path)
    if tree is None:
        return 0, 0
    nets = sexpr.find(tree, "nets")
    comps = sexpr.find(tree, "components")
    return (
        len(sexpr.children(nets, "net")) if nets else 0,
        len(sexpr.children(comps, "comp")) if comps else 0,
    )


def _parse_pin_mapping_from_netlist(
    netlist_path: str, component_filter: str | None
) -> list[dict[str, Any]]:
    """Each component's pins and the net on each, from a KiCad S-expression netlist."""
    tree = _read_netlist(netlist_path)
    if tree is None:
        return []
    components: dict[str, dict[str, Any]] = {}
    comps = sexpr.find(tree, "components")
    for comp in sexpr.children(comps, "comp") if comps else []:
        ref = sexpr.value(comp, "ref")
        if not ref or (component_filter and not ref.startswith(component_filter)):
            continue
        components[ref] = {
            "reference": ref,
            "value": sexpr.value(comp, "value"),
            "footprint": sexpr.value(comp, "footprint"),
            "pins": [],
        }
    nets = sexpr.find(tree, "nets")
    for net in sexpr.children(nets, "net") if nets else []:
        net_name = sexpr.value(net, "name")
        for node in sexpr.children(net, "node"):
            ref = sexpr.value(node, "ref")
            if ref not in components:
                continue
            pin = sexpr.value(node, "pin")
            components[ref]["pins"].append(
                {
                    "number": pin,
                    "name": sexpr.value(node, "pinfunction") or pin,
                    "type": sexpr.value(node, "pintype") or "passive",
                    "net": net_name,
                }
            )
    for entry in components.values():
        entry["pins"].sort(key=lambda p: (len(p["number"]), p["number"]))
    return list(components.values())
