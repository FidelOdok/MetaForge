"""Compound filter semantics for ``knowledge.search`` (MET-417, L1-B5).

Pins the contract documented in the knowledge-ingestion playbook
(``docs/architecture/knowledge-ingestion-playbook.md#search-filters``)
and exercised by KB-SRC-014 in the UAT plan:

* Filters are **AND across keys, equality match**.
* Unknown keys pass through as literal metadata-key equality — a key
  no chunk carries simply yields zero hits with no exception.
* Allowed value types: ``str`` / ``int`` / ``bool`` / ``None``.
* ``dict`` and ``list`` filter values are rejected at the adapter
  boundary with the MET-385 ``invalid_input`` envelope (the same
  envelope shape that L1-B4 introduced for the ``knowledge_type``
  enum check).
* The exception path does not poison adapter state — the next
  well-formed call still flows through.

The fake ``KnowledgeService`` here mirrors the ``_FakeService``
pattern in ``tests/unit/test_knowledge_mcp_adapter.py`` and
``tests/unit/test_knowledge_tool_errors.py`` and additionally
implements the AND-equality filter semantics in Python so the tests
exercise the contract end-to-end without a real backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.service import IngestResult, SearchHit, SourceSummary
from digital_twin.knowledge.types import KnowledgeType
from mcp_core.errors import ErrorCode
from tool_registry.tools.knowledge.adapter import (
    KnowledgeServer,
    McpToolError,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _Doc:
    """One ingested chunk: content + metadata (the only fields we filter on)."""

    content: str
    metadata: dict[str, Any]


class _FakeService:
    """Minimal ``KnowledgeService`` stand-in with AND-equality filtering.

    Holds an in-memory corpus of ``_Doc``s and replays the
    AND-across-keys equality contract on ``search()`` so the adapter's
    pass-through behaviour can be asserted against deterministic hits.
    Doesn't call any real backend — pgvector, LightRAG, asyncpg are all
    untouched.
    """

    def __init__(self) -> None:
        self.corpus: list[_Doc] = []
        self.search_calls: list[dict[str, Any]] = []

    def add(self, content: str, metadata: dict[str, Any]) -> None:
        self.corpus.append(_Doc(content=content, metadata=metadata))

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        actor_id: str | None = None,
    ) -> IngestResult:
        # The filter tests don't go through ingest — they pre-seed the
        # corpus directly via ``add`` — but the protocol requires this
        # method to exist so the adapter can construct.
        self.corpus.append(_Doc(content=content, metadata=dict(metadata or {})))
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
        actor_id: str | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "filters": filters,
                "project_id": project_id,
                "actor_id": actor_id,
            }
        )
        # Mirror the AND-across-keys, equality-match contract from
        # ``digital_twin.knowledge.lightrag_service._matches_filters``.
        # ``project_id`` is special-cased the same way: an unscoped
        # search defaults to ``"default"`` so legacy chunks survive.
        scope = str(project_id) if project_id is not None else "default"
        effective: dict[str, Any] = dict(filters or {})
        effective.setdefault("project_id", scope)

        hits: list[SearchHit] = []
        for doc in self.corpus:
            if not _doc_matches(doc, effective):
                continue
            hits.append(
                SearchHit(
                    content=doc.content,
                    similarity_score=1.0,
                    source_path=doc.metadata.get("source_path"),
                    heading=None,
                    chunk_index=None,
                    total_chunks=None,
                    metadata=dict(doc.metadata),
                    knowledge_type=None,
                    source_work_product_id=None,
                )
            )
        return hits[:top_k]

    async def delete_by_source(
        self, source_path: str, project_id: UUID | None = None
    ) -> int:
        return 0

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]:
        return []

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


def _doc_matches(doc: _Doc, filters: dict[str, Any]) -> bool:
    """AND-across-keys, equality-match against ``doc.metadata``.

    Mirrors the production semantics in ``_matches_filters``:

    * ``project_id`` falls back to the documented ``"default"`` tenant
      when the chunk pre-dates project isolation.
    * Every other key is a direct metadata lookup — missing keys
      naturally fail the equality check, which is exactly the
      "unknown filter key yields zero hits" contract.
    """
    for key, expected in filters.items():
        if key == "project_id":
            actual = doc.metadata.get("project_id", "default")
            if str(actual) != str(expected):
                return False
        else:
            actual = doc.metadata.get(key)
            if actual != expected:
                return False
    return True


@pytest.fixture
def server() -> tuple[KnowledgeServer, _FakeService]:
    """Server + the underlying fake service, returned together.

    Several tests need to seed the corpus before issuing the search;
    returning the fake alongside saves a ``server.service`` cast that
    Pyright complains about in every callsite.
    """
    fake = _FakeService()
    srv = KnowledgeServer(service=fake)  # type: ignore[arg-type]
    return srv, fake


# ---------------------------------------------------------------------------
# AND semantics — the core contract
# ---------------------------------------------------------------------------


class TestFilterAndSemantics:
    async def test_filters_are_and_across_keys(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, fake = server
        # Three chunks; only the first matches BOTH a=="x" AND b=="y".
        fake.add("doc-xy", {"a": "x", "b": "y"})
        fake.add("doc-xz", {"a": "x", "b": "z"})
        fake.add("doc-qy", {"a": "q", "b": "y"})

        result = await srv.handle_search({"query": "anything", "filters": {"a": "x", "b": "y"}})
        contents = [hit["content"] for hit in result["hits"]]
        assert contents == ["doc-xy"]

    async def test_unknown_filter_key_returns_zero_hits_no_error(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, fake = server
        fake.add("doc-1", {"a": "x"})
        fake.add("doc-2", {"a": "y"})
        # ``banana`` is not a key any chunk carries — pinned contract
        # is: literal metadata equality, missing key fails the match,
        # so we return zero hits without raising.
        result = await srv.handle_search({"query": "anything", "filters": {"banana": "yellow"}})
        assert result["hits"] == []


# ---------------------------------------------------------------------------
# Allowed value types — regressions for the four valid scalar types
# ---------------------------------------------------------------------------


class TestFilterValueTypesAccepted:
    async def test_filter_value_string_accepted(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, fake = server
        fake.add("ti-mcu", {"vendor": "TI"})
        fake.add("st-mcu", {"vendor": "ST"})

        result = await srv.handle_search({"query": "mcu", "filters": {"vendor": "TI"}})
        assert [h["content"] for h in result["hits"]] == ["ti-mcu"]

    async def test_filter_value_int_accepted(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        # CSV chunker stamps integer ``row_index`` values into chunk
        # metadata — the contract must keep ``int`` filterable verbatim.
        srv, fake = server
        fake.add("row-1", {"row_index": 1})
        fake.add("row-2", {"row_index": 2})
        fake.add("row-3", {"row_index": 3})

        result = await srv.handle_search({"query": "row", "filters": {"row_index": 2}})
        assert [h["content"] for h in result["hits"]] == ["row-2"]

        # The underlying call also carries the int through unchanged —
        # no string coercion at the adapter layer.
        assert fake.search_calls[-1]["filters"]["row_index"] == 2
        assert isinstance(fake.search_calls[-1]["filters"]["row_index"], int)

    async def test_filter_value_bool_accepted(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, fake = server
        fake.add("public-doc", {"public": True})
        fake.add("private-doc", {"public": False})

        result = await srv.handle_search({"query": "anything", "filters": {"public": True}})
        assert [h["content"] for h in result["hits"]] == ["public-doc"]
        # Booleans must round-trip as ``bool``, not collapse to ``int``.
        assert fake.search_calls[-1]["filters"]["public"] is True

    async def test_filter_value_none_accepted(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        # ``None`` matches null / missing metadata — it's how callers
        # query for "deprecated is unset" without a sentinel string.
        srv, fake = server
        fake.add("active", {"deprecated": None})
        fake.add("retired", {"deprecated": "2025-01-01"})

        result = await srv.handle_search({"query": "anything", "filters": {"deprecated": None}})
        assert [h["content"] for h in result["hits"]] == ["active"]


# ---------------------------------------------------------------------------
# Rejected value types — dict / list raise the MET-385 envelope
# ---------------------------------------------------------------------------


class TestFilterValueTypesRejected:
    async def test_filter_value_dict_rejected_with_envelope(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, _fake = server
        with pytest.raises(McpToolError) as exc_info:
            await srv.handle_search({"query": "x", "filters": {"nested": {"a": "b"}}})
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.code == "invalid_input"  # StrEnum equality
        # Field path includes the offending key so callers can pinpoint
        # the bad slot in a multi-key payload.
        assert err.data["field"] == "filters.nested"
        assert err.data["value_type"] == "dict"
        assert "str" in err.data["allowed_types"]
        assert "int" in err.data["allowed_types"]
        assert "bool" in err.data["allowed_types"]
        assert "null" in err.data["allowed_types"]
        # And the canonical envelope rides through too.
        envelope = err.envelope
        assert envelope.code == ErrorCode.INVALID_INPUT
        assert envelope.retryable is False
        assert envelope.details is not None
        assert envelope.details["field"] == "filters.nested"

    async def test_filter_value_list_rejected_with_envelope(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, _fake = server
        with pytest.raises(McpToolError) as exc_info:
            await srv.handle_search({"query": "x", "filters": {"tags": ["a", "b"]}})
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.data["field"] == "filters.tags"
        assert err.data["value_type"] == "list"

    async def test_filter_top_level_not_dict_rejected(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        # Defensive: ``filters="banana"`` (caller sent a stringified
        # JSON instead of a real object) must fail at the adapter
        # boundary with the same envelope shape, not crash with an
        # AttributeError deeper down the stack.
        srv, _fake = server
        with pytest.raises(McpToolError) as exc_info:
            await srv.handle_search({"query": "x", "filters": "banana"})
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.data["field"] == "filters"
        assert err.data["value_type"] == "str"


# ---------------------------------------------------------------------------
# State recovery — adapter stays responsive after rejection
# ---------------------------------------------------------------------------


class TestInvalidFilterDoesNotPoisonState:
    async def test_invalid_filter_does_not_poison_state(
        self, server: tuple[KnowledgeServer, _FakeService]
    ) -> None:
        srv, fake = server
        fake.add("doc-1", {"vendor": "TI"})

        # Step 1: bad filter — adapter rejects with the envelope.
        with pytest.raises(McpToolError):
            await srv.handle_search({"query": "x", "filters": {"nested": {"a": "b"}}})

        # Step 2: well-formed call still flows end-to-end. The rejected
        # call must not have reached the service, and the next valid
        # one must.
        assert fake.search_calls == []  # rejection short-circuited
        result = await srv.handle_search({"query": "x", "filters": {"vendor": "TI"}})
        assert [h["content"] for h in result["hits"]] == ["doc-1"]
        assert len(fake.search_calls) == 1
        assert fake.search_calls[0]["filters"] == {"vendor": "TI"}
