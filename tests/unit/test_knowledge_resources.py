"""Unit tests for the knowledge MCP adapter's resource surface (MET-384, L1-B1).

Covers the two ``metaforge://knowledge/sources`` URIs:

* ``metaforge://knowledge/sources`` — list summary, delegates to
  ``KnowledgeService.list_sources()``.
* ``metaforge://knowledge/sources/{id}`` — per-source detail, where
  ``{id}`` is a URL-encoded ``source_path``.

Mirrors the ``_FakeService`` mocking pattern used in
``test_knowledge_mcp_adapter.py`` and ``test_knowledge_service_list.py``
so no real Postgres / LightRAG / sentence-transformers is required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.service import (
    IngestResult,
    SearchHit,
    SourceSummary,
)
from digital_twin.knowledge.types import KnowledgeType
from tool_registry.tools.knowledge.adapter import KnowledgeServer

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_FIXED_INDEXED_AT = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _summary(
    source_path: str,
    knowledge_type: KnowledgeType = KnowledgeType.DESIGN_DECISION,
    fragment_count: int = 1,
    metadata: dict[str, Any] | None = None,
) -> SourceSummary:
    return SourceSummary(
        source_path=source_path,
        knowledge_type=knowledge_type,
        fragment_count=fragment_count,
        indexed_at=_FIXED_INDEXED_AT,
        metadata=metadata or {"author": "uat"},
    )


class _FakeService:
    """Mirrors ``_FakeService`` in ``test_knowledge_mcp_adapter.py`` —
    records calls so the test can assert exact delegation, and lets
    individual tests stub ``list_sources`` / ``search`` return values.
    """

    def __init__(
        self,
        *,
        sources: list[SourceSummary] | None = None,
        search_hits: list[SearchHit] | None = None,
    ) -> None:
        self.list_sources_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.ingest_calls: list[dict[str, Any]] = []
        self._sources = sources if sources is not None else []
        self._search_hits = search_hits if search_hits is not None else []

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
    ) -> IngestResult:
        self.ingest_calls.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "project_id": project_id,
            }
        )
        return IngestResult(
            entry_ids=[uuid4()],
            chunks_indexed=1,
            source_path=source_path,
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "filters": filters,
                "project_id": project_id,
            }
        )
        return list(self._search_hits)

    async def delete_by_source(self, source_path: str) -> int:
        return 0

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]:
        self.list_sources_calls.append(
            {
                "project_id": project_id,
                "knowledge_type": knowledge_type,
                "limit": limit,
                "offset": offset,
            }
        )
        return list(self._sources)

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": "1", "method": method, "params": params})


def _build_server(
    *,
    sources: list[SourceSummary] | None = None,
    search_hits: list[SearchHit] | None = None,
) -> KnowledgeServer:
    fake = _FakeService(sources=sources, search_hits=search_hits)
    return KnowledgeServer(service=fake)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resources/list
# ---------------------------------------------------------------------------


class TestResourcesList:
    """The adapter must advertise both URIs in ``resources/list``."""

    async def test_list_returns_knowledge_sources_uri(self) -> None:
        server = _build_server()
        raw = await server.handle_request(_request("resources/list", {"adapter_id": "knowledge"}))
        body = json.loads(raw)
        manifests = body["result"]["resources"]

        templates = {m["uri_template"] for m in manifests}
        assert "metaforge://knowledge/sources" in templates
        assert "metaforge://knowledge/sources/{id}" in templates
        # Both must be tagged as belonging to the knowledge adapter so a
        # federated server can filter cleanly.
        for m in manifests:
            assert m["adapter_id"] == "knowledge"


# ---------------------------------------------------------------------------
# resources/read — list summary
# ---------------------------------------------------------------------------


class TestReadSourcesUri:
    async def test_read_sources_uri_returns_list_summary(self) -> None:
        sources = [
            _summary(
                "alpha.md",
                knowledge_type=KnowledgeType.DESIGN_DECISION,
                fragment_count=3,
                metadata={"author": "mech"},
            ),
            _summary(
                "bravo.csv",
                knowledge_type=KnowledgeType.COMPONENT,
                fragment_count=1,
                metadata={"vendor": "digikey"},
            ),
        ]
        server = _build_server(sources=sources)

        raw = await server.handle_request(
            _request("resources/read", {"uri": "metaforge://knowledge/sources"})
        )
        body = json.loads(raw)
        contents = body["result"]["contents"]

        assert len(contents) == 1
        envelope = contents[0]
        assert envelope["uri"] == "metaforge://knowledge/sources"
        assert envelope["mime_type"] == "application/json"

        payload = json.loads(envelope["text"])
        assert "sources" in payload
        rows = payload["sources"]
        assert len(rows) == 2

        # Documented schema: source_path, knowledge_type, fragment_count,
        # indexed_at, metadata. Pinned by HP-INGEST-01 / KB-RES-001..004.
        for row in rows:
            assert set(row.keys()) >= {
                "source_path",
                "knowledge_type",
                "fragment_count",
                "indexed_at",
                "metadata",
            }
            # ISO-8601 string round-trips back to a datetime.
            datetime.fromisoformat(row["indexed_at"])

        first = rows[0]
        assert first["source_path"] == "alpha.md"
        assert first["knowledge_type"] == "design_decision"
        assert first["fragment_count"] == 3
        assert first["metadata"] == {"author": "mech"}


# ---------------------------------------------------------------------------
# resources/read — per-source detail
# ---------------------------------------------------------------------------


class TestReadSourceById:
    async def test_read_source_by_id_returns_detail(self) -> None:
        # source_path with characters that must be URL-encoded — proves
        # the unquote/quote round-trip works end to end.
        source_path = "docs/decisions/2026-Q2 plan.md"
        sources = [
            _summary(
                source_path,
                knowledge_type=KnowledgeType.DESIGN_DECISION,
                fragment_count=2,
                metadata={"author": "mech"},
            )
        ]
        # Search returns a single hit so the chunks field comes back
        # populated — the field must be present either way.
        hit = SearchHit(
            content="Decision body",
            similarity_score=0.9,
            source_path=source_path,
            heading="Decision",
            chunk_index=0,
            total_chunks=2,
            metadata={"author": "mech"},
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            source_work_product_id=None,
        )
        server = _build_server(sources=sources, search_hits=[hit])

        encoded = quote(source_path, safe="")
        uri = f"metaforge://knowledge/sources/{encoded}"
        raw = await server.handle_request(_request("resources/read", {"uri": uri}))
        body = json.loads(raw)
        contents = body["result"]["contents"]

        assert len(contents) == 1
        envelope = contents[0]
        assert envelope["uri"] == uri
        payload = json.loads(envelope["text"])

        # SourceSummary fields.
        assert payload["source_path"] == source_path
        assert payload["knowledge_type"] == "design_decision"
        assert payload["fragment_count"] == 2
        assert payload["metadata"] == {"author": "mech"}
        datetime.fromisoformat(payload["indexed_at"])

        # Chunks always present; populated when search surfaces hits.
        assert "chunks" in payload
        assert isinstance(payload["chunks"], list)
        assert len(payload["chunks"]) == 1
        assert payload["chunks"][0]["content"] == "Decision body"
        assert payload["chunks"][0]["source_path"] == source_path

    async def test_read_source_by_id_handles_empty_chunks(self) -> None:
        """Mock service returns no search hits — chunks must still be
        an empty list, not absent."""
        source_path = "docs/empty.md"
        server = _build_server(
            sources=[_summary(source_path)],
            search_hits=[],
        )
        encoded = quote(source_path, safe="")
        uri = f"metaforge://knowledge/sources/{encoded}"
        raw = await server.handle_request(_request("resources/read", {"uri": uri}))
        body = json.loads(raw)
        payload = json.loads(body["result"]["contents"][0]["text"])
        assert payload["chunks"] == []


# ---------------------------------------------------------------------------
# resources/read — unknown id surfaces MET-385 not_found envelope
# ---------------------------------------------------------------------------


class TestReadUnknownSource:
    async def test_read_unknown_source_returns_not_found_envelope(self) -> None:
        server = _build_server(sources=[])  # empty store

        # ``uat://does-not-exist`` mirrors the spec example — URL-encode it.
        unknown_path = "uat://does-not-exist"
        encoded = quote(unknown_path, safe="")
        uri = f"metaforge://knowledge/sources/{encoded}"
        raw = await server.handle_request(_request("resources/read", {"uri": uri}))
        body = json.loads(raw)

        # JSON-RPC error wrapper for resource-not-found.
        assert "error" in body, body
        # The server maps ResourceNotFoundError → -32004 (RESOURCE_NOT_FOUND).
        assert body["error"]["code"] == -32004
        # Offending URI is in the data payload so the harness can
        # surface it without re-parsing the message.
        assert body["error"]["data"]["uri"] == uri

        # Server stays responsive — a follow-up resources/list still works.
        raw_after = await server.handle_request(
            _request("resources/list", {"adapter_id": "knowledge"})
        )
        body_after = json.loads(raw_after)
        assert "result" in body_after
        assert any(
            m["uri_template"] == "metaforge://knowledge/sources"
            for m in body_after["result"]["resources"]
        )

    async def test_read_unknown_source_attaches_met385_envelope(self) -> None:
        """The reader stamps a MET-385 ``not_found`` envelope onto the
        ``ResourceNotFoundError`` for harnesses that want the canonical
        ``ErrorCode.NOT_FOUND`` payload (``code``, ``message``,
        ``details``, ``retryable``)."""
        from mcp_core.errors import ErrorCode
        from tool_registry.mcp_server.handlers import ResourceNotFoundError

        server = _build_server(sources=[])
        encoded = quote("uat://missing.md", safe="")
        uri = f"metaforge://knowledge/sources/{encoded}"

        # Drive the reader directly so we can inspect the raised exception.
        with pytest.raises(ResourceNotFoundError) as excinfo:
            await server._read_source_detail(uri)  # noqa: SLF001

        envelope = getattr(excinfo.value, "error_envelope", None)
        assert envelope is not None, "reader must stamp the MET-385 envelope on the exception"
        assert envelope["code"] == ErrorCode.NOT_FOUND.value
        assert envelope["details"]["uri"] == uri
        assert envelope["details"]["source_path"] == "uat://missing.md"
        assert envelope["retryable"] is False
