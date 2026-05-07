"""Unit tests for ``KnowledgeService.list_sources`` (MET-415, L1-A8).

Mirrors the offline-mock pattern in ``test_knowledge_reranker.py``: we
fake out LightRAG's ``chunks_vdb`` storage with a tiny stub so the
service code path runs end-to-end without LightRAG, sentence-transformers,
or Postgres.

Two sets of cases:

* **In-memory path** — exercises ``_list_sources_in_memory`` against a
  ``client_storage`` dict shaped like NanoVectorDBStorage. Covers the
  filtering, grouping, ordering, and pagination contract.
* **Postgres path** — patches ``asyncpg.connect`` so the GROUP BY SQL
  is observed (workspace, project_id, kt, limit, offset) without
  needing a live PG.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from digital_twin.knowledge.service import SourceSummary
from digital_twin.knowledge.types import KnowledgeType

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _encoded_file_path(
    *,
    source_path: str,
    chunk_index: int,
    total_chunks: int,
    knowledge_type: KnowledgeType,
    project_id: str = "default",
    extra: dict[str, Any] | None = None,
) -> str:
    """Build the JSON metadata blob the LightRAG adapter writes to ``file_path``.

    Mirrors ``_encode_meta`` in ``lightrag_service.py`` so the fake row
    round-trips through ``_decode_meta``.
    """
    payload: dict[str, Any] = {
        "ver": "v1",
        "src": source_path,
        "ci": chunk_index,
        "tc": total_chunks,
        "h": None,
        "kt": str(knowledge_type),
        "wp": None,
        "x": {"project_id": project_id, **(extra or {})},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _stub_chunk(
    *,
    chunk_id: str,
    source_path: str,
    chunk_index: int,
    total_chunks: int,
    knowledge_type: KnowledgeType,
    indexed_at: datetime,
    project_id: str = "default",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a NanoVectorDB-shaped chunk row for the in-memory fallback path."""
    return {
        "id": chunk_id,
        "content": f"chunk {chunk_index} of {source_path}",
        "file_path": _encoded_file_path(
            source_path=source_path,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            knowledge_type=knowledge_type,
            project_id=project_id,
            extra=extra,
        ),
        "create_time": indexed_at,
    }


def _make_service(chunks: list[dict[str, Any]]) -> LightRAGKnowledgeService:
    """Build a service with a stubbed ``chunks_vdb.client_storage``.

    The in-memory path reads ``client_storage["data"]`` — same shape
    NanoVectorDBStorage exposes — so we can drive ``list_sources``
    without LightRAG, sentence-transformers, or Postgres.
    """
    svc = LightRAGKnowledgeService(working_dir="/tmp/uat-list-sources")
    svc._initialized = True  # bypass real initialize()
    fake_rag = MagicMock()
    fake_rag.chunks_vdb = MagicMock()
    fake_rag.chunks_vdb.client_storage = {"data": chunks}
    svc._rag = fake_rag
    return svc


# Shared fixture: 3 sources, mixed types, distinct ingest times.
# ``alpha.md`` is a multi-chunk DESIGN_DECISION (3 fragments).
# ``bravo.csv`` is a single-chunk COMPONENT.
# ``charlie.md`` is a single-chunk FAILURE (most recent — should sort first).
NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _three_source_fixture(*, project_id: str = "default") -> list[dict[str, Any]]:
    return [
        _stub_chunk(
            chunk_id="alpha-0",
            source_path="alpha.md",
            chunk_index=0,
            total_chunks=3,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            indexed_at=NOW - timedelta(hours=2),
            project_id=project_id,
            extra={"author": "mech"},
        ),
        _stub_chunk(
            chunk_id="alpha-1",
            source_path="alpha.md",
            chunk_index=1,
            total_chunks=3,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            indexed_at=NOW - timedelta(hours=2),
            project_id=project_id,
            extra={"author": "mech"},
        ),
        _stub_chunk(
            chunk_id="alpha-2",
            source_path="alpha.md",
            chunk_index=2,
            total_chunks=3,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
            indexed_at=NOW - timedelta(hours=2),
            project_id=project_id,
            extra={"author": "mech"},
        ),
        _stub_chunk(
            chunk_id="bravo-0",
            source_path="bravo.csv",
            chunk_index=0,
            total_chunks=1,
            knowledge_type=KnowledgeType.COMPONENT,
            indexed_at=NOW - timedelta(hours=1),
            project_id=project_id,
        ),
        _stub_chunk(
            chunk_id="charlie-0",
            source_path="charlie.md",
            chunk_index=0,
            total_chunks=1,
            knowledge_type=KnowledgeType.FAILURE,
            indexed_at=NOW,
            project_id=project_id,
        ),
    ]


# ---------------------------------------------------------------------------
# In-memory fallback contract (no postgres_dsn)
# ---------------------------------------------------------------------------


class TestListSourcesInMemoryContract:
    """Exercises the public ``list_sources`` API via the in-memory path."""

    @pytest.mark.asyncio
    async def test_list_sources_returns_one_row_per_source(self) -> None:
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources()

        assert len(rows) == 3
        assert all(isinstance(r, SourceSummary) for r in rows)
        paths = {r.source_path for r in rows}
        assert paths == {"alpha.md", "bravo.csv", "charlie.md"}

    @pytest.mark.asyncio
    async def test_list_sources_filter_by_type(self) -> None:
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources(knowledge_type=KnowledgeType.COMPONENT)

        assert len(rows) == 1
        assert rows[0].source_path == "bravo.csv"
        assert rows[0].knowledge_type == KnowledgeType.COMPONENT

    @pytest.mark.asyncio
    async def test_list_sources_fragment_count_matches(self) -> None:
        """alpha.md has 3 chunks — the row's fragment_count must be 3."""
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources()

        by_path = {r.source_path: r for r in rows}
        assert by_path["alpha.md"].fragment_count == 3
        assert by_path["bravo.csv"].fragment_count == 1
        assert by_path["charlie.md"].fragment_count == 1

    @pytest.mark.asyncio
    async def test_list_sources_orders_by_indexed_at_desc(self) -> None:
        """Most-recently ingested source must come first."""
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources()

        # charlie.md (NOW) > bravo.csv (NOW-1h) > alpha.md (NOW-2h)
        assert [r.source_path for r in rows] == ["charlie.md", "bravo.csv", "alpha.md"]

    @pytest.mark.asyncio
    async def test_list_sources_pagination(self) -> None:
        """``limit=2, offset=1`` returns the second + third entries."""
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources(limit=2, offset=1)

        # Full order is [charlie, bravo, alpha]; offset=1 skips charlie,
        # limit=2 returns [bravo, alpha].
        assert [r.source_path for r in rows] == ["bravo.csv", "alpha.md"]

    @pytest.mark.asyncio
    async def test_source_summary_round_trips_metadata(self) -> None:
        """Custom metadata fields ingested into ``x`` must come back out."""
        svc = _make_service(_three_source_fixture())

        rows = await svc.list_sources(knowledge_type=KnowledgeType.DESIGN_DECISION)

        assert len(rows) == 1
        assert rows[0].source_path == "alpha.md"
        assert rows[0].metadata.get("author") == "mech"

    @pytest.mark.asyncio
    async def test_list_sources_default_tenant_scope_when_project_id_none(self) -> None:
        """``project_id is None`` must scope to ``project_id == "default"``,
        not return rows across every tenant. Pinned in L1-A1.
        """
        chunks: list[dict[str, Any]] = []
        chunks.extend(_three_source_fixture(project_id="default"))
        chunks.append(
            _stub_chunk(
                chunk_id="other-0",
                source_path="other-tenant.md",
                chunk_index=0,
                total_chunks=1,
                knowledge_type=KnowledgeType.SESSION,
                indexed_at=NOW + timedelta(hours=1),
                project_id="11111111-1111-4111-8111-111111111111",
            )
        )
        svc = _make_service(chunks)

        rows = await svc.list_sources()

        # Even though "other-tenant.md" is the most recent ingest, it
        # must NOT appear under an unscoped (default) listing.
        assert all(r.source_path != "other-tenant.md" for r in rows)
        assert {r.source_path for r in rows} == {"alpha.md", "bravo.csv", "charlie.md"}

    @pytest.mark.asyncio
    async def test_list_sources_explicit_project_id_scopes_correctly(self) -> None:
        """An explicit ``project_id`` filter must surface only that tenant's rows."""
        other_uuid = UUID("11111111-1111-4111-8111-111111111111")
        chunks: list[dict[str, Any]] = []
        chunks.extend(_three_source_fixture(project_id="default"))
        chunks.append(
            _stub_chunk(
                chunk_id="other-0",
                source_path="other-tenant.md",
                chunk_index=0,
                total_chunks=1,
                knowledge_type=KnowledgeType.SESSION,
                indexed_at=NOW,
                project_id=str(other_uuid),
            )
        )
        svc = _make_service(chunks)

        rows = await svc.list_sources(project_id=other_uuid)

        assert len(rows) == 1
        assert rows[0].source_path == "other-tenant.md"


# ---------------------------------------------------------------------------
# Postgres path: SQL shape + parameter binding
# ---------------------------------------------------------------------------


class _FakeAsyncpgConn:
    """Stand-in ``asyncpg.Connection`` for the PG-path tests.

    Captures the SQL string + bound params so the test can assert the
    GROUP BY shape, the workspace / project filter, and pagination
    bindings without spinning up a real Postgres.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, params))
        return self._rows

    async def close(self) -> None:
        self.closed = True


class TestListSourcesPostgresPath:
    """Verifies the SQL the PG path issues — without a live Postgres."""

    def _service_with_pg(self) -> tuple[LightRAGKnowledgeService, MagicMock]:
        svc = LightRAGKnowledgeService(
            working_dir="/tmp/uat-list-sources-pg",
            postgres_dsn="postgresql://x:y@localhost:5432/z",
            namespace_prefix="lightrag_test",
        )
        svc._initialized = True
        fake_rag = MagicMock()
        fake_rag.chunks_vdb = MagicMock()
        fake_rag.chunks_vdb.table_name = "lightrag_vdb_chunks"
        fake_rag.chunks_vdb.workspace = "lightrag_test"
        # No client_storage attribute — PG path must NOT touch it.
        svc._rag = fake_rag
        return svc, fake_rag

    @pytest.mark.asyncio
    async def test_pg_path_issues_group_by_with_default_scope(self) -> None:
        svc, _rag = self._service_with_pg()
        fake_rows = [
            {
                "source_path": "alpha.md",
                "knowledge_type": "design_decision",
                "fragment_count": 3,
                "indexed_at": NOW,
                "metadata": json.dumps({"project_id": "default", "author": "mech"}),
            }
        ]
        fake_conn = _FakeAsyncpgConn(fake_rows)
        with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)) as mock_conn:
            rows = await svc.list_sources()

        # Connection acquired exactly once, against the configured DSN.
        mock_conn.assert_awaited_once_with("postgresql://x:y@localhost:5432/z")
        assert fake_conn.closed is True

        # SQL shape: GROUP BY, workspace param, default-tenant scope, LIMIT/OFFSET.
        assert len(fake_conn.fetch_calls) == 1
        sql, params = fake_conn.fetch_calls[0]
        assert "GROUP BY source_path, knowledge_type" in sql
        assert "ORDER BY indexed_at DESC" in sql
        assert "LIMIT" in sql and "OFFSET" in sql
        assert "lightrag_vdb_chunks" in sql

        # Param order: (workspace, project_scope, limit, offset).
        assert params == ("lightrag_test", "default", 100, 0)

        # Row → SourceSummary projection.
        assert len(rows) == 1
        assert rows[0].source_path == "alpha.md"
        assert rows[0].knowledge_type == KnowledgeType.DESIGN_DECISION
        assert rows[0].fragment_count == 3
        assert rows[0].indexed_at == NOW
        assert rows[0].metadata == {"project_id": "default", "author": "mech"}

    @pytest.mark.asyncio
    async def test_pg_path_threads_knowledge_type_filter(self) -> None:
        svc, _rag = self._service_with_pg()
        fake_conn = _FakeAsyncpgConn([])
        with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)):
            await svc.list_sources(knowledge_type=KnowledgeType.COMPONENT)

        sql, params = fake_conn.fetch_calls[0]
        # When kt is set the SQL has an extra clause AND $3 binds it.
        assert "kt" in sql
        # Param order with kt: (workspace, project_scope, kt, limit, offset).
        assert params == ("lightrag_test", "default", "component", 100, 0)

    @pytest.mark.asyncio
    async def test_pg_path_threads_explicit_project_id(self) -> None:
        svc, _rag = self._service_with_pg()
        project = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        fake_conn = _FakeAsyncpgConn([])
        with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)):
            await svc.list_sources(project_id=project, limit=10, offset=5)

        sql, params = fake_conn.fetch_calls[0]
        assert "lightrag_vdb_chunks" in sql
        assert params == ("lightrag_test", str(project), 10, 5)
