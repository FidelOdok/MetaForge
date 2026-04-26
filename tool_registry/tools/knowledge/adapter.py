"""Knowledge MCP tool adapter — wraps ``KnowledgeService`` (MET-335).

Exposes the L1 knowledge contract as two MCP tools:

* ``knowledge.search`` — semantic + keyword retrieval
* ``knowledge.ingest`` — write-through ingestion

The adapter depends only on ``digital_twin.knowledge.service``
(the framework-agnostic Protocol from MET-346 / ADR-008). It never
imports LightRAG or any other concrete backend, so swapping the
provider via ``create_knowledge_service(provider=...)`` requires no
change here.

Layer note: ``tool_registry/CLAUDE.md`` normally bars imports from
``digital_twin``. Importing the ``KnowledgeService`` Protocol +
``SearchHit`` / ``IngestResult`` dataclasses is an explicit exception
because that module is the published L1 contract — any backend the
tool registry knows how to talk to must satisfy it. No heavy
``digital_twin`` runtime code is pulled in.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

# L1 contract import — see module docstring for the layer-rule rationale.
from digital_twin.knowledge.service import IngestResult, KnowledgeService, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.knowledge.adapter")


_KNOWLEDGE_TYPE_VALUES = sorted(kt.value for kt in KnowledgeType)


class KnowledgeServer(McpToolServer):
    """MCP server adapter around ``KnowledgeService``.

    The constructor takes a *factory* rather than a service instance so
    construction at registry-bootstrap time can be lazy — the adapter
    is built before the gateway has finished initialising the knowledge
    service. ``set_service`` is the late-binding hook the gateway calls
    once ``app.state.knowledge_service`` is available.
    """

    def __init__(self, service: KnowledgeService | None = None) -> None:
        super().__init__(adapter_id="knowledge", version="0.1.0")
        self._service: KnowledgeService | None = service
        self._register_tools()

    # ------------------------------------------------------------------
    # Late binding
    # ------------------------------------------------------------------

    def set_service(self, service: KnowledgeService) -> None:
        """Bind a concrete ``KnowledgeService`` after construction."""
        self._service = service
        logger.info("knowledge_mcp_service_bound", service=type(service).__name__)

    @property
    def service(self) -> KnowledgeService:
        if self._service is None:
            raise RuntimeError(
                "KnowledgeServer.service was called before set_service(); "
                "ensure the gateway init wires app.state.knowledge_service in."
            )
        return self._service

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="knowledge.search",
                adapter_id="knowledge",
                name="Search Knowledge",
                description=(
                    "Semantic search over the L1 knowledge layer. Returns "
                    "ranked chunks with citations (source_path, heading, "
                    "chunk_index)."
                ),
                capability="knowledge_retrieval",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query.",
                        },
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 5,
                            "description": "Maximum number of hits to return.",
                        },
                        "knowledge_type": {
                            "type": "string",
                            "enum": _KNOWLEDGE_TYPE_VALUES,
                            "description": "Optional knowledge_type filter.",
                        },
                        "filters": {
                            "type": "object",
                            "description": (
                                "Optional metadata filters keyed on "
                                "source_path / source_work_product_id / arbitrary keys."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "hits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "similarity_score": {"type": "number"},
                                    "source_path": {"type": ["string", "null"]},
                                    "heading": {"type": ["string", "null"]},
                                    "chunk_index": {"type": ["integer", "null"]},
                                    "total_chunks": {"type": ["integer", "null"]},
                                    "metadata": {"type": "object"},
                                    "knowledge_type": {"type": ["string", "null"]},
                                    "source_work_product_id": {"type": ["string", "null"]},
                                },
                            },
                        },
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=512, max_cpu_seconds=30),
            ),
            handler=self.handle_search,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="knowledge.ingest",
                adapter_id="knowledge",
                name="Ingest Knowledge",
                description=(
                    "Ingest a document (markdown / plain text) into the L1 "
                    "knowledge layer. Heading-aware chunking and citation "
                    "metadata are handled by the underlying provider."
                ),
                capability="knowledge_ingest",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Document content as a single string.",
                        },
                        "source_path": {
                            "type": "string",
                            "description": (
                                "Stable identifier for the source — file path, URL, or "
                                "``work_product://<uuid>``. Used as the dedup key for "
                                "re-ingest."
                            ),
                        },
                        "knowledge_type": {
                            "type": "string",
                            "enum": _KNOWLEDGE_TYPE_VALUES,
                            "description": "Knowledge category.",
                        },
                        "source_work_product_id": {
                            "type": ["string", "null"],
                            "description": "Optional UUID of the source work_product.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Arbitrary metadata round-tripped on search hits.",
                        },
                    },
                    "required": ["content", "source_path", "knowledge_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "entry_ids": {"type": "array", "items": {"type": "string"}},
                        "chunks_indexed": {"type": "integer"},
                        "source_path": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
            ),
            handler=self.handle_ingest,
        )

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def handle_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("knowledge.mcp.search") as span:
            query = arguments.get("query")
            if not query or not isinstance(query, str):
                raise ValueError("knowledge.search: 'query' is required and must be a string")
            top_k = int(arguments.get("top_k", 5))
            kt_raw = arguments.get("knowledge_type")
            knowledge_type = self._coerce_knowledge_type(kt_raw)
            filters = arguments.get("filters") or None
            span.set_attribute("knowledge.query_length", len(query))
            span.set_attribute("knowledge.top_k", top_k)

            hits = await self.service.search(
                query=query,
                top_k=top_k,
                knowledge_type=knowledge_type,
                filters=filters,
            )
            span.set_attribute("knowledge.result_count", len(hits))
            return {"hits": [_hit_to_dict(h) for h in hits]}

    async def handle_ingest(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("knowledge.mcp.ingest") as span:
            content = arguments.get("content")
            source_path = arguments.get("source_path")
            kt_raw = arguments.get("knowledge_type")
            if not content or not isinstance(content, str):
                raise ValueError("knowledge.ingest: 'content' is required and must be a string")
            if not source_path or not isinstance(source_path, str):
                raise ValueError("knowledge.ingest: 'source_path' is required and must be a string")
            knowledge_type = self._coerce_knowledge_type(kt_raw)
            if knowledge_type is None:
                raise ValueError(
                    f"knowledge.ingest: 'knowledge_type' must be one of {_KNOWLEDGE_TYPE_VALUES}"
                )
            wp_id = self._coerce_uuid(arguments.get("source_work_product_id"))
            metadata = arguments.get("metadata") or None

            span.set_attribute("knowledge.source_path", source_path)
            span.set_attribute("knowledge.type", str(knowledge_type))

            result = await self.service.ingest(
                content=content,
                source_path=source_path,
                knowledge_type=knowledge_type,
                source_work_product_id=wp_id,
                metadata=metadata,
            )
            return _ingest_result_to_dict(result)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_knowledge_type(value: Any) -> KnowledgeType | None:
        if value is None or value == "":
            return None
        if isinstance(value, KnowledgeType):
            return value
        try:
            return KnowledgeType(str(value))
        except ValueError:
            return None

    @staticmethod
    def _coerce_uuid(value: Any) -> UUID | None:
        if value is None or value == "":
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None


def _hit_to_dict(hit: SearchHit) -> dict[str, Any]:
    """Wire-safe serialization of a ``SearchHit``.

    UUIDs and ``KnowledgeType`` are coerced to strings so the result is
    JSON-encodable by the MCP transport layer.
    """
    return {
        "content": hit.content,
        "similarity_score": hit.similarity_score,
        "source_path": hit.source_path,
        "heading": hit.heading,
        "chunk_index": hit.chunk_index,
        "total_chunks": hit.total_chunks,
        "metadata": hit.metadata,
        "knowledge_type": str(hit.knowledge_type) if hit.knowledge_type is not None else None,
        "source_work_product_id": (
            str(hit.source_work_product_id) if hit.source_work_product_id is not None else None
        ),
    }


def _ingest_result_to_dict(result: IngestResult) -> dict[str, Any]:
    return {
        "entry_ids": [str(eid) for eid in result.entry_ids],
        "chunks_indexed": result.chunks_indexed,
        "source_path": result.source_path,
    }
