"""Knowledge MCP tool adapter — wraps ``KnowledgeService`` (MET-335).

Exposes the L1 knowledge contract as two MCP tools:

* ``knowledge.search`` — semantic + keyword retrieval
* ``knowledge.ingest`` — write-through ingestion

…and two MCP resources (MET-384, L1-B1):

* ``metaforge://knowledge/sources`` — ``SourceSummary`` list summary.
* ``metaforge://knowledge/sources/{id}`` — per-source detail keyed
  on a URL-encoded ``source_path`` (the natural identifier — there is
  no separate stable id in this codebase).

The adapter depends only on ``digital_twin.knowledge.service``
(the framework-agnostic Protocol from MET-346 / ADR-008). It never
imports LightRAG or any other concrete backend, so swapping the
provider via ``create_knowledge_service(provider=...)`` requires no
change here.

Layer note: ``tool_registry/CLAUDE.md`` normally bars imports from
``digital_twin``. Importing the ``KnowledgeService`` Protocol +
``SearchHit`` / ``IngestResult`` / ``SourceSummary`` dataclasses is an
explicit exception because that module is the published L1 contract —
any backend the tool registry knows how to talk to must satisfy it.
No heavy ``digital_twin`` runtime code is pulled in.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

import structlog

# L1 contract import — see module docstring for the layer-rule rationale.
from digital_twin.knowledge.service import (
    IngestResult,
    KnowledgeService,
    SearchHit,
    SourceSummary,
)
from digital_twin.knowledge.types import KnowledgeType
from mcp_core.context import current_context
from mcp_core.errors import ErrorCode, make_tool_error
from mcp_core.progress import emit_progress
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import (
    ResourceLimits,
    ResourceManifestEntry,
    ResourceNotFoundError,
    ToolManifest,
)
from tool_registry.mcp_server.server import McpToolServer

# Resource URI constants — kept here so a typo can't drift the list URI
# from the templated URI silently.
_SOURCES_LIST_URI = "metaforge://knowledge/sources"
_SOURCES_ITEM_PREFIX = "metaforge://knowledge/sources/"
_SOURCES_ITEM_TEMPLATE = "metaforge://knowledge/sources/{id}"

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
        self._register_resources()

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
                    "Ingest one document (single-payload mode) or a batch of "
                    "documents (``files: [...]`` mode) into the L1 knowledge "
                    "layer. Heading-aware chunking and citation metadata are "
                    "handled by the underlying provider. Batch mode emits a "
                    "``notifications/progress`` event after each file."
                ),
                capability="knowledge_ingest",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "Single-payload mode: document content as a "
                                "string. Mutually exclusive with ``files``."
                            ),
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
                        "files": {
                            "type": "array",
                            "description": (
                                "Multi-file mode (MET-388, L1-B2): each entry is "
                                "an object with ``content``, ``source_path``, "
                                "``knowledge_type``, optional ``source_work_product_id`` "
                                "and ``metadata``. The handler emits a progress "
                                "notification after each file is ingested."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "source_path": {"type": "string"},
                                    "knowledge_type": {
                                        "type": "string",
                                        "enum": _KNOWLEDGE_TYPE_VALUES,
                                    },
                                    "source_work_product_id": {
                                        "type": ["string", "null"],
                                    },
                                    "metadata": {"type": "object"},
                                },
                                "required": ["content", "source_path", "knowledge_type"],
                            },
                        },
                        "request_id": {
                            "type": "string",
                            "description": (
                                "Optional client-supplied request id stamped on "
                                "progress notifications so callers can correlate "
                                "them with the originating tool/call."
                            ),
                        },
                    },
                    # ``required`` documents the legacy single-payload
                    # shape. Multi-file callers pass ``files`` instead;
                    # the handler enforces per-entry required keys at
                    # runtime so the discovery shape stays stable for
                    # existing consumers.
                    "required": ["content", "source_path", "knowledge_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "entry_ids": {"type": "array", "items": {"type": "string"}},
                        "chunks_indexed": {"type": "integer"},
                        "source_path": {"type": "string"},
                        "files_ingested": {"type": "integer"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "entry_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "chunks_indexed": {"type": "integer"},
                                    "source_path": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
                # MET-388 (L1-B2): batch mode emits notifications/progress.
                supports_progress=True,
            ),
            handler=self.handle_ingest,
        )

    # ------------------------------------------------------------------
    # Resource registration (MET-384)
    # ------------------------------------------------------------------

    def _register_resources(self) -> None:
        """Wire the two ``metaforge://knowledge/sources`` URIs.

        The list URI surfaces a ``SourceSummary`` array; the templated
        item URI carries a URL-encoded ``source_path`` as ``{id}`` so
        callers can round-trip arbitrary file paths / URLs without a
        separate stable id (none exists in the codebase today).
        """
        self.register_resource(
            manifest=ResourceManifestEntry(
                uri_template=_SOURCES_LIST_URI,
                name="Knowledge sources",
                description=(
                    "List of ingested knowledge sources, one row per "
                    "(source_path, knowledge_type) pair, with fragment "
                    "count and last-indexed timestamp."
                ),
                mime_type="application/json",
                adapter_id="knowledge",
            ),
            reader=self._read_sources_list,
            # Exact-match: only the bare list URI lands here. The
            # templated reader takes everything under
            # ``metaforge://knowledge/sources/`` (note the trailing
            # slash) so the two registrations don't fight.
            matcher=lambda uri: uri == _SOURCES_LIST_URI,
        )

        self.register_resource(
            manifest=ResourceManifestEntry(
                uri_template=_SOURCES_ITEM_TEMPLATE,
                name="Knowledge source detail",
                description=(
                    "Per-source detail (source_path, knowledge_type, "
                    "fragment_count, indexed_at, metadata, chunks). "
                    "{id} is the URL-encoded source_path."
                ),
                mime_type="application/json",
                adapter_id="knowledge",
            ),
            reader=self._read_source_detail,
            matcher=lambda uri: uri.startswith(_SOURCES_ITEM_PREFIX) and uri != _SOURCES_LIST_URI,
        )

    # ------------------------------------------------------------------
    # Resource readers
    # ------------------------------------------------------------------

    async def _read_sources_list(self, uri: str) -> list[dict[str, Any]]:
        """Reader for ``metaforge://knowledge/sources`` (MET-384).

        Delegates to ``KnowledgeService.list_sources()`` (L1-A8) and
        forwards the ambient ``project_id`` from the call context so
        tenant scoping mirrors the search/ingest tools.
        """
        with tracer.start_as_current_span("knowledge.mcp.resources.list_sources") as span:
            project_id = current_context().project_id
            span.set_attribute("knowledge.resource.uri", uri)
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))

            summaries = await self.service.list_sources(project_id=project_id)
            payload = {"sources": [_source_summary_to_dict(s) for s in summaries]}
            span.set_attribute("knowledge.result_count", len(summaries))
            logger.info(
                "knowledge_resource_read",
                uri=uri,
                result_count=len(summaries),
                not_found=False,
            )
            return [
                {
                    "uri": uri,
                    "mime_type": "application/json",
                    "text": json.dumps(payload),
                }
            ]

    async def _read_source_detail(self, uri: str) -> list[dict[str, Any]]:
        """Reader for ``metaforge://knowledge/sources/{id}`` (MET-384).

        ``{id}`` is a URL-encoded ``source_path`` (the natural
        identifier — no separate stable id exists in the codebase).
        Resolves the source by scanning ``list_sources()`` for an
        exact match. On miss, raises ``ResourceNotFoundError`` so the
        server emits the MET-385 not_found envelope with the offending
        URI in ``data``.

        Chunks come from a filtered ``search()`` keyed on
        ``source_path``. They may be empty if no backend hits surface
        for the source (e.g. a freshly-deleted source mid-flight) —
        the field is always present.
        """
        with tracer.start_as_current_span("knowledge.mcp.resources.source_detail") as span:
            raw_id = uri[len(_SOURCES_ITEM_PREFIX) :]
            source_path = unquote(raw_id)
            project_id = current_context().project_id
            span.set_attribute("knowledge.resource.uri", uri)
            span.set_attribute("knowledge.source_path", source_path)
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))

            summaries = await self.service.list_sources(project_id=project_id)
            match = next((s for s in summaries if s.source_path == source_path), None)
            if match is None:
                span.set_attribute("knowledge.not_found", True)
                logger.info(
                    "knowledge_resource_read",
                    uri=uri,
                    not_found=True,
                )
                err = make_tool_error(
                    ErrorCode.NOT_FOUND,
                    f"No knowledge source registered for {source_path!r}",
                    details={"uri": uri, "source_path": source_path},
                )
                # The server wraps ResourceNotFoundError into a JSON-RPC
                # -32004 with ``data.uri`` populated. Stash the MET-385
                # envelope on the exception so transports / tests can
                # surface it without an extra contract change.
                exc = ResourceNotFoundError(uri)
                exc.error_envelope = err.model_dump()  # type: ignore[attr-defined]
                raise exc

            # Best-effort chunk fetch. ``search`` with a source_path
            # filter is the only public path the L1 contract exposes —
            # the dedicated chunk-by-source method lands separately.
            try:
                hits = await self.service.search(
                    query=source_path,
                    top_k=max(match.fragment_count, 1),
                    filters={"source_path": source_path},
                    project_id=project_id,
                )
            except Exception as exc:  # pragma: no cover — defensive
                # A flaky search backend must not break resource reads.
                logger.warning(
                    "knowledge_resource_chunk_fetch_failed",
                    uri=uri,
                    error=str(exc),
                )
                hits = []

            payload = _source_summary_to_dict(match)
            payload["chunks"] = [_hit_to_dict(h) for h in hits]
            span.set_attribute("knowledge.result_count", len(hits))
            logger.info(
                "knowledge_resource_read",
                uri=uri,
                result_count=len(hits),
                not_found=False,
            )
            return [
                {
                    "uri": uri,
                    "mime_type": "application/json",
                    "text": json.dumps(payload),
                }
            ]

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
            # MET-401 / MET-387: forward project_id and actor_id from the
            # active call-context. project_id scopes retrieval (search
            # falls back to "default tenant" when None); actor_id is an
            # attribution signal — it rides through as an OTel/log
            # attribute and never filters hits.
            ctx = current_context()
            project_id = ctx.project_id
            actor_id = _resolve_actor_id(ctx.actor_id)
            span.set_attribute("knowledge.query_length", len(query))
            span.set_attribute("knowledge.top_k", top_k)
            # MET-387: stamp the standard mcp.* attributes onto the
            # adapter span so it joins up with the McpToolServer parent
            # span the harness already correlates on.
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))
                span.set_attribute("mcp.project_id", str(project_id))
            if actor_id is not None:
                span.set_attribute("mcp.actor_id", actor_id)

            hits = await self.service.search(
                query=query,
                top_k=top_k,
                knowledge_type=knowledge_type,
                filters=filters,
                project_id=project_id,
                actor_id=actor_id,
            )
            span.set_attribute("knowledge.result_count", len(hits))
            logger.info(
                "knowledge_search",
                top_k=top_k,
                result_count=len(hits),
                project_id=str(project_id) if project_id is not None else None,
                actor_id=actor_id,
            )
            return {"hits": [_hit_to_dict(h) for h in hits]}

    async def handle_ingest(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # MET-388 (L1-B2): batch mode short-circuits when ``files`` is set.
        # Single-payload mode stays silent (no progress) for back-compat.
        files = arguments.get("files")
        if files is not None:
            if not isinstance(files, list):
                raise ValueError("knowledge.ingest: 'files' must be a list when provided")
            return await self._handle_ingest_batch(files, arguments)
        return await self._handle_ingest_single(arguments)

    async def _handle_ingest_single(self, arguments: dict[str, Any]) -> dict[str, Any]:
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
            # MET-401 / MET-387: stamp the active call-context's
            # project_id and actor_id onto the ingest. project_id scopes
            # the chunks to a tenant; actor_id is forwarded as both a
            # span attribute and per-chunk metadata so attribution
            # survives a round-trip through the store.
            ctx = current_context()
            project_id = ctx.project_id
            actor_id = _resolve_actor_id(ctx.actor_id)

            span.set_attribute("knowledge.source_path", source_path)
            span.set_attribute("knowledge.type", str(knowledge_type))
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))
                span.set_attribute("mcp.project_id", str(project_id))
            if actor_id is not None:
                span.set_attribute("mcp.actor_id", actor_id)

            result = await self.service.ingest(
                content=content,
                source_path=source_path,
                knowledge_type=knowledge_type,
                source_work_product_id=wp_id,
                metadata=metadata,
                project_id=project_id,
                actor_id=actor_id,
            )
            logger.info(
                "knowledge_ingest",
                source_path=source_path,
                chunks_indexed=result.chunks_indexed,
                project_id=str(project_id) if project_id is not None else None,
                actor_id=actor_id,
            )
            return _ingest_result_to_dict(result)

    async def _handle_ingest_batch(
        self,
        files: list[Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Batch ingest with per-file ``notifications/progress`` (MET-388).

        Each entry must be an object with ``content``, ``source_path``,
        ``knowledge_type`` (and optional ``source_work_product_id`` /
        ``metadata``). After every file lands the handler emits one
        progress event whose ``progress`` value advances monotonically
        from ``1/total`` toward ``1.0`` and ``message`` carries the
        source path. Single-payload callers (no ``files`` key) keep the
        legacy silent behaviour for back-compat.
        """
        with tracer.start_as_current_span("knowledge.mcp.ingest.batch") as span:
            total = len(files)
            span.set_attribute("knowledge.batch_size", total)
            if total == 0:
                # Empty batches are a no-op rather than a hard error so
                # callers can pass through whatever the file walk
                # produced without first checking length.
                return {
                    "files_ingested": 0,
                    "chunks_indexed": 0,
                    "files": [],
                }

            ctx = current_context()
            project_id = ctx.project_id
            actor_id = _resolve_actor_id(ctx.actor_id)
            if project_id is not None:
                span.set_attribute("knowledge.project_id", str(project_id))
                span.set_attribute("mcp.project_id", str(project_id))
            if actor_id is not None:
                span.set_attribute("mcp.actor_id", actor_id)

            # Caller-supplied id correlates progress events with the
            # originating ``tool/call``. When absent we synthesise one
            # tied to the source set so the contextvar-based emitter
            # doesn't drop the events.
            request_id = arguments.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                request_id = f"knowledge.ingest.batch.{id(arguments)}"

            per_file_results: list[dict[str, Any]] = []
            total_chunks = 0
            all_entry_ids: list[str] = []

            for index, raw_entry in enumerate(files):
                if not isinstance(raw_entry, dict):
                    raise ValueError(f"knowledge.ingest: files[{index}] must be an object")
                content = raw_entry.get("content")
                source_path = raw_entry.get("source_path")
                kt_raw = raw_entry.get("knowledge_type")
                if not content or not isinstance(content, str):
                    raise ValueError(
                        f"knowledge.ingest: files[{index}].content is required and must be a string"
                    )
                if not source_path or not isinstance(source_path, str):
                    raise ValueError(
                        f"knowledge.ingest: files[{index}].source_path is required "
                        "and must be a string"
                    )
                knowledge_type = self._coerce_knowledge_type(kt_raw)
                if knowledge_type is None:
                    raise ValueError(
                        f"knowledge.ingest: files[{index}].knowledge_type must be one of "
                        f"{_KNOWLEDGE_TYPE_VALUES}"
                    )
                wp_id = self._coerce_uuid(raw_entry.get("source_work_product_id"))
                metadata = raw_entry.get("metadata") or None

                span.set_attribute("knowledge.batch_index", index)

                result = await self.service.ingest(
                    content=content,
                    source_path=source_path,
                    knowledge_type=knowledge_type,
                    source_work_product_id=wp_id,
                    metadata=metadata,
                    project_id=project_id,
                    actor_id=actor_id,
                )

                serialised = _ingest_result_to_dict(result)
                per_file_results.append(serialised)
                total_chunks += int(serialised.get("chunks_indexed", 0) or 0)
                all_entry_ids.extend(serialised.get("entry_ids", []) or [])

                current = index + 1
                # ``progress`` is a fraction in [0, 1]; one event per
                # file keeps the cadence predictable for harnesses that
                # assert a per-file count.
                progress_value = current / total
                message = f"ingested {current}/{total}: {source_path}"
                emitted = await emit_progress(
                    request_id=request_id,
                    progress=progress_value,
                    message=message,
                )
                logger.info(
                    "knowledge_ingest_progress",
                    batch_index=index,
                    batch_size=total,
                    source_path=source_path,
                    progress=progress_value,
                    emitted=emitted,
                    actor_id=actor_id,
                )

            return {
                "files_ingested": total,
                "chunks_indexed": total_chunks,
                "entry_ids": all_entry_ids,
                "files": per_file_results,
            }

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


def _resolve_actor_id(raw: str | None) -> str | None:
    """Project the call-context ``actor_id`` to forwarded form (MET-387).

    The context model defaults ``actor_id`` to ``"system:unattributed"``
    so it always resolves to a string — but at the adapter boundary
    "no caller identity" should travel as ``None`` so the service /
    span / log layer can elide the field entirely instead of stamping
    a misleading sentinel onto every chunk's metadata. Empty strings
    follow the same rule for defence in depth.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned or cleaned == "system:unattributed":
        return None
    return cleaned


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


def _source_summary_to_dict(summary: SourceSummary) -> dict[str, Any]:
    """Wire-safe serialization of a ``SourceSummary``.

    ``knowledge_type`` may already be a string (legacy rows) or a
    ``KnowledgeType`` enum — both are coerced to the bare string form
    so downstream JSON consumers don't have to branch.
    ``indexed_at`` is emitted as ISO-8601.
    """
    kt = summary.knowledge_type
    if isinstance(kt, KnowledgeType):
        kt_str: str | None = str(kt)
    elif kt is None:
        kt_str = None
    else:
        kt_str = str(kt)
    indexed_at = summary.indexed_at
    indexed_at_str = indexed_at.isoformat() if isinstance(indexed_at, datetime) else str(indexed_at)
    return {
        "source_path": summary.source_path,
        "knowledge_type": kt_str,
        "fragment_count": summary.fragment_count,
        "indexed_at": indexed_at_str,
        "metadata": dict(summary.metadata or {}),
    }


def _encode_source_id(source_path: str) -> str:
    """URL-encode a ``source_path`` for embedding in the templated URI.

    Kept here as a helper (vs inlined ``quote(safe="")``) so callers
    constructing resource links — CLI, dashboard — share the exact
    encoding rules with the reader's ``unquote`` round-trip.
    """
    return quote(source_path, safe="")
