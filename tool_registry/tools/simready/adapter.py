"""NVIDIA SimReady assets -- MCP server for finding and importing OpenUSD parts (FORGE-711).

SimReady Central is a catalogue of OpenUSD models with physics already on
them: mass per rigid body, joints, contact friction, semantic labels, and a
licence and sha256 for every file. This adapter finds them, checks them and
puts them where the twin can record them. It never writes to the twin
itself: the import's folder is recorded by ``twin.record_design_files``, the
same way the KiCad and firmware adapters' output is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.simready.config import SimReadyConfig
from tool_registry.tools.simready.importer import (
    attribution_line,
    import_package,
    inspect_package,
    read_import,
)
from tool_registry.tools.simready.index import SimReadyIndex
from tool_registry.tools.simready.package import Fetcher, HttpFetcher, parse_package_url

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.simready.adapter")

_PACKAGE_URL = {
    "type": "string",
    "description": (
        "The commit-pinned URL of the package's com.nvidia.simready.packaging.json, "
        "as simready.search returns it."
    ),
}


class SimReadyServer(McpToolServer):
    """Search, inspect and import NVIDIA SimReady packages."""

    def __init__(self, config: SimReadyConfig | None = None, fetcher: Fetcher | None = None):
        super().__init__(adapter_id="simready", version="0.1.0")
        self.config = config or SimReadyConfig()
        self._fetcher = fetcher or HttpFetcher(self.config.timeout_s)
        self._index = SimReadyIndex(self.config, self._fetcher)
        self._register_tools()

    @property
    def _max_bytes(self) -> int:
        return int(self.config.max_package_mb * 1024 * 1024)

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="simready.search",
                adapter_id="simready",
                name="Search SimReady assets",
                description=(
                    "Find OpenUSD part models in NVIDIA SimReady Central (robot arms, "
                    "grippers, motors, enclosures, racks, props) by words in their name, "
                    "optionally from one publisher (owner, e.g. FANUC-CORPORATION, "
                    "Robotiq-Official, nvidia). Each result is a commit-pinned package_url "
                    "and its dataset licence. Packages under a licence MetaForge does not "
                    "store without a person reading it are left out unless "
                    "include_unaccepted is true."
                ),
                capability="asset_search",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "e.g. 'gripper', 'compressor'"},
                        "owner": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "include_unaccepted": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                        "matched": {"type": "integer"},
                        "left_out_for_licence": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=120),
            ),
            handler=self.search,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="simready.inspect",
                adapter_id="simready",
                name="Inspect a SimReady package",
                description=(
                    "Check one package and report what it is, keeping nothing: its licence "
                    "as the dataset, the manifest, the licence file and the USD layer each "
                    "state it, and whether MetaForge accepts it; then, if accepted, every "
                    "USD layer fetched and checked against its sha256, and the stage's "
                    "units, bounding box (m), mass per rigid body (kg), joints, contact "
                    "friction and restitution, semantic labels, physics backends and "
                    "SimReady conformance. Downloads the layers (~60 MB) to a scratch folder."
                ),
                capability="asset_inspection",
                input_schema={
                    "type": "object",
                    "properties": {"package_url": _PACKAGE_URL},
                    "required": ["package_url"],
                },
                output_schema={"type": "object"},
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=300),
            ),
            handler=self.inspect,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="simready.import",
                adapter_id="simready",
                name="Import a SimReady package",
                description=(
                    "Fetch one package onto the adapter workspace, refusing it unless every "
                    "licence statement agrees on an accepted licence (CC-BY-4.0, CC0-1.0, "
                    "Apache-2.0, BSD-2/3-Clause, MIT) and every file matches its sha256. "
                    "Writes the USD layers, a GLB preview, LICENSE.txt, ATTRIBUTION.txt "
                    "and simready.json under simready/, and returns the folder and a "
                    "library_component body. Then record it with twin.record_design_files "
                    "(work_product_type='purchased_model', file_path=folder), which reads the "
                    "licence and mass from the folder, and register library_component with "
                    "library.register_component to reuse it. Textures are left out unless "
                    "include_textures is true (they can be a gigabyte)."
                ),
                capability="asset_import",
                input_schema={
                    "type": "object",
                    "properties": {
                        "package_url": _PACKAGE_URL,
                        "include_textures": {"type": "boolean"},
                    },
                    "required": ["package_url"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string"},
                        "record": {"type": "object"},
                        "library_component": {"type": "object"},
                        "preview": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=2048, max_cpu_seconds=600),
            ),
            handler=self.import_,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="simready.attribution",
                adapter_id="simready",
                name="SimReady attribution",
                description=(
                    "The credit lines the licences of imported SimReady parts ask for, for a "
                    "README, a release package or a manufacturing pack. Pass the folders "
                    "simready.import returned."
                ),
                capability="asset_attribution",
                input_schema={
                    "type": "object",
                    "properties": {"folders": {"type": "array", "items": {"type": "string"}}},
                    "required": ["folders"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"lines": {"type": "array"}, "text": {"type": "string"}},
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=10),
            ),
            handler=self.attribution,
        )

    async def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = int(arguments.get("limit") or 20)
        with tracer.start_as_current_span("simready.search") as span:
            span.set_attribute("simready.query", query)
            try:
                result = await self._index.search(
                    query,
                    owner=arguments.get("owner") or None,
                    limit=max(1, min(limit, 100)),
                    include_unaccepted=bool(arguments.get("include_unaccepted")),
                )
            except Exception as exc:
                span.record_exception(exc)
                raise
            span.set_attribute("simready.results", len(result["results"]))
        logger.info("simready_search", query=query, results=len(result["results"]))
        return result

    async def inspect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ref = parse_package_url(str(arguments.get("package_url") or ""))
        with tracer.start_as_current_span("simready.inspect") as span:
            span.set_attribute("simready.package", ref.url)
            try:
                licence = await self._index.dataset_licence(ref.dataset_id)
                return await inspect_package(ref, self._fetcher, licence, max_bytes=self._max_bytes)
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def import_(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ref = parse_package_url(str(arguments.get("package_url") or ""))
        with tracer.start_as_current_span("simready.import") as span:
            span.set_attribute("simready.package", ref.url)
            try:
                licence = await self._index.dataset_licence(ref.dataset_id)
                return await import_package(
                    ref,
                    self._fetcher,
                    licence,
                    Path(self.config.workspace_dir),
                    include_textures=bool(arguments.get("include_textures")),
                    max_bytes=self._max_bytes,
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def attribution(self, arguments: dict[str, Any]) -> dict[str, Any]:
        folders = arguments.get("folders")
        if not isinstance(folders, list) or not folders:
            raise ValueError("folders must list the folders simready.import returned")
        workspace = Path(self.config.workspace_dir)
        records = [read_import(workspace, str(f)) for f in folders]
        lines = [r.get("attribution") or attribution_line(r) for r in records]
        return {
            "lines": lines,
            "licences": sorted({str(r.get("licence")) for r in records}),
            "text": "Third-party models\n\n" + "\n".join(f"- {line}" for line in lines),
        }
