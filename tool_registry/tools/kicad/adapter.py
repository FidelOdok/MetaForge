"""KiCad PCB/schematic tool adapter -- MCP server for EDA validation."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.kicad.config import KicadConfig

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.kicad")


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
                                "Layers to export (default: all copper + mask + silk + edge)"
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

                errors = sum(1 for v in violations if v["severity"] == "error")
                warnings = sum(1 for v in violations if v["severity"] == "warning")

                result = {
                    "schematic_file": schematic_file,
                    "total_violations": len(violations),
                    "errors": errors,
                    "warnings": warnings,
                    "violations": violations,
                    "passed": len(violations) == 0,
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
        self, pcb_file: str, severity_filter: str, rule_set: str | None
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

            try:
                cli_args = [
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--severity-all",
                    "--output",
                    report_path,
                    pcb_file,
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

                errors = sum(1 for v in violations if v["severity"] == "error")
                warnings = sum(1 for v in violations if v["severity"] == "warning")

                result = {
                    "pcb_file": pcb_file,
                    "total_violations": len(violations),
                    "errors": errors,
                    "warnings": warnings,
                    "violations": violations,
                    "unconnected_items": unconnected,
                    "passed": len(violations) == 0 and unconnected == 0,
                }

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
            output_file = f"{self.config.work_dir}/{stem}_bom.{output_format}"

            cli_args = [
                "sch",
                "export",
                "bom",
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

                # Parse the BOM output to count components
                total_components, unique_parts = _parse_bom_csv(output_file)

                result = {
                    "output_file": output_file,
                    "total_components": total_components,
                    "unique_parts": unique_parts,
                    "format": output_format,
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

            cli_args = [
                "pcb",
                "export",
                "gerbers",
                "--output",
                output_dir,
                pcb_file,
            ]
            # Add layer arguments
            for layer in layers:
                cli_args.extend(["--layers", layer])

            timeout = float(self.config.max_operation_time)

            try:
                returncode, stdout, stderr = await _run_kicad_cli(
                    self.config.kicad_cli, cli_args, timeout
                )

                if returncode != 0:
                    raise KicadCliError(returncode, stderr)

                # List generated files
                files_generated = _list_gerber_files(output_dir)

                result = {
                    "output_dir": output_dir,
                    "files_generated": files_generated,
                    "total_files": len(files_generated),
                    "layers_exported": layers,
                }

                span.set_attribute("kicad.gerber.total_files", len(files_generated))
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

            format_ext_map = {"kicad": "net", "spice": "cir", "cadstar": "cdx"}
            ext = format_ext_map.get(output_format, "net")
            stem = Path(schematic_file).stem
            output_file = f"{self.config.work_dir}/{stem}.{ext}"

            cli_args = [
                "sch",
                "export",
                "netlist",
                "--format",
                output_format,
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

                # Parse the netlist to count nets and components
                total_nets, total_components = _parse_netlist_stats(output_file)

                result = {
                    "output_file": output_file,
                    "total_nets": total_nets,
                    "total_components": total_components,
                    "format": output_format,
                }

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
                    "kicad",
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

    KiCad 8 JSON ERC report format:
    {
        "source": "...",
        "date": "...",
        "kicad_version": "...",
        "sheets": [...],
        "violations": [
            {
                "type": "...",
                "description": "...",
                "severity": "error"|"warning",
                "items": [
                    {"description": "...", "sheet": "...", "pos": {"x": ..., "y": ...}}
                ]
            }
        ]
    }
    """
    raw_violations = report.get("violations", [])
    result: list[dict[str, Any]] = []

    for v in raw_violations:
        severity = v.get("severity", "error")
        if severity_filter != "all" and severity != severity_filter:
            continue

        # Extract component and sheet info from items
        items = v.get("items", [])
        sheet = ""
        component = ""
        pin = ""
        if items:
            first_item = items[0]
            sheet = first_item.get("sheet", "")
            desc = first_item.get("description", "")
            # Try to extract component reference from description
            if ":" in desc:
                component = desc.split(":")[0].strip()
            if len(items) > 1:
                pin = items[1].get("description", "")

        result.append(
            {
                "rule_id": v.get("type", ""),
                "severity": severity,
                "message": v.get("description", ""),
                "sheet": sheet,
                "component": component,
                "pin": pin,
            }
        )

    return result


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


def _count_drc_unconnected(report: dict[str, Any]) -> int:
    """Count unconnected items from DRC report."""
    unresolved = report.get("unresolved", [])
    return len(unresolved)


def _parse_bom_csv(file_path: str) -> tuple[int, int]:
    """Parse a KiCad BOM CSV to count total components and unique parts.

    Returns (total_components, unique_parts).
    """
    try:
        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)
    except (FileNotFoundError, UnicodeDecodeError):
        return 0, 0

    total = 0
    unique_values: set[str] = set()

    for row in rows:
        # KiCad BOM CSV typically has Qty column
        qty_str = row.get("Qty", row.get("Quantity", "1"))
        try:
            qty = int(qty_str)
        except (ValueError, TypeError):
            qty = 1
        total += qty
        value = row.get("Value", row.get("value", ""))
        if value:
            unique_values.add(value)

    return total, len(unique_values) if unique_values else len(rows)


def _list_gerber_files(output_dir: str) -> list[str]:
    """List Gerber files in the output directory."""
    gerber_extensions = {
        ".gtl",
        ".gbl",
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
            if ext in gerber_extensions or entry.endswith(".gbr"):
                files.append(entry)
        return sorted(files)
    except FileNotFoundError:
        return []


def _parse_netlist_stats(file_path: str) -> tuple[int, int]:
    """Parse a KiCad netlist file to count nets and components.

    Returns (total_nets, total_components).
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return 0, 0

    # Count (net entries and (comp entries in KiCad netlist format
    net_count = content.count("(net ")
    comp_count = content.count("(comp ")

    return net_count, comp_count


def _parse_pin_mapping_from_netlist(
    netlist_path: str, component_filter: str | None
) -> list[dict[str, Any]]:
    """Parse pin mapping from a KiCad netlist file.

    This is a simplified parser for the KiCad S-expression netlist format.
    Returns a list of component dicts with their pin-to-net mappings.
    """
    try:
        with open(netlist_path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return []

    # This is a simplified extraction -- a full parser would use S-expression parsing.
    # We extract basic component info from the netlist structure.
    components: list[dict[str, Any]] = []

    # Split by component blocks
    comp_sections = content.split("(comp ")
    for section in comp_sections[1:]:  # skip the preamble
        ref = _extract_field(section, "ref")
        value = _extract_field(section, "value")
        footprint = _extract_field(section, "footprint")

        if not ref:
            continue

        if component_filter and not ref.startswith(component_filter):
            continue

        components.append(
            {
                "reference": ref,
                "value": value,
                "footprint": footprint,
                "pins": [],
            }
        )

    # Parse net sections to fill in pin mapping
    net_sections = content.split("(net ")
    for section in net_sections[1:]:
        # Extract net name from (name "...") field in this section
        net_name = _extract_field(section, "name")

        # Find node references in this net section
        node_sections = section.split("(node ")
        for node in node_sections[1:]:
            node_ref = _extract_field(node, "ref")
            node_pin = _extract_field(node, "pin")
            pin_name = _extract_field(node, "pinfunction")
            pin_type = _extract_field(node, "pintype")

            if node_ref and node_pin:
                for comp in components:
                    if comp["reference"] == node_ref:
                        comp["pins"].append(
                            {
                                "number": node_pin,
                                "name": pin_name or node_pin,
                                "type": pin_type or "passive",
                                "net": net_name,
                            }
                        )

    return components


def _extract_field(text: str, field_name: str) -> str:
    """Extract a field value from an S-expression fragment.

    Looks for (field_name value) or (field_name "value") patterns.
    """
    marker = f"({field_name} "
    idx = text.find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    if start >= len(text):
        return ""
    if text[start] == '"':
        end = text.find('"', start + 1)
        return text[start + 1 : end] if end > start else ""
    end = text.find(")", start)
    return text[start:end].strip() if end > start else ""
