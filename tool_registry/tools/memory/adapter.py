"""Memory MCP tool adapter — wraps ``MemoryClient`` (MET-453).

Exposes the L2 agent-memory contract as one MCP tool:

* ``memory.retrieve_similar_experience`` — nearest-neighbour search over
  indexed ``AGENT_TASK_*`` events.

Layer note: ``tool_registry/CLAUDE.md`` normally bars imports from
``digital_twin``. Importing ``MemoryClient`` is an explicit exception
because that module is the published L2 contract — any backend the
tool registry knows how to talk to must satisfy it. No heavy
``digital_twin`` runtime code is pulled in.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.client import MAX_RETRIEVAL_LIMIT, MemoryClient
from digital_twin.memory.consolidation.insight import Insight, InsightStatus
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InsightStore
from digital_twin.memory.models import MemorySearchHit
from mcp_core.context import current_context
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.memory.adapter")


class MemoryServer(McpToolServer):
    """MCP server adapter around ``MemoryClient``.

    Takes the client lazily via ``set_client`` so the registry can
    bootstrap before the gateway has finished wiring the embedding
    service and experience store.
    """

    def __init__(
        self,
        client: MemoryClient | None = None,
        insight_store: InsightStore | None = None,
    ) -> None:
        super().__init__(adapter_id="memory", version="0.1.0")
        self._client: MemoryClient | None = client
        self._insight_store: InsightStore | None = insight_store
        self._register_tools()

    # ------------------------------------------------------------------
    # Late binding
    # ------------------------------------------------------------------

    def set_client(self, client: MemoryClient) -> None:
        """Bind a concrete ``MemoryClient`` after construction."""
        self._client = client
        logger.info("memory_mcp_client_bound", client=type(client).__name__)

    def set_insight_store(self, store: InsightStore) -> None:
        """Bind the consolidated-insight store (for ``memory.list_insights``)."""
        self._insight_store = store
        logger.info("memory_mcp_insight_store_bound", store=type(store).__name__)

    @property
    def client(self) -> MemoryClient:
        if self._client is None:
            raise RuntimeError(
                "MemoryServer.client was called before set_client(); "
                "ensure the gateway init wires app.state.memory_client in."
            )
        return self._client

    @property
    def insight_store(self) -> InsightStore:
        if self._insight_store is None:
            raise RuntimeError(
                "MemoryServer.insight_store was called before set_insight_store(); "
                "ensure the gateway init wires app.state.consolidation_insight_store in."
            )
        return self._insight_store

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="memory.retrieve_similar_experience",
                adapter_id="memory",
                name="Retrieve Similar Experience",
                description=(
                    "Semantic search over indexed agent-task experiences. "
                    "Returns the closest past run records by goal, with "
                    "cosine similarity, agent code, success flag, and the "
                    "result summary used to build the embedding."
                ),
                capability="memory_retrieval",
                input_schema={
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": (
                                "Natural-language description of what the "
                                "caller is trying to do. Embedded with the "
                                "same service used at index time."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_RETRIEVAL_LIMIT,
                            "default": 5,
                            "description": "Maximum number of experiences to return.",
                        },
                        "agent_code": {
                            "type": ["string", "null"],
                            "description": (
                                "Optional agent_code filter — only return "
                                "experiences produced by a specific agent."
                            ),
                        },
                        "only_success": {
                            "type": ["boolean", "null"],
                            "description": (
                                "When true, only successful experiences "
                                "(``AGENT_TASK_COMPLETED``) are returned. "
                                "When false, only failures. Null = no filter."
                            ),
                        },
                    },
                    "required": ["goal"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "hits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "experience_id": {"type": "string"},
                                    "similarity": {"type": "number"},
                                    "rank": {"type": "integer"},
                                    "agent_code": {"type": "string"},
                                    "task_type": {"type": "string"},
                                    "run_id": {"type": "string"},
                                    "step_id": {"type": "string"},
                                    "success": {"type": "boolean"},
                                    "duration_seconds": {"type": ["number", "null"]},
                                    "result_summary": {"type": "string"},
                                    "error": {"type": ["string", "null"]},
                                    "importance": {"type": "number"},
                                    "confidence": {"type": "string"},
                                    "timestamp": {"type": "string"},
                                    "project_id": {"type": ["string", "null"]},
                                },
                            },
                        }
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_retrieve_similar_experience,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="memory.list_insights",
                adapter_id="memory",
                name="List Consolidated Insights",
                description=(
                    "List synthesized consolidation insights (lessons learned "
                    "across agent-task experiences). Excludes stale (decayed) "
                    "insights by default; set include_stale=true for the audit "
                    "view. Optional theme filter."
                ),
                capability="memory_insights",
                input_schema={
                    "type": "object",
                    "properties": {
                        "theme": {
                            "type": ["string", "null"],
                            "description": (
                                "Optional consolidation theme filter "
                                "(e.g. mechanical_validation, power_analysis)."
                            ),
                        },
                        "include_stale": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, include STALE_WARN insights. Default "
                                "false — agents should act on fresh lessons."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "default": 50,
                            "description": "Maximum number of insights to return.",
                        },
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "insights": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "theme": {"type": "string"},
                                    "kind": {"type": "string"},
                                    "status": {"type": "string"},
                                    "narrative": {"type": "string"},
                                    "confidence": {"type": "number"},
                                    "confidence_tier": {"type": "string"},
                                    "supporting_experience_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "synthesized_at": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=10),
            ),
            handler=self.handle_list_insights,
        )

    # ------------------------------------------------------------------
    # Tool handler
    # ------------------------------------------------------------------

    async def handle_retrieve_similar_experience(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("memory.mcp.retrieve") as span:
            goal = arguments.get("goal")
            if not goal or not isinstance(goal, str):
                raise ValueError(
                    "memory.retrieve_similar_experience: 'goal' is required and must be a string"
                )
            limit_raw = arguments.get("limit", 5)
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "memory.retrieve_similar_experience: 'limit' must be an integer"
                ) from exc

            agent_code_raw = arguments.get("agent_code")
            agent_code: str | None = (
                agent_code_raw if isinstance(agent_code_raw, str) and agent_code_raw else None
            )

            only_success_raw = arguments.get("only_success")
            only_success: bool | None = (
                bool(only_success_raw) if isinstance(only_success_raw, bool) else None
            )

            project_id = _project_id_from_context()

            span.set_attribute("memory.goal_length", len(goal))
            span.set_attribute("memory.limit", limit)
            if project_id is not None:
                span.set_attribute("memory.project_id", str(project_id))
                span.set_attribute("mcp.project_id", str(project_id))

            hits = await self.client.retrieve_similar_experience(
                goal,
                limit=limit,
                project_id=project_id,
                agent_code=agent_code,
                only_success=only_success,
            )
            span.set_attribute("memory.result_count", len(hits))
            logger.info(
                "memory_retrieve",
                goal_length=len(goal),
                limit=limit,
                result_count=len(hits),
                project_id=str(project_id) if project_id else None,
                agent_code=agent_code,
            )
            return {"hits": [_hit_to_dict(h) for h in hits]}

    async def handle_list_insights(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("memory.mcp.list_insights") as span:
            theme_raw = arguments.get("theme")
            theme: ConsolidationTheme | None = None
            if isinstance(theme_raw, str) and theme_raw:
                try:
                    theme = ConsolidationTheme(theme_raw)
                except ValueError as exc:
                    raise ValueError(f"memory.list_insights: unknown theme {theme_raw!r}") from exc

            include_stale = bool(arguments.get("include_stale", False))
            limit_raw = arguments.get("limit", 50)
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("memory.list_insights: 'limit' must be an integer") from exc
            limit = max(1, min(limit, 500))

            span.set_attribute("memory.include_stale", include_stale)
            span.set_attribute("memory.limit", limit)
            if theme is not None:
                span.set_attribute("memory.theme", theme.value)

            # Over-fetch when filtering stale so the page doesn't under-fill.
            raw = await self.insight_store.list(
                theme=theme, limit=limit if include_stale else limit * 2
            )
            if include_stale:
                selected = raw[:limit]
            else:
                selected = [i for i in raw if i.status is not InsightStatus.STALE_WARN][:limit]

            span.set_attribute("memory.result_count", len(selected))
            logger.info(
                "memory_list_insights",
                theme=theme.value if theme else None,
                include_stale=include_stale,
                result_count=len(selected),
            )
            return {"insights": [_insight_to_dict(i) for i in selected]}


def _project_id_from_context() -> UUID | None:
    project_id = current_context().project_id
    if isinstance(project_id, UUID):
        return project_id
    if project_id is None:
        return None
    try:
        return UUID(str(project_id))
    except (TypeError, ValueError):
        return None


def _hit_to_dict(hit: MemorySearchHit) -> dict[str, Any]:
    exp = hit.experience
    return {
        "experience_id": str(exp.id),
        "similarity": hit.similarity,
        "rank": hit.rank,
        "agent_code": exp.agent_code,
        "task_type": exp.task_type,
        "run_id": exp.run_id,
        "step_id": exp.step_id,
        "success": exp.success,
        "duration_seconds": exp.duration_seconds,
        "result_summary": exp.result_summary,
        "error": exp.error,
        "importance": exp.importance,
        "confidence": str(exp.confidence),
        "timestamp": exp.timestamp.isoformat(),
        "project_id": str(exp.project_id) if exp.project_id else None,
    }


def _insight_to_dict(insight: Insight) -> dict[str, Any]:
    return {
        "id": str(insight.id),
        "theme": insight.theme.value,
        "kind": insight.kind.value,
        "status": insight.status.value,
        "narrative": insight.narrative,
        "confidence": insight.confidence,
        "confidence_tier": insight.confidence_tier.value,
        "supporting_experience_ids": [str(eid) for eid in insight.supporting_experience_ids],
        "synthesized_at": insight.synthesized_at.isoformat(),
    }
