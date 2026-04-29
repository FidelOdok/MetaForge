"""Twin MCP adapter — five tools exposing the digital-twin graph (MET-382).

Without these tools, harnesses route every question to ``knowledge.search``
(LightRAG) by default and end up hallucinating structural facts. The
five tools below are the "structural" surface — they answer "which X
links to which Y" questions against the authoritative Neo4j graph.

Tool descriptions are tuned for LLM tool-picking. Bad descriptions →
wrong triage → harness loops. Each description states the question
shape it answers in plain English so the LLM can match intent fast.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.twin.queries import (
    detect_mutations,
    serialise_node,
    serialise_subgraph,
    serialise_violation,
)

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.twin.adapter")


class TwinServer(McpToolServer):
    """MCP adapter wrapping ``TwinAPI`` for harness consumption."""

    def __init__(self, twin: Any, *, allow_mutations: bool = False) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self._twin = twin
        # ``query_cypher`` rejects mutating Cypher unless this flag is
        # explicitly set at adapter construction. Kept on the adapter
        # rather than per-call so callers can't escalate by passing a
        # parameter; flag must be set by the deployment.
        self._allow_mutations = allow_mutations
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registrations
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.get_node",
                adapter_id="twin",
                name="Get Twin Node",
                description=(
                    "Fetch a graph node by id from the digital twin. Returns "
                    "properties + first-hop neighbours. Use when you have a "
                    "node id and want to inspect it."
                ),
                capability="twin_inspect",
                input_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the node to fetch.",
                        },
                    },
                    "required": ["node_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "object"},
                        "neighbours": {"type": "array"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=256, max_cpu_seconds=10, max_disk_mb=32
                ),
            ),
            handler=self.get_node,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.thread_for",
                adapter_id="twin",
                name="Walk Digital Thread",
                description=(
                    "Walk the digital thread starting from a node. Returns "
                    "connected Requirements, DesignElements, BOMItems, Tests, "
                    "Evidence as a subgraph. Use for 'what depends on / what "
                    "tests / what evidence' questions."
                ),
                capability="twin_thread",
                input_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string", "format": "uuid"},
                        "depth": {
                            "type": "integer",
                            "default": 3,
                            "minimum": 1,
                            "maximum": 10,
                            "description": "Maximum hop depth from the root node.",
                        },
                    },
                    "required": ["node_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "nodes": {"type": "array"},
                        "edges": {"type": "array"},
                        "root_id": {"type": "string"},
                        "depth": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=512, max_cpu_seconds=30, max_disk_mb=64
                ),
            ),
            handler=self.thread_for,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.find_by_property",
                adapter_id="twin",
                name="Find Node by Property",
                description=(
                    "Look up nodes by indexed property (e.g., BOMItem by MPN). "
                    "Use when you have a known property value and want the "
                    "structured node — faster and more precise than knowledge.search."
                ),
                capability="twin_lookup",
                input_schema={
                    "type": "object",
                    "properties": {
                        "node_type": {
                            "type": "string",
                            "description": (
                                "Node label / WorkProductType to filter on "
                                "(e.g. 'BOMItem', 'Component', 'WorkProduct')."
                            ),
                        },
                        "property": {
                            "type": "string",
                            "description": "Property name to match.",
                        },
                        "value": {
                            "description": "Property value to match (any JSON type).",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 25,
                            "minimum": 1,
                            "maximum": 200,
                        },
                    },
                    "required": ["node_type", "property", "value"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "nodes": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=256, max_cpu_seconds=10, max_disk_mb=32
                ),
            ),
            handler=self.find_by_property,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.constraint_violations",
                adapter_id="twin",
                name="List Current Constraint Violations",
                description=(
                    "Return current constraint violations for the project, "
                    "severity-ordered (error > warning > info). Use to ask "
                    "'what's currently broken?' before proposing changes."
                ),
                capability="twin_constraints",
                input_schema={
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "default": "main",
                            "description": "Branch to evaluate against. Default: main.",
                        },
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "violations": {"type": "array"},
                        "warnings": {"type": "array"},
                        "passed": {"type": "boolean"},
                        "evaluated_count": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=512, max_cpu_seconds=30, max_disk_mb=64
                ),
            ),
            handler=self.constraint_violations,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.query_cypher",
                adapter_id="twin",
                name="Query Twin via Cypher",
                description=(
                    "Power-user escape hatch: run a raw Cypher query against "
                    "the digital twin. READ-ONLY by default — mutating "
                    "queries (CREATE/DELETE/SET/MERGE/...) are rejected unless "
                    "the adapter is started with --allow-mutations. Every call "
                    "is logged to audit. Use when the typed tools above can't "
                    "express your question."
                ),
                capability="twin_cypher",
                input_schema={
                    "type": "object",
                    "properties": {
                        "cypher": {
                            "type": "string",
                            "description": "The Cypher query to execute.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Bind parameters for the query.",
                            "default": {},
                        },
                    },
                    "required": ["cypher"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=512, max_cpu_seconds=60, max_disk_mb=64
                ),
            ),
            handler=self.query_cypher,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def get_node(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_id = arguments.get("node_id")
        if not raw_id:
            raise ValueError("node_id is required")
        try:
            node_id = UUID(str(raw_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"node_id must be a valid UUID: {exc}") from exc

        with tracer.start_as_current_span("twin.get_node") as span:
            span.set_attribute("twin.node_id", str(node_id))
            # ``get_subgraph`` with depth=1 returns the node + its first-hop
            # neighbours in a single round-trip. Beats hand-rolled get + edges.
            subgraph = await self._twin.get_subgraph(node_id, depth=1)
            sg = serialise_subgraph(subgraph)

            # Extract the root node from nodes — others are neighbours.
            root_id_str = str(node_id)
            root: dict[str, Any] | None = None
            neighbours: list[dict[str, Any]] = []
            for n in sg.get("nodes", []) or []:
                if str(n.get("id")) == root_id_str:
                    root = n
                else:
                    neighbours.append(n)

            return {
                "node": root,
                "neighbours": neighbours,
                "edges": sg.get("edges", []),
            }

    async def thread_for(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_id = arguments.get("node_id")
        if not raw_id:
            raise ValueError("node_id is required")
        try:
            node_id = UUID(str(raw_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"node_id must be a valid UUID: {exc}") from exc

        depth = int(arguments.get("depth", 3))
        if depth < 1 or depth > 10:
            raise ValueError("depth must be between 1 and 10 inclusive")

        with tracer.start_as_current_span("twin.thread_for") as span:
            span.set_attribute("twin.node_id", str(node_id))
            span.set_attribute("twin.depth", depth)
            subgraph = await self._twin.get_subgraph(node_id, depth=depth)
            return serialise_subgraph(subgraph)

    async def find_by_property(self, arguments: dict[str, Any]) -> dict[str, Any]:
        node_type = arguments.get("node_type", "")
        prop = arguments.get("property", "")
        value = arguments.get("value")
        limit = int(arguments.get("limit", 25))

        if not node_type or not isinstance(node_type, str):
            raise ValueError("node_type is required and must be a string")
        if not prop or not isinstance(prop, str):
            raise ValueError("property is required and must be a string")
        if value is None:
            raise ValueError("value is required")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200 inclusive")

        # Reject anything that doesn't look like a Cypher-safe label /
        # identifier — backticks alone aren't enough since the harness
        # could send a value with them. Match what the Twin's own indexer
        # accepts (alphanumeric + underscore, must start with a letter).
        import re as _re

        if not _re.match(r"^[A-Za-z][A-Za-z0-9_]*$", node_type):
            raise ValueError(f"invalid node_type label: {node_type!r}")
        if not _re.match(r"^[A-Za-z][A-Za-z0-9_]*$", prop):
            raise ValueError(f"invalid property name: {prop!r}")

        with tracer.start_as_current_span("twin.find_by_property") as span:
            span.set_attribute("twin.node_type", node_type)
            span.set_attribute("twin.property", prop)
            span.set_attribute("twin.limit", limit)

            cypher = f"MATCH (n:`{node_type}` {{`{prop}`: $value}}) RETURN n LIMIT $limit"
            rows = await self._twin.query_cypher(cypher, {"value": value, "limit": limit})
            nodes: list[dict[str, Any]] = []
            for row in rows or []:
                # Neo4j returns each row as a dict with the bound name.
                if isinstance(row, dict) and "n" in row:
                    nodes.append(serialise_node(row["n"]))
                else:
                    nodes.append(serialise_node(row))
            span.set_attribute("twin.result_count", len(nodes))
            return {"nodes": nodes, "count": len(nodes)}

    async def constraint_violations(self, arguments: dict[str, Any]) -> dict[str, Any]:
        branch = arguments.get("branch", "main")
        if not isinstance(branch, str):
            raise ValueError("branch must be a string")

        with tracer.start_as_current_span("twin.constraint_violations") as span:
            span.set_attribute("twin.branch", branch)
            result = await self._twin.evaluate_constraints(branch=branch)
            span.set_attribute("twin.passed", result.passed)
            span.set_attribute("twin.violation_count", len(result.violations))
            span.set_attribute("twin.warning_count", len(result.warnings))

            # Severity-ordered: errors first, warnings second. Within each
            # tier preserve the engine's order (already deterministic).
            return {
                "passed": result.passed,
                "violations": [serialise_violation(v) for v in result.violations],
                "warnings": [serialise_violation(v) for v in result.warnings],
                "evaluated_count": result.evaluated_count,
            }

    async def query_cypher(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cypher = arguments.get("cypher", "")
        params = arguments.get("params") or {}
        if not isinstance(cypher, str) or not cypher.strip():
            raise ValueError("cypher is required and must be a non-empty string")
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")

        mutations = detect_mutations(cypher)
        if mutations and not self._allow_mutations:
            # Audit even on rejection — the attempt itself is signal.
            logger.warning(
                "twin_query_cypher_rejected",
                reason="mutating_cypher_disabled",
                mutations=mutations,
                cypher_preview=cypher[:200],
            )
            raise ValueError(
                "mutating Cypher rejected: this adapter is read-only. "
                f"Detected keywords: {mutations}. Start the adapter with "
                "--allow-mutations to permit writes."
            )

        with tracer.start_as_current_span("twin.query_cypher") as span:
            span.set_attribute("twin.cypher_length", len(cypher))
            span.set_attribute("twin.mutation_keywords", str(mutations))
            # Audit log every call (not just rejected ones) — the
            # power-user escape hatch is exactly the surface that
            # warrants traceability.
            logger.info(
                "twin_query_cypher",
                cypher_preview=cypher[:200],
                param_keys=sorted(params.keys()),
                mutations=mutations,
                allow_mutations=self._allow_mutations,
            )
            rows = await self._twin.query_cypher(cypher, params)
            row_list = list(rows or [])
            span.set_attribute("twin.row_count", len(row_list))
            return {"rows": row_list, "count": len(row_list)}
