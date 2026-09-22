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

from mcp_core.context import current_context
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

    def __init__(
        self,
        twin: Any,
        *,
        allow_mutations: bool = False,
        decision_recorder: Any = None,
        geometry_recorder: Any = None,
        proposal_recorder: Any = None,
        constraint_recorder: Any = None,
        engineering_entity_recorder: Any = None,
        document_recorder: Any = None,
        blob_stager: Any = None,
        design_sketch_recorder: Any = None,
        hazard_analysis_recorder: Any = None,
        system_architecture_recorder: Any = None,
        technical_drawing_recorder: Any = None,
        compliance_checklist_recorder: Any = None,
        procurement_record_recorder: Any = None,
        component_recorder: Any = None,
        evidence_recorder: Any = None,
        claim_recorder: Any = None,
    ) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self._twin = twin
        # ``query_cypher`` rejects mutating Cypher unless this flag is
        # explicitly set at adapter construction. Kept on the adapter
        # rather than per-call so callers can't escalate by passing a
        # parameter; flag must be set by the deployment.
        self._allow_mutations = allow_mutations
        # MET-495: an injected async ``record(...)`` callable (built in the
        # api_gateway layer). When present, ``twin.record_decision`` is
        # registered. None keeps tool_registry free of api_gateway imports.
        self._decision_recorder = decision_recorder
        # MET-529: an injected async ``record(...)`` that persists authored CAD
        # geometry (STEP bytes) as a CAD_MODEL work product — MinIO blob + twin
        # node + project link — so it renders in the viewer. Same injection seam
        # as decision_recorder; None keeps tool_registry free of api_gateway.
        self._geometry_recorder = geometry_recorder
        # MET-548: an injected async ``propose(...)`` that files a reviewable
        # design-change proposal (HITL) instead of mutating the twin directly.
        # Built in api_gateway over the ApprovalWorkflow; None keeps
        # tool_registry free of api_gateway imports.
        self._proposal_recorder = proposal_recorder
        # MET-582: an injected async ``record(...)`` that persists a batch of
        # structured, evaluable constraints + a constraint_set work product.
        # Same injection seam as decision_recorder; None keeps tool_registry
        # free of api_gateway imports.
        self._constraint_recorder = constraint_recorder
        # FORGE-45 (epic FORGE-35): an injected async ``record(...)`` that
        # persists one EngineeringEntity node (intent/need/objective/
        # assumption/question/risk/verification_case/evidence), optionally
        # linked to parent(s) it derives_from/satisfies/etc. Same injection
        # seam as constraint_recorder; None keeps tool_registry free of
        # api_gateway imports.
        self._engineering_entity_recorder = engineering_entity_recorder
        # MET-588: an injected async ``record(...)`` (make_document_recorder)
        # that persists an arbitrary text/markdown artifact as a PRD/
        # DOCUMENTATION work product — MinIO blob + twin node + project link.
        # Fixes the gap where the chat agent had no direct way to save a
        # requirements/notes document and fell back to twin.propose_change,
        # whose apply-on-approve executor only implements a `record_decision`
        # action — every other diff shape (including one the model invents,
        # e.g. `create_work_product`) silently no-ops even after approval.
        # Same injection seam as decision_recorder; None keeps tool_registry
        # free of api_gateway imports.
        self._document_recorder = document_recorder
        # MET-618: an injected async ``stage(node_id) -> dict`` that resolves a
        # committed work product's blob and writes it into the shared adapter
        # workspace, returning a local file_path. Without it, an agent has no
        # way back to a work product's actual content once its authoring
        # session is gone — every CAD/FEA tool needs a file_path, not a node
        # id. Same injection seam as decision_recorder; None keeps
        # tool_registry free of api_gateway imports.
        self._blob_stager = blob_stager
        # Follow-up to MET-740/747: an injected async ``commit(...)`` that
        # persists a self-contained HTML reference sketch as a
        # DESIGN_SKETCH work product -- the human-approval gate before
        # committing to real CAD/build work. Same injection seam as
        # geometry_recorder; None keeps tool_registry free of api_gateway.
        self._design_sketch_recorder = design_sketch_recorder
        # Lifecycle-mapping follow-up (MET-747): five structured-document
        # work-product types sharing api_gateway.twin.structured_document_
        # recorder's persistence helper. Same injection seam as every
        # recorder above; None keeps tool_registry free of api_gateway.
        self._hazard_analysis_recorder = hazard_analysis_recorder
        self._system_architecture_recorder = system_architecture_recorder
        self._technical_drawing_recorder = technical_drawing_recorder
        self._compliance_checklist_recorder = compliance_checklist_recorder
        self._procurement_record_recorder = procurement_record_recorder
        # MET-436 follow-up: an injected async ``record(...)`` (make_component_
        # recorder) that persists one chosen component.search_* result as a
        # BOMItem graph node + project link. Without it, a search result was
        # pure chat output -- no reviewable, versioned trace in the twin at
        # all. Same injection seam as decision_recorder; None keeps
        # tool_registry free of api_gateway imports.
        self._component_recorder = component_recorder
        # FORGE-64 (epic FORGE-35, Phase 6: Evidence Integration): an
        # injected async ``record(...)`` (make_evidence_recorder) that
        # persists a tool-generated Evidence EngineeringEntity -- real
        # producer/inputs/result (hashed), supports/contradicts edges, and
        # revision-pinned valid_against dependencies via FORGE-59's
        # StalenessEngine. Same injection seam as decision_recorder; None
        # keeps tool_registry free of api_gateway imports.
        self._evidence_recorder = evidence_recorder
        # FORGE-65 (epic FORGE-35, Phase 6): an injected async ``record(...)``
        # (make_claim_recorder) that persists a requirement-satisfaction
        # claim -- a real graph edge from an artefact to the requirement it
        # satisfies, citing evidence ids in its metadata. Same injection
        # seam as decision_recorder; None keeps tool_registry free of
        # api_gateway imports.
        self._claim_recorder = claim_recorder
        self._register_tools()
        if decision_recorder is not None:
            self._register_record_decision()
        if geometry_recorder is not None:
            self._register_commit_geometry()
        if proposal_recorder is not None:
            self._register_propose_change()
        if constraint_recorder is not None:
            self._register_record_constraint_set()
        if engineering_entity_recorder is not None:
            self._register_record_engineering_entity()
        if document_recorder is not None:
            self._register_record_document()
        if design_sketch_recorder is not None:
            self._register_commit_design_sketch()
        if hazard_analysis_recorder is not None:
            self._register_commit_hazard_analysis()
        if system_architecture_recorder is not None:
            self._register_commit_system_architecture()
        if technical_drawing_recorder is not None:
            self._register_commit_technical_drawing()
        if compliance_checklist_recorder is not None:
            self._register_commit_compliance_checklist()
        if procurement_record_recorder is not None:
            self._register_commit_procurement_record()
        if blob_stager is not None:
            self._register_stage_work_product_file()
        if component_recorder is not None:
            self._register_record_component_selection()
        if evidence_recorder is not None:
            self._register_record_evidence()
        if claim_recorder is not None:
            self._register_record_claim()

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
                        "edge_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Restrict the walk to these edge types (e.g. "
                                "['implements', 'derives_from', 'motivates'] to render "
                                "just the Engineering Intent & Requirements Harness's "
                                "decomposition chain). Omit to walk every edge type."
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming", "both"],
                            "default": "outgoing",
                            "description": (
                                "Edge direction to traverse from the root. Most "
                                "traceability edges point child-to-parent (e.g. "
                                "evidence SATISFIES a requirement), so a root "
                                "that is usually an edge *target* -- a "
                                "requirement or constraint -- needs 'incoming' "
                                "or 'both' to see anything connected to it."
                            ),
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
                    "'what's currently broken?' before proposing changes. "
                    "Pass project_id (FORGE-75) to scope to one project -- "
                    "without it, falls back to the calling session's active "
                    "project if one is set (FORGE-74), and otherwise "
                    "evaluates every constraint across every project (admin "
                    "path)."
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
                        "project_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": (
                                "Project to scope violations to. Takes "
                                "precedence over the calling session's ambient "
                                "project context, if any."
                            ),
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

        # FORGE-47: plain strings, not EdgeType members -- tool_registry (layer
        # 3) may not import twin_core (layer 4+). EdgeType is a StrEnum, so an
        # EdgeType instance compares equal to its raw string value; the real
        # graph engines' `edge.edge_type not in edge_types` filter (in-memory
        # and Neo4j alike) works correctly against plain strings with no
        # conversion needed here. An unrecognised string just matches nothing.
        raw_edge_types = arguments.get("edge_types")
        edge_types: list[str] | None = None
        if raw_edge_types is not None:
            if not isinstance(raw_edge_types, list) or not all(
                isinstance(t, str) and t.strip() for t in raw_edge_types
            ):
                raise ValueError("edge_types must be a list of non-empty strings")
            edge_types = raw_edge_types

        # FORGE-72: most traceability edges point child-to-parent (evidence
        # SATISFIES a requirement, a low-level constraint IMPLEMENTS a
        # high-level one) -- a root that is usually an edge *target*, like a
        # requirement or constraint, sees nothing with outgoing-only
        # traversal. direction lets a caller opt into 'incoming' or 'both';
        # default stays 'outgoing' so existing callers see no behavior change.
        direction = arguments.get("direction", "outgoing")
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError("direction must be one of: outgoing, incoming, both")

        with tracer.start_as_current_span("twin.thread_for") as span:
            span.set_attribute("twin.node_id", str(node_id))
            span.set_attribute("twin.depth", depth)
            span.set_attribute("twin.direction", direction)
            if edge_types:
                span.set_attribute("twin.edge_types", ",".join(edge_types))
            subgraph = await self._twin.get_subgraph(
                node_id, depth=depth, edge_types=edge_types, direction=direction
            )
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

            # MET-441: when the call context names a project, inject a
            # project_id binding so the Cypher only returns rows in
            # that tenant. Safe because `project_id` is a parameter, not
            # interpolated text. Without a context, no filter is added
            # (admin path).
            ctx_project_id = current_context().project_id
            params: dict[str, Any] = {"value": value, "limit": limit}
            if ctx_project_id is not None:
                cypher = (
                    f"MATCH (n:`{node_type}` "
                    f"{{`{prop}`: $value, project_id: $project_id}}) "
                    f"RETURN n LIMIT $limit"
                )
                params["project_id"] = str(ctx_project_id)
                span.set_attribute("mcp.project_id", str(ctx_project_id))
            else:
                cypher = f"MATCH (n:`{node_type}` {{`{prop}`: $value}}) RETURN n LIMIT $limit"

            rows = await self._twin.query_cypher(cypher, params)
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

        # FORGE-75: an explicit project_id argument always wins -- the chat
        # harness's own project brief tells the agent its project_id directly
        # (the same convention twin.commit_geometry/twin.record_decision
        # already use) and that reaches this tool as a real call argument,
        # unlike the ambient mcp_core.context binding FORGE-74 also added,
        # which nothing in the live chat path currently populates.
        raw_project_id = arguments.get("project_id")
        explicit_project_id: UUID | None = None
        if raw_project_id is not None:
            if not isinstance(raw_project_id, str):
                raise ValueError("project_id must be a string")
            try:
                explicit_project_id = UUID(raw_project_id)
            except ValueError as exc:
                raise ValueError(f"project_id must be a valid UUID: {exc}") from exc

        with tracer.start_as_current_span("twin.constraint_violations") as span:
            span.set_attribute("twin.branch", branch)
            # FORGE-74: same MET-441 pattern as find_by_property -- when the
            # call context names a project, scope to it so one project's
            # violations can't be reported against another's. Without a
            # context, no filter is added (admin path) -- same as before.
            scope_project_id = explicit_project_id or current_context().project_id
            if scope_project_id is not None:
                span.set_attribute("mcp.project_id", str(scope_project_id))
            result = await self._twin.evaluate_constraints(
                branch=branch, project_id=scope_project_id
            )
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

    # ------------------------------------------------------------------
    # twin.record_decision (MET-495)
    # ------------------------------------------------------------------

    def _register_record_decision(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_decision",
                adapter_id="twin",
                name="Record Design Decision",
                description=(
                    "Persist a design decision as a first-class DESIGN_DECISION "
                    "work product: renders an ADR-style markdown doc, stores it "
                    "in MinIO, and links it to a project. Use to capture WHY a "
                    "choice was made (with alternatives considered)."
                ),
                capability="twin_decision",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Short decision title.",
                        },
                        "rationale": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Why this decision was made.",
                        },
                        "alternatives": {
                            "type": "array",
                            "description": "Options considered + why rejected.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "option": {"type": "string"},
                                    "reason_rejected": {"type": "string"},
                                },
                            },
                        },
                        "parent_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "FORGE-61: requirement(s)/objective(s) this decision's "
                                "selected concept satisfies -- by name/title or node id."
                            ),
                        },
                        "relation": {
                            "type": "string",
                            "description": (
                                "Edge type linking this decision to each parent_ref "
                                "(default 'satisfies')."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                        "supersedes": {
                            "type": "string",
                            "description": "Node id of a decision this replaces.",
                        },
                    },
                    "required": ["title", "rationale"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "parent_refs": {"type": "array", "items": {"type": "string"}},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_decision,
        )

    async def record_decision(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = arguments.get("title")
        rationale = arguments.get("rationale")
        if not title or not isinstance(title, str):
            raise ValueError("twin.record_decision: 'title' is required (non-empty string)")
        if not rationale or not isinstance(rationale, str):
            raise ValueError("twin.record_decision: 'rationale' is required (non-empty string)")
        alternatives = arguments.get("alternatives")
        if alternatives is not None and not isinstance(alternatives, list):
            raise ValueError("twin.record_decision: 'alternatives' must be an array")
        parent_refs = arguments.get("parent_refs")
        if parent_refs is not None and not isinstance(parent_refs, list):
            raise ValueError("twin.record_decision: 'parent_refs' must be an array")
        relation = arguments.get("relation")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        supersedes = arguments.get("supersedes")
        kwargs: dict[str, Any] = {
            "title": title,
            "rationale": rationale,
            "alternatives": alternatives,
            "parent_refs": parent_refs,
            "project_id": project_id if isinstance(project_id, str) else None,
            "session_id": session_id if isinstance(session_id, str) else None,
            "supersedes": supersedes if isinstance(supersedes, str) else None,
        }
        if isinstance(relation, str) and relation:
            kwargs["relation"] = relation
        return await self._decision_recorder(**kwargs)

    # ------------------------------------------------------------------
    # twin.record_component_selection (MET-436 follow-up)
    # ------------------------------------------------------------------

    def _register_record_component_selection(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_component_selection",
                adapter_id="twin",
                name="Record Component Selection",
                description=(
                    "Persist one chosen component.search_parametric/"
                    "search_intent result as a real BOMItem work product "
                    "linked to a project — the reviewable, versioned "
                    "artifact a search result otherwise never becomes. Use "
                    "after picking an MPN from search results, not for "
                    "searching itself."
                ),
                capability="twin_component_selection",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mpn": {"type": "string", "minLength": 1, "description": "Part number."},
                        "manufacturer": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Manufacturer name.",
                        },
                        "category": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Catalog category, e.g. 'buck_converter'.",
                        },
                        "purchase_unit": {
                            "type": "string",
                            "enum": ["discrete_part", "cots_assembly"],
                            "description": (
                                "'discrete_part' (design in) or 'cots_assembly' "
                                "(complete, ready-to-buy)."
                            ),
                        },
                        "role": {
                            "type": ["string", "null"],
                            "description": "Subsystem role, e.g. 'mcu' (from search_intent).",
                        },
                        "quantity": {"type": "integer", "minimum": 1, "default": 1},
                        "unit_cost_usd": {"type": ["number", "null"]},
                        "specs": {
                            "type": "object",
                            "description": "Extra spec fields to store, e.g. from the search row.",
                        },
                        "source": {
                            "type": "string",
                            "description": "How found: 'parametric' | 'fuzzy_fallback' | 'manual'.",
                        },
                        "distributor": {"type": ["string", "null"]},
                        "datasheet_url": {"type": ["string", "null"]},
                        "image_url": {
                            "type": ["string", "null"],
                            "description": "Product photo/rendering URL, when known.",
                        },
                        "footprint": {
                            "type": ["string", "null"],
                            "description": (
                                "PCB land-pattern/footprint id (e.g. an IPC-7351 name) -- "
                                "distinct from the coarser package name, which goes in 'specs'."
                            ),
                        },
                        "cad_model_url": {
                            "type": ["string", "null"],
                            "description": "3D/CAD model (e.g. STEP) URL, when known.",
                        },
                        "purchase_url": {
                            "type": ["string", "null"],
                            "description": (
                                "Public product/detail page to actually buy the part -- e.g. a "
                                "distributor search result's 'product_url'."
                            ),
                        },
                        "price_currency": {
                            "type": "string",
                            "default": "USD",
                            "description": "ISO 4217 currency code for unit_cost_usd.",
                        },
                        "priced_distributor": {
                            "type": ["string", "null"],
                            "description": (
                                "Which distributor unit_cost_usd came from, if different from "
                                "'distributor' (e.g. a resolve_offers comparison)."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                    },
                    "required": ["mpn", "manufacturer", "category", "purchase_unit"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "mpn": {"type": "string"},
                        "category": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "bom_work_product_id": {
                            "type": ["string", "null"],
                            "description": (
                                "The project's BOM work product this selection was linked to "
                                "(CONTAINS edge) -- null when unscoped (no project_id given) or "
                                "if the link failed (best-effort, the BOMItem write still "
                                "succeeds)."
                            ),
                        },
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_component_selection,
        )

    async def record_component_selection(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mpn = arguments.get("mpn")
        manufacturer = arguments.get("manufacturer")
        category = arguments.get("category")
        purchase_unit = arguments.get("purchase_unit")
        if not mpn or not isinstance(mpn, str):
            raise ValueError(
                "twin.record_component_selection: 'mpn' is required (non-empty string)"
            )
        if not manufacturer or not isinstance(manufacturer, str):
            raise ValueError(
                "twin.record_component_selection: 'manufacturer' is required (non-empty string)"
            )
        if not category or not isinstance(category, str):
            raise ValueError(
                "twin.record_component_selection: 'category' is required (non-empty string)"
            )
        if purchase_unit not in ("discrete_part", "cots_assembly"):
            raise ValueError(
                "twin.record_component_selection: 'purchase_unit' must be "
                "'discrete_part' or 'cots_assembly'"
            )

        role = arguments.get("role")
        quantity = arguments.get("quantity", 1)
        try:
            quantity_int = int(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "twin.record_component_selection: 'quantity' must be an integer"
            ) from exc
        unit_cost_usd = arguments.get("unit_cost_usd")
        specs = arguments.get("specs")
        source = arguments.get("source")
        distributor = arguments.get("distributor")
        datasheet_url = arguments.get("datasheet_url")
        image_url = arguments.get("image_url")
        footprint = arguments.get("footprint")
        cad_model_url = arguments.get("cad_model_url")
        purchase_url = arguments.get("purchase_url")
        price_currency = arguments.get("price_currency", "USD")
        priced_distributor = arguments.get("priced_distributor")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")

        return await self._component_recorder(
            mpn=mpn,
            manufacturer=manufacturer,
            category=category,
            purchase_unit=purchase_unit,
            role=role if isinstance(role, str) else None,
            quantity=quantity_int,
            unit_cost_usd=unit_cost_usd if isinstance(unit_cost_usd, (int, float)) else None,
            specs=specs if isinstance(specs, dict) else None,
            source=source if isinstance(source, str) else None,
            distributor=distributor if isinstance(distributor, str) else None,
            datasheet_url=datasheet_url if isinstance(datasheet_url, str) else None,
            image_url=image_url if isinstance(image_url, str) else None,
            footprint=footprint if isinstance(footprint, str) else None,
            cad_model_url=cad_model_url if isinstance(cad_model_url, str) else None,
            purchase_url=purchase_url if isinstance(purchase_url, str) else None,
            price_currency=price_currency if isinstance(price_currency, str) else "USD",
            priced_distributor=(
                priced_distributor if isinstance(priced_distributor, str) else None
            ),
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.record_constraint_set (MET-582)
    # ------------------------------------------------------------------

    def _register_record_constraint_set(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_constraint_set",
                adapter_id="twin",
                name="Record Constraint Set",
                description=(
                    "Persist a project's structured requirements as EVALUABLE "
                    "constraints: each entry becomes a Constraint node the "
                    "constraint engine checks at design-flow gates, plus one "
                    "constraint_set work product summarising the set. Use "
                    "during requirements capture so quantified limits (mass, "
                    "power, cost, safety factor, ...) become machine-checked "
                    "gate criteria instead of prose."
                ),
                capability="twin_constraint_set",
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Constraint-set title (e.g. 'Gimbal v1 requirements').",
                        },
                        "constraints": {
                            "type": "array",
                            "minItems": 1,
                            "description": "Structured constraints to record.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Short unique name (e.g. mass_budget).",
                                    },
                                    "expression": {
                                        "type": "string",
                                        "description": (
                                            "Python expression over `ctx` the engine "
                                            'evaluates (e.g. "all(float(wp.metadata.get('
                                            "'mass_g', 0)) <= 60 for wp in "
                                            "ctx.work_products(type='cad_model'))\"). "
                                            "Must compile; validated at record time."
                                        ),
                                    },
                                    "severity": {
                                        "type": "string",
                                        "enum": ["error", "warning", "info"],
                                        "description": "error violations fail enforcing gates.",
                                    },
                                    "message": {
                                        "type": "string",
                                        "description": "Human explanation of the limit.",
                                    },
                                    "domain": {
                                        "type": "string",
                                        "description": "Discipline (default: systems).",
                                    },
                                    "parent_refs": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "FORGE-46: the high-level requirement(s)/"
                                            "constraint(s) this one implements/decomposes -- "
                                            "each an exact Constraint name, EngineeringEntity "
                                            "title, or node UUID, scoped to project_id. "
                                            "Linked via an IMPLEMENTS edge. An unresolvable "
                                            "ref fails the whole call -- record the parent "
                                            "first."
                                        ),
                                    },
                                },
                                "required": ["name", "expression"],
                            },
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                    },
                    "required": ["title", "constraints"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "constraint_ids": {"type": "array", "items": {"type": "string"}},
                        "constraint_parents": {
                            "type": "object",
                            "description": (
                                "Constraint id -> resolved parent id(s), for entries that "
                                "carried parent_refs (FORGE-46)."
                            ),
                        },
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_constraint_set,
        )

    async def record_constraint_set(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = arguments.get("title")
        constraints = arguments.get("constraints")
        if not title or not isinstance(title, str):
            raise ValueError("twin.record_constraint_set: 'title' is required (non-empty string)")
        if not isinstance(constraints, list) or not constraints:
            raise ValueError(
                "twin.record_constraint_set: 'constraints' is required (non-empty array)"
            )
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        return await self._constraint_recorder(
            title=title,
            constraints=constraints,
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.record_engineering_entity (FORGE-45/47, epic FORGE-35)
    # ------------------------------------------------------------------

    def _register_record_engineering_entity(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_engineering_entity",
                adapter_id="twin",
                name="Record Engineering Entity",
                description=(
                    "Persist one Engineering Intent & Requirements Harness entity: "
                    "an intent, stakeholder_need, objective, assumption, question, "
                    "risk, verification_case, or evidence. Use to capture WHY a "
                    "product/requirement exists before recording the quantified "
                    "requirements themselves (twin.record_constraint_set). Link it "
                    "to the entity it derives_from/satisfies/motivates/etc. via "
                    "parent_refs so the chain from stated intent to a specific "
                    "requirement stays traceable."
                ),
                capability="twin_engineering_entity",
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_type": {
                            "type": "string",
                            "enum": [
                                "intent",
                                "stakeholder_need",
                                "objective",
                                "assumption",
                                "question",
                                "risk",
                                "verification_case",
                                "evidence",
                            ],
                        },
                        "statement": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The entity's content in plain English.",
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "Short, stable name other entries can reference by "
                                "parent_refs. Give one to any entity you expect to be "
                                "a future parent."
                            ),
                        },
                        "extra": {
                            "type": "object",
                            "description": (
                                "Type-specific fields (e.g. objective's metric/direction/"
                                "target, risk's probability/severity/mitigation, "
                                "evidence's evidence_type/result) -- stored in metadata."
                            ),
                        },
                        "parent_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "The entity/entities this one derives_from/satisfies/"
                                "motivates/etc. -- each an exact Constraint name, "
                                "EngineeringEntity title, or node UUID, scoped to "
                                "project_id. An unresolvable ref fails the whole call."
                            ),
                        },
                        "relation": {
                            "type": "string",
                            "default": "derives_from",
                            "description": (
                                "The EdgeType linking this entity to each parent_ref "
                                "(e.g. 'motivates' for intent->need, 'satisfies' for "
                                "need->requirement). Default: derives_from."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                    },
                    "required": ["entity_type", "statement"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "parent_ids": {"type": "array", "items": {"type": "string"}},
                        "project_linked": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=10),
            ),
            handler=self.record_engineering_entity,
        )

    async def record_engineering_entity(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entity_type = arguments.get("entity_type")
        statement = arguments.get("statement")
        if not entity_type or not isinstance(entity_type, str):
            raise ValueError(
                "twin.record_engineering_entity: 'entity_type' is required (non-empty string)"
            )
        if not statement or not isinstance(statement, str):
            raise ValueError(
                "twin.record_engineering_entity: 'statement' is required (non-empty string)"
            )
        title = arguments.get("title")
        extra = arguments.get("extra")
        parent_refs = arguments.get("parent_refs")
        relation = arguments.get("relation")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        return await self._engineering_entity_recorder(
            entity_type=entity_type,
            statement=statement,
            title=title if isinstance(title, str) else None,
            extra=extra if isinstance(extra, dict) else None,
            parent_refs=parent_refs if isinstance(parent_refs, list) else None,
            relation=relation if isinstance(relation, str) else "derives_from",
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.record_document (MET-588)
    # ------------------------------------------------------------------

    _DOCUMENT_TYPES = ("prd", "documentation")

    def _register_record_document(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_document",
                adapter_id="twin",
                name="Record Document",
                description=(
                    "Persist a text/markdown artifact (requirements, notes, a "
                    "spec) as a first-class PRD or DOCUMENTATION work product: "
                    "stores it in MinIO and links it to a project so it shows on "
                    "the project's work-product list. Writes immediately — no "
                    "approval gate, same as twin.record_decision. Use this "
                    "instead of twin.propose_change for saving a document; "
                    "propose_change's apply-on-approve step only implements a "
                    "'record_decision' action, so any other diff (including a "
                    "document) silently does nothing even after a human approves it."
                ),
                capability="twin_decision",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Document title / work-product name.",
                        },
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The document body, as markdown.",
                        },
                        "document_type": {
                            "type": "string",
                            "enum": list(self._DOCUMENT_TYPES),
                            "description": (
                                "'prd' for a requirements/product doc, "
                                "'documentation' for general notes/specs. "
                                "Defaults to 'documentation'."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                    },
                    "required": ["name", "content"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "size_bytes": {"type": "integer"},
                        "project_linked": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_document,
        )

    async def record_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        content = arguments.get("content")
        if not name or not isinstance(name, str):
            raise ValueError("twin.record_document: 'name' is required (non-empty string)")
        if not content or not isinstance(content, str):
            raise ValueError("twin.record_document: 'content' is required (non-empty string)")
        document_type = arguments.get("document_type") or "documentation"
        if document_type not in self._DOCUMENT_TYPES:
            raise ValueError(
                f"twin.record_document: 'document_type' must be one of {self._DOCUMENT_TYPES}"
            )
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        return await self._document_recorder(
            content=content,
            name=name,
            wp_type=document_type,
            domain="requirements" if document_type == "prd" else "documentation",
            fmt="md",
            link_type=document_type,
            source_tool="twin.record_document",
            session_id=session_id if isinstance(session_id, str) else None,
            project_id=project_id if isinstance(project_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.propose_change (MET-548) — gated HITL modification
    # ------------------------------------------------------------------

    def _register_propose_change(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.propose_change",
                adapter_id="twin",
                name="Propose Design Change",
                description=(
                    "Propose a REVIEWABLE change to a work product. Does NOT modify "
                    "the twin directly — it files a pending proposal a human "
                    "approves or rejects (human-in-the-loop). Use this for any "
                    "consequential change (geometry, parameters, decisions) the "
                    "user asked for, instead of committing directly."
                ),
                capability="twin_propose",
                input_schema={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Human-readable summary of the proposed change.",
                        },
                        "diff": {
                            "type": "object",
                            "description": (
                                "Structured change. Include an 'action' "
                                "(e.g. 'record_decision' | 'regenerate_geometry' | "
                                "'update_properties') plus its parameters. For "
                                "'regenerate_geometry': 'script_source' (required — the "
                                "full edited script text), 'name', 'parameters' (optional "
                                "structured values, purely informational), and 'cad_tool' "
                                "('cadquery', the default, or 'freecad' — state which "
                                "dialect script_source is written in; they are not "
                                "interchangeable and this is never auto-detected)."
                            ),
                        },
                        "work_products_affected": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Twin node ids this change touches.",
                        },
                        "agent_code": {"type": "string", "description": "Proposing agent code."},
                        "project_id": {"type": "string"},
                        "session_id": {"type": "string"},
                    },
                    "required": ["description", "diff"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "change_id": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=10),
            ),
            handler=self.propose_change,
        )

    async def propose_change(self, arguments: dict[str, Any]) -> dict[str, Any]:
        description = arguments.get("description")
        diff = arguments.get("diff")
        if not description or not isinstance(description, str):
            raise ValueError("twin.propose_change: 'description' is required (non-empty string)")
        if not isinstance(diff, dict):
            raise ValueError("twin.propose_change: 'diff' is required (object)")
        wps = arguments.get("work_products_affected")
        work_products = [str(w) for w in wps] if isinstance(wps, list) else []
        agent_code = arguments.get("agent_code")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        return await self._proposal_recorder(
            agent_code=agent_code if isinstance(agent_code, str) else "assistant",
            description=description,
            diff=diff,
            work_products=work_products,
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.commit_geometry (MET-529)
    # ------------------------------------------------------------------

    def _register_commit_geometry(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_geometry",
                adapter_id="twin",
                name="Commit Authored Geometry",
                description=(
                    "Persist CAD geometry authored over MCP as a CAD_MODEL work "
                    "product: stores the STEP blob in MinIO, creates a twin node, "
                    "and links it to a project so it renders in the 3D viewer. "
                    "PREFER commit-by-reference: after freecad.export_model, call "
                    "this with the SAME session_id AND obj_id (plus name) and the "
                    "server fills the STEP itself — you do NOT need to copy the "
                    "large base64 string. BOTH session_id and obj_id are required "
                    "together on every call, including retries — obj_id alone is "
                    "NOT unique (it's a per-session counter, not a global id), so "
                    "omitting session_id will not match your prior export even "
                    "though obj_id is correct. (Passing step_base64 directly also "
                    "works and needs neither id — but when it names an export "
                    "the server already holds, the server's copy is used, "
                    "because a copied 30,000-character blob can only be equal "
                    "or damaged.)"
                ),
                capability="twin_geometry",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Display name for the work product.",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "freecad session id (with obj_id: commit by reference).",
                        },
                        "obj_id": {
                            "type": "string",
                            "description": "exported obj_id (with session_id: by reference).",
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "step_base64": {
                            "type": "string",
                            "description": (
                                "Base64 STEP. Omit when passing session_id + obj_id: "
                                "the server substitutes its own copy of that export "
                                "anyway (MET-684)."
                            ),
                        },
                        "domain": {"type": "string", "description": "Discipline (def mech)."},
                        "format": {"type": "string", "description": "Format (def step)."},
                        "script_source": {
                            "type": "string",
                            "description": (
                                "The CadQuery/FreeCAD generation script text that authored "
                                "this geometry, if any. When given, it is committed to the "
                                "project's real git repo as the source of truth (real "
                                "diffs/merges), linked as provenance to this STEP node."
                            ),
                        },
                        "parameters": {
                            "type": "object",
                            "description": (
                                "Structured values that drove generation (e.g. pad_length, "
                                "hole_diameter) — stored on the node as queryable metadata."
                            ),
                        },
                        "properties": {
                            "type": "object",
                            "description": (
                                "Derived geometric measurements (volume_mm3, bounding_box, "
                                "mass properties, etc.) — stored on the node as queryable "
                                "metadata alongside 'parameters'."
                            ),
                        },
                        "source_tool": {
                            "type": "string",
                            "description": (
                                "Which authoring tool produced this geometry, e.g. "
                                "'cadquery.execute_script' or 'freecad.export_model'. "
                                "Recorded as the work product's provenance "
                                "(authored_by/created_by). Defaults to "
                                "'freecad.export_model', so pass it explicitly when the "
                                "geometry came from CadQuery — otherwise the node claims "
                                "a tool that never touched it (MET-693)."
                            ),
                        },
                    },
                    "required": ["name"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "model_url": {"type": "string"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=512, max_cpu_seconds=30),
            ),
            handler=self.commit_geometry,
        )

    async def commit_geometry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        step_base64 = arguments.get("step_base64")
        name = arguments.get("name")
        if not step_base64 or not isinstance(step_base64, str):
            # MET-642 S4 finding: reproduced live TWICE with the identical
            # mechanism -- the model retried commit_geometry with obj_id but
            # WITHOUT session_id (confirmed via the new geometry_stash logging
            # below: "session_id": null). The stash keys on (session_id,
            # obj_id) together -- not obj_id alone -- because obj_id is a
            # per-session sequential counter (f"{kind}_{n}", see
            # FreecadSessionStore.register_object), not a globally-unique id;
            # dropping session_id from the lookup would risk a cross-session
            # collision, so the fix is a sharper error, not a looser stash.
            given_obj_id = arguments.get("obj_id")
            given_session_id = arguments.get("session_id")
            logger.warning(
                "commit_geometry_missing_step_base64",
                session_id=given_session_id,
                obj_id=given_obj_id,
                name=name,
            )
            if given_obj_id and not given_session_id:
                raise ValueError(
                    "twin.commit_geometry: you passed obj_id but no session_id -- "
                    "commit-by-reference requires BOTH, exactly as given to the "
                    "freecad.export_model call that produced this obj_id (obj_id "
                    "alone is not unique across sessions). Re-call with the same "
                    "session_id you used for export_model, or pass step_base64 directly."
                )
            raise ValueError(
                "twin.commit_geometry: no geometry to commit — call freecad.export_model "
                "first, then commit with the same session_id + obj_id (or pass step_base64)."
            )
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_geometry: 'name' is required (non-empty string)")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        domain = arguments.get("domain")
        fmt = arguments.get("format")
        script_source = arguments.get("script_source")
        parameters = arguments.get("parameters")
        properties = arguments.get("properties")
        script_source = script_source if isinstance(script_source, str) and script_source else None
        # MET-693: the recorder's `source_tool` defaults to
        # "freecad.export_model", and this handler never passed one -- so every
        # commit was stamped as FreeCAD-authored, including CadQuery geometry.
        # The provenance of a work product has to be accurate: it is what a
        # reviewer reads to know how the artifact was produced, and script-as-
        # SSOT diffing (MET-630) depends on knowing which authoring tool the
        # stored script belongs to.
        source_tool = arguments.get("source_tool")
        source_tool = source_tool if isinstance(source_tool, str) and source_tool else None
        return await self._geometry_recorder(
            step_base64=step_base64,
            name=name,
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
            domain=domain if isinstance(domain, str) and domain else "mechanical",
            fmt=fmt if isinstance(fmt, str) and fmt else "step",
            script_source=script_source,
            parameters=parameters if isinstance(parameters, dict) else None,
            properties=properties if isinstance(properties, dict) else None,
            **({"source_tool": source_tool} if source_tool else {}),
        )

    # ------------------------------------------------------------------
    # twin.commit_design_sketch (follow-up to MET-740/747)
    # ------------------------------------------------------------------

    def _register_commit_design_sketch(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_design_sketch",
                adapter_id="twin",
                name="Commit Design Sketch",
                description=(
                    "Persist a self-contained HTML reference sketch (proportions, "
                    "topology, range-of-motion preview) as a DESIGN_SKETCH work "
                    "product -- the human-approval gate MetaForge uses before "
                    "committing to real CAD/build work on anything non-trivial or "
                    "any revision of an already-built design. Call this BEFORE "
                    "authoring real CAD geometry when a sketch is warranted (see "
                    "the mechanical.decide_sketch_needed skill for when it is); "
                    "the returned node starts unapproved -- do not proceed to "
                    "real CAD/build work until a human approves it (dashboard "
                    "'Approve' action, or GET the node to check "
                    "metadata.approved)."
                ),
                capability="twin_design_sketch",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Display name for the work product.",
                        },
                        "html_content": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "The complete, self-contained sketch document: inline "
                                "CSS/JS, no external assets, no <!DOCTYPE>/<html>/<head>/"
                                "<body> wrapper needed -- just the content that would go "
                                "inside <body>. Rendered full-screen in a sandboxed "
                                "iframe by the dashboard."
                            ),
                        },
                        "description_text": {
                            "type": "string",
                            "description": "Short rationale for what this sketch is checking.",
                        },
                        "source_node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Existing work-product node ids this sketch reviews -- "
                                "give this when the sketch is about a REVISION to an "
                                "already-built design. Omit for a brand-new design "
                                "(nothing built yet to reference)."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "domain": {"type": "string", "description": "Discipline (def mech)."},
                        "source_tool": {
                            "type": "string",
                            "description": "Which tool/skill produced this sketch (provenance).",
                        },
                    },
                    "required": ["name", "html_content"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.commit_design_sketch,
        )

    async def commit_design_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_design_sketch: 'name' is required (non-empty string)")
        html_content = arguments.get("html_content")
        if not html_content or not isinstance(html_content, str):
            raise ValueError(
                "twin.commit_design_sketch: 'html_content' is required (non-empty string)"
            )
        source_node_ids = arguments.get("source_node_ids")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        source_tool = arguments.get("source_tool")
        return await self._design_sketch_recorder(
            name=name,
            html_content=html_content,
            description_text=arguments.get("description_text") or "",
            source_node_ids=source_node_ids if isinstance(source_node_ids, list) else None,
            project_id=project_id if isinstance(project_id, str) else None,
            domain=domain if isinstance(domain, str) and domain else "mechanical",
            **(
                {"source_tool": source_tool} if isinstance(source_tool, str) and source_tool else {}
            ),
        )

    # ------------------------------------------------------------------
    # Structured-document work products (MET-747 lifecycle-mapping follow-up)
    # ------------------------------------------------------------------

    def _register_commit_hazard_analysis(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_hazard_analysis",
                adapter_id="twin",
                name="Commit Hazard Analysis",
                description=(
                    "Persist a hazard/risk log (hazard, cause, effect, "
                    "severity x likelihood -> risk score, mitigation) as a "
                    "HAZARD_ANALYSIS work product. Called by the "
                    "compliance.analyze_hazards skill after it computes risk "
                    "scores -- do not call this directly with unscored data."
                ),
                capability="twin_hazard_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "system_name": {"type": "string"},
                        "hazards": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "hazard": {"type": "string"},
                                    "cause": {"type": "string"},
                                    "effect": {"type": "string"},
                                    "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                                    "likelihood": {"type": "integer", "minimum": 1, "maximum": 5},
                                    "mitigation": {"type": "string"},
                                },
                                "required": ["hazard", "cause", "effect", "severity", "likelihood"],
                            },
                        },
                        "project_id": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                    "required": ["name", "hazards"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "hazard_count": {"type": "integer"},
                        "highest_risk_score": {"type": "integer"},
                        "unmitigated_count": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
                # MET-747 follow-up: invoked by compliance.analyze_hazards's
                # handler via context.mcp.invoke(...), not meant to be picked
                # ad hoc off a raw chat tool list -- keeps it out of the
                # OpenAI-family chat tools array (128-entry hard cap).
                chat_visible=False,
            ),
            handler=self.commit_hazard_analysis,
        )

    async def commit_hazard_analysis(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_hazard_analysis: 'name' is required")
        hazards = arguments.get("hazards")
        if not isinstance(hazards, list) or not hazards:
            raise ValueError("twin.commit_hazard_analysis: 'hazards' must be a non-empty array")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        return await self._hazard_analysis_recorder(
            name=name,
            system_name=arguments.get("system_name") or name,
            hazards=hazards,
            project_id=project_id if isinstance(project_id, str) else None,
            **({"domain": domain} if isinstance(domain, str) and domain else {}),
        )

    def _register_commit_system_architecture(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_system_architecture",
                adapter_id="twin",
                name="Commit System Architecture",
                description=(
                    "Persist a cross-discipline component/interface map (block "
                    "diagram + interface table) as a SYSTEM_ARCHITECTURE work "
                    "product -- captures what talks to what before detailed "
                    "design starts. Called by shared.define_system_architecture."
                ),
                capability="twin_system_architecture",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "system_name": {"type": "string"},
                        "components": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "discipline": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["name"],
                            },
                        },
                        "interfaces": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "from": {"type": "string"},
                                    "to": {"type": "string"},
                                    "interface_type": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["from", "to"],
                            },
                        },
                        "project_id": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                    "required": ["name", "components"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "component_count": {"type": "integer"},
                        "interface_count": {"type": "integer"},
                        "dangling_interfaces": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
                # MET-747 follow-up: invoked by shared.define_system_architecture's
                # handler via context.mcp.invoke(...) -- see chat_visible's
                # docstring on ToolManifest.
                chat_visible=False,
            ),
            handler=self.commit_system_architecture,
        )

    async def commit_system_architecture(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_system_architecture: 'name' is required")
        components = arguments.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError(
                "twin.commit_system_architecture: 'components' must be a non-empty array"
            )
        interfaces = arguments.get("interfaces")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        return await self._system_architecture_recorder(
            name=name,
            system_name=arguments.get("system_name") or name,
            components=components,
            interfaces=interfaces if isinstance(interfaces, list) else None,
            project_id=project_id if isinstance(project_id, str) else None,
            **({"domain": domain} if isinstance(domain, str) and domain else {}),
        )

    def _register_commit_technical_drawing(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_technical_drawing",
                adapter_id="twin",
                name="Commit Technical Drawing",
                description=(
                    "Persist a structured drawing-package spec (dimensions, "
                    "GD&T callouts, surface finishes, inspection requirements) "
                    "for a CAD part as a TECHNICAL_DRAWING work product. NOT a "
                    "rendered 2D vector drawing -- this is the callout DATA a "
                    "real drawing would encode. Called by "
                    "mechanical.generate_technical_drawing."
                ),
                capability="twin_technical_drawing",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "part_name": {"type": "string"},
                        "dimensions": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "feature": {"type": "string"},
                                    "nominal_mm": {"type": "number"},
                                    "tolerance_plus_mm": {"type": "number"},
                                    "tolerance_minus_mm": {"type": "number"},
                                },
                                "required": ["feature", "nominal_mm"],
                            },
                        },
                        "gdt_callouts": {"type": "array"},
                        "surface_finishes": {"type": "array"},
                        "inspection_requirements": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "source_node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Source CAD_MODEL node id(s) this drawing documents.",
                        },
                        "project_id": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                    "required": ["name", "dimensions"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "dimension_count": {"type": "integer"},
                        "gdt_callout_count": {"type": "integer"},
                        "surface_finish_count": {"type": "integer"},
                        "inspection_requirement_count": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
                # MET-747 follow-up: invoked by mechanical.generate_technical_drawing's
                # handler via context.mcp.invoke(...) -- see chat_visible's
                # docstring on ToolManifest.
                chat_visible=False,
            ),
            handler=self.commit_technical_drawing,
        )

    async def commit_technical_drawing(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_technical_drawing: 'name' is required")
        dimensions = arguments.get("dimensions")
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError(
                "twin.commit_technical_drawing: 'dimensions' must be a non-empty array"
            )
        source_node_ids = arguments.get("source_node_ids")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        gdt_callouts = arguments.get("gdt_callouts")
        surface_finishes = arguments.get("surface_finishes")
        inspection_requirements = arguments.get("inspection_requirements")
        return await self._technical_drawing_recorder(
            name=name,
            part_name=arguments.get("part_name") or name,
            dimensions=dimensions,
            gdt_callouts=gdt_callouts if isinstance(gdt_callouts, list) else None,
            surface_finishes=surface_finishes if isinstance(surface_finishes, list) else None,
            inspection_requirements=(
                inspection_requirements if isinstance(inspection_requirements, list) else None
            ),
            source_node_ids=source_node_ids if isinstance(source_node_ids, list) else None,
            project_id=project_id if isinstance(project_id, str) else None,
            **({"domain": domain} if isinstance(domain, str) and domain else {}),
        )

    def _register_commit_compliance_checklist(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_compliance_checklist",
                adapter_id="twin",
                name="Commit Compliance Checklist",
                description=(
                    "Persist a generated regulatory checklist (regime, "
                    "requirement, evidence rows) as a COMPLIANCE_CHECKLIST "
                    "work product. Called by "
                    "compliance.record_compliance_checklist after it computes "
                    "the checklist -- do not call this with raw, unvalidated "
                    "checklist data."
                ),
                capability="twin_compliance_checklist",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "target_markets": {"type": "array", "items": {"type": "string"}},
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "regime": {"type": "string"},
                                    "category": {"type": "string"},
                                    "requirement": {"type": "string"},
                                    "standard": {"type": "string"},
                                    "evidence_type": {"type": "string"},
                                    "evidence_status": {"type": "string"},
                                },
                                "required": ["regime", "requirement"],
                            },
                        },
                        "coverage_percent": {"type": "number"},
                        "project_id": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                    "required": ["name", "items"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "total_items": {"type": "integer"},
                        "coverage_percent": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
                # MET-747 follow-up: invoked by compliance.record_compliance_checklist's
                # handler via context.mcp.invoke(...) -- see chat_visible's
                # docstring on ToolManifest.
                chat_visible=False,
            ),
            handler=self.commit_compliance_checklist,
        )

    async def commit_compliance_checklist(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_compliance_checklist: 'name' is required")
        items = arguments.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("twin.commit_compliance_checklist: 'items' must be a non-empty array")
        target_markets = arguments.get("target_markets")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        return await self._compliance_checklist_recorder(
            name=name,
            target_markets=target_markets if isinstance(target_markets, list) else [],
            items=items,
            coverage_percent=float(arguments.get("coverage_percent") or 0.0),
            project_id=project_id if isinstance(project_id, str) else None,
            **({"domain": domain} if isinstance(domain, str) and domain else {}),
        )

    def _register_commit_procurement_record(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.commit_procurement_record",
                adapter_id="twin",
                name="Commit Procurement Record",
                description=(
                    "Persist a purchase-order-style procurement record (line "
                    "items, quantities, unit costs, distributor, lead time) as "
                    "a PROCUREMENT_RECORD work product, usually linked back to "
                    "the BOM it was sourced from. Called by "
                    "supply_chain.create_procurement_record."
                ),
                capability="twin_procurement_record",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "line_items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "part_number": {"type": "string"},
                                    "description": {"type": "string"},
                                    "quantity": {"type": "number"},
                                    "unit_cost": {"type": "number"},
                                    "currency": {"type": "string"},
                                    "distributor": {"type": "string"},
                                    "lead_time_days": {"type": "integer"},
                                },
                                "required": ["part_number", "quantity", "unit_cost"],
                            },
                        },
                        "notes": {"type": "string"},
                        "source_node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Source BOM node id(s) this record was sourced from.",
                        },
                        "project_id": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                    "required": ["name", "line_items"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "minio_object_key": {"type": ["string", "null"]},
                        "content_hash": {"type": "string"},
                        "project_linked": {"type": "boolean"},
                        "line_item_count": {"type": "integer"},
                        "total_cost": {"type": "number"},
                        "currency": {"type": "string"},
                        "max_lead_time_days": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
                # MET-747 follow-up: invoked by supply_chain.create_procurement_record's
                # handler via context.mcp.invoke(...) -- see chat_visible's
                # docstring on ToolManifest.
                chat_visible=False,
            ),
            handler=self.commit_procurement_record,
        )

    async def commit_procurement_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_procurement_record: 'name' is required")
        line_items = arguments.get("line_items")
        if not isinstance(line_items, list) or not line_items:
            raise ValueError(
                "twin.commit_procurement_record: 'line_items' must be a non-empty array"
            )
        source_node_ids = arguments.get("source_node_ids")
        project_id = arguments.get("project_id")
        domain = arguments.get("domain")
        return await self._procurement_record_recorder(
            name=name,
            line_items=line_items,
            notes=arguments.get("notes") or "",
            source_node_ids=source_node_ids if isinstance(source_node_ids, list) else None,
            project_id=project_id if isinstance(project_id, str) else None,
            **({"domain": domain} if isinstance(domain, str) and domain else {}),
        )

    def _register_stage_work_product_file(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.stage_work_product_file",
                adapter_id="twin",
                name="Stage Work Product File",
                description=(
                    "Materialize a committed work product's stored file onto disk and "
                    "return a local file_path that freecad.*/cadquery.*/calculix.* tools "
                    "can load directly (e.g. freecad.get_properties, freecad.open_session "
                    "then import it). Call this whenever you need to inspect a work "
                    "product's actual content — geometry, mesh, whatever — and its "
                    "original authoring session_id is stale, unknown, or was never yours: "
                    "do NOT give up after freecad.describe_session fails or after seeing "
                    "an empty file_path on the twin node. This tool works for ANY "
                    "committed work product, independent of any live session."
                ),
                capability="twin_read",
                input_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "Work product node UUID (from twin.get_node etc.).",
                        },
                    },
                    "required": ["node_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "file_path": {"type": "string"},
                        "filename": {"type": "string"},
                        "size_bytes": {"type": "integer"},
                        "content_hash": {"type": "string"},
                        "format": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=512, max_cpu_seconds=30),
            ),
            handler=self.stage_work_product_file,
        )

    async def stage_work_product_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        node_id = arguments.get("node_id")
        if not node_id or not isinstance(node_id, str):
            raise ValueError(
                "twin.stage_work_product_file: 'node_id' is required (non-empty string)"
            )
        return await self._blob_stager(node_id)

    # ------------------------------------------------------------------
    # twin.record_evidence (FORGE-64, epic FORGE-35)
    # ------------------------------------------------------------------

    def _register_record_evidence(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_evidence",
                adapter_id="twin",
                name="Record Evidence",
                description=(
                    "Persist a tool-generated evidence entity: the REAL structured "
                    "result of a calculix/simulation/CAD/test tool call (never a "
                    "restated or summarized assertion), with producer, inputs, a "
                    "content hash, and optional links to the requirement(s) it "
                    "supports/contradicts, and the revision(s) of other entities it "
                    "is only valid against. Call this immediately after the tool "
                    "call it evidences, passing that tool's own output as 'result'."
                ),
                capability="twin_evidence",
                input_schema={
                    "type": "object",
                    "properties": {
                        "evidence_type": {
                            "type": "string",
                            "enum": [
                                "calculation",
                                "simulation",
                                "test",
                                "inspection",
                                "demonstration",
                                "datasheet",
                                "external_reference",
                            ],
                        },
                        "producer": {
                            "type": "object",
                            "description": "e.g. {'tool': 'calculix.run_fea', 'version': '2.20'}.",
                            "properties": {
                                "tool": {"type": "string"},
                                "version": {"type": "string"},
                            },
                            "required": ["tool"],
                        },
                        "inputs": {
                            "type": "object",
                            "description": "The parameters/state the tool was run against.",
                        },
                        "result": {
                            "type": "object",
                            "description": "The tool's own real structured output.",
                        },
                        "statement": {
                            "type": "string",
                            "description": "Optional human-readable summary.",
                        },
                        "supports": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Requirement/objective refs this evidence supports.",
                        },
                        "contradicts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Requirement/objective refs this evidence contradicts.",
                        },
                        "valid_against": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ref": {"type": "string"},
                                    "entity_kind": {
                                        "type": "string",
                                        "enum": ["constraint", "engineering_entity"],
                                    },
                                    "revision": {"type": "integer"},
                                },
                                "required": ["ref", "entity_kind"],
                            },
                            "description": (
                                "Revision-pinned dependencies (e.g. the requirement "
                                "whose current revision this evidence was computed "
                                "against) -- omit 'revision' to pin the current one."
                            ),
                        },
                        "supersedes": {
                            "type": "string",
                            "description": (
                                "FORGE-65 revalidation: node id of the stale evidence this "
                                "fresh run replaces -- flips that evidence to SUPERSEDED."
                            ),
                        },
                        "project_id": {"type": "string", "description": "Project UUID to link."},
                        "session_id": {"type": "string", "description": "Originating session id."},
                    },
                    "required": ["evidence_type", "producer", "inputs", "result"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "result_hash": {"type": "string"},
                        "execution_timestamp": {"type": "string"},
                        "supports": {"type": "array", "items": {"type": "string"}},
                        "contradicts": {"type": "array", "items": {"type": "string"}},
                        "valid_against_count": {"type": "integer"},
                        "superseded": {"type": ["string", "null"]},
                        "project_linked": {"type": "boolean"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_evidence,
        )

    async def record_evidence(self, arguments: dict[str, Any]) -> dict[str, Any]:
        evidence_type = arguments.get("evidence_type")
        producer = arguments.get("producer")
        inputs = arguments.get("inputs")
        result = arguments.get("result")
        if not evidence_type or not isinstance(evidence_type, str):
            raise ValueError("twin.record_evidence: 'evidence_type' is required (non-empty string)")
        if not isinstance(producer, dict):
            raise ValueError("twin.record_evidence: 'producer' is required (object)")
        if not isinstance(inputs, dict):
            raise ValueError("twin.record_evidence: 'inputs' is required (object)")
        if not isinstance(result, dict):
            raise ValueError("twin.record_evidence: 'result' is required (object)")
        statement = arguments.get("statement")
        supports = arguments.get("supports")
        if supports is not None and not isinstance(supports, list):
            raise ValueError("twin.record_evidence: 'supports' must be an array")
        contradicts = arguments.get("contradicts")
        if contradicts is not None and not isinstance(contradicts, list):
            raise ValueError("twin.record_evidence: 'contradicts' must be an array")
        valid_against = arguments.get("valid_against")
        if valid_against is not None and not isinstance(valid_against, list):
            raise ValueError("twin.record_evidence: 'valid_against' must be an array")
        supersedes = arguments.get("supersedes")
        project_id = arguments.get("project_id")
        session_id = arguments.get("session_id")
        return await self._evidence_recorder(
            evidence_type=evidence_type,
            producer=producer,
            inputs=inputs,
            result=result,
            statement=statement if isinstance(statement, str) else None,
            supports=supports,
            contradicts=contradicts,
            valid_against=valid_against,
            supersedes=supersedes if isinstance(supersedes, str) else None,
            project_id=project_id if isinstance(project_id, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    # ------------------------------------------------------------------
    # twin.record_claim (FORGE-65, epic FORGE-35)
    # ------------------------------------------------------------------

    def _register_record_claim(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="twin.record_claim",
                adapter_id="twin",
                name="Record Requirement Satisfaction Claim",
                description=(
                    "Persist an explicit claim that a design artefact (CAD model, "
                    "schematic, ...) satisfies a requirement, citing the evidence "
                    "that backs it. The claim's status (supported/unsupported) is "
                    "always computed live from current evidence staleness, never "
                    "cached -- a claim with no evidence, or whose evidence has all "
                    "gone stale, reports unsupported."
                ),
                capability="twin_evidence",
                input_schema={
                    "type": "object",
                    "properties": {
                        "requirement_ref": {
                            "type": "string",
                            "description": "The requirement (Constraint) by name or UUID.",
                        },
                        "artefact_ref": {
                            "type": "string",
                            "description": "The artefact (WorkProduct, e.g. a cad_model) by "
                            "name or UUID.",
                        },
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "twin.record_evidence node ids this claim cites.",
                        },
                        "claim_type": {
                            "type": "string",
                            "description": "Edge type for the claim relation (default "
                            "'satisfies').",
                        },
                        "project_id": {"type": "string", "description": "Project UUID scope."},
                    },
                    "required": ["requirement_ref", "artefact_ref"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "artefact_id": {"type": "string"},
                        "requirement_id": {"type": "string"},
                        "claim_type": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "status": {"type": "string", "enum": ["supported", "unsupported"]},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=15),
            ),
            handler=self.record_claim,
        )

    async def record_claim(self, arguments: dict[str, Any]) -> dict[str, Any]:
        requirement_ref = arguments.get("requirement_ref")
        artefact_ref = arguments.get("artefact_ref")
        if not requirement_ref or not isinstance(requirement_ref, str):
            raise ValueError("twin.record_claim: 'requirement_ref' is required (non-empty string)")
        if not artefact_ref or not isinstance(artefact_ref, str):
            raise ValueError("twin.record_claim: 'artefact_ref' is required (non-empty string)")
        evidence_refs = arguments.get("evidence_refs")
        if evidence_refs is not None and not isinstance(evidence_refs, list):
            raise ValueError("twin.record_claim: 'evidence_refs' must be an array")
        claim_type = arguments.get("claim_type")
        project_id = arguments.get("project_id")
        kwargs: dict[str, Any] = {
            "requirement_ref": requirement_ref,
            "artefact_ref": artefact_ref,
            "evidence_refs": evidence_refs,
            "project_id": project_id if isinstance(project_id, str) else None,
        }
        if isinstance(claim_type, str) and claim_type:
            kwargs["claim_type"] = claim_type
        return await self._claim_recorder(**kwargs)
