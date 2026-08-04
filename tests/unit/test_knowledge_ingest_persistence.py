"""Unit tests for the post-ingest persistence read-back (hotfix).

``LightRAGKnowledgeService.ingest`` used to report
``chunks_indexed=len(chunks)`` straight from the submitted chunk list,
without confirming the chunks actually landed in the vector store.
LightRAG's ``ainsert`` can swallow an embedding / KG-extraction failure
and return normally, so a silent write failure was indistinguishable
from success — the tool answered "N chunks indexed" while the store held
nothing and every later ``search`` came back empty.

These tests fake LightRAG's ``chunks_vdb`` with a tiny recording stub
(same ``client_storage`` shape ``test_knowledge_service_list.py`` uses)
so the ingest path runs end-to-end without LightRAG, sentence-transformers,
or Postgres:

* a persisting ``ainsert`` -> ingest reports the chunk count as before;
* a no-op ("silent failure") ``ainsert`` -> ingest raises instead of
  falsely reporting success.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from digital_twin.knowledge.lightrag_service import LightRAGKnowledgeService
from digital_twin.knowledge.types import KnowledgeType

_SOURCE = "project://cfcd6ae8-5b16-4ba1-84d7-b9384ea1cbf3/goal-design-intent"
_CONTENT = "# Monitor Build Demo — Goal\n\nBuild a detailed 27-inch monitor CAD assembly.\n"


class _RecordingRag:
    """Minimal stand-in for a LightRAG instance.

    ``ainsert`` appends rows shaped like NanoVectorDBStorage's
    ``client_storage["data"]`` (``id`` / ``content`` / ``file_path``)
    when ``persist`` is True, and does nothing when False — simulating a
    pipeline that returns normally but never stores the vectors.
    """

    def __init__(self, *, persist: bool) -> None:
        self._persist = persist
        self.chunks_vdb = MagicMock()
        self.chunks_vdb.client_storage = {"data": []}

    async def ainsert(
        self,
        *,
        input: list[str],  # noqa: A002 — match LightRAG's kwarg name
        ids: list[str],
        file_paths: list[str],
    ) -> None:
        if not self._persist:
            return
        data: list[dict[str, Any]] = self.chunks_vdb.client_storage["data"]
        for chunk_id, file_path, text in zip(ids, file_paths, input, strict=True):
            data.append({"id": chunk_id, "content": text, "file_path": file_path})


def _service(rag: _RecordingRag) -> LightRAGKnowledgeService:
    # No ``postgres_dsn`` -> the in-memory read-back path is exercised.
    svc = LightRAGKnowledgeService(working_dir="/tmp/knowledge-persist-test")
    svc._initialized = True  # bypass real initialize()
    svc._rag = rag
    return svc


async def test_ingest_reports_count_when_chunks_persist() -> None:
    svc = _service(_RecordingRag(persist=True))
    result = await svc.ingest(
        content=_CONTENT,
        source_path=_SOURCE,
        knowledge_type=KnowledgeType.DESIGN_DECISION,
    )
    assert result.chunks_indexed >= 1
    assert result.source_path == _SOURCE
    assert len(result.entry_ids) == result.chunks_indexed


async def test_ingest_raises_when_write_silently_fails() -> None:
    rag = _RecordingRag(persist=False)
    svc = _service(rag)
    with pytest.raises(RuntimeError, match="did not"):
        await svc.ingest(
            content=_CONTENT,
            source_path=_SOURCE,
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
    # A phantom ingest must not leave the source registered in the
    # in-process index — a later delete/re-ingest would otherwise think
    # chunks exist for it.
    assert ("default", _SOURCE) not in svc._source_index
    assert ("default", _SOURCE) not in svc._content_sha_index


async def test_count_persisted_chunks_unverifiable_returns_none() -> None:
    # A MagicMock chunks_vdb has no real dict client_storage, so the
    # check reports "unverifiable" (None) and ingest keeps the optimistic
    # count instead of failing a possibly-good write.
    svc = LightRAGKnowledgeService(working_dir="/tmp/knowledge-persist-test")
    svc._initialized = True
    svc._rag = MagicMock()  # chunks_vdb is a MagicMock, not a dict-backed stub
    assert await svc._count_persisted_chunks(["some-chunk-id"], _SOURCE) is None


async def test_read_back_is_id_based_and_immune_to_mangled_rows() -> None:
    """MET-577: the read-back matches on chunk id, never file_path.

    lightrag-hku 1.5.x basenames ``file_path`` on write, destroying the
    encoded metadata. An id-based read-back still counts such rows as
    persisted (they DID land), and garbage rows from other writers can
    neither error the check nor inflate the count.
    """
    rag = _RecordingRag(persist=True)
    svc = _service(rag)
    result = await svc.ingest(
        content=_CONTENT,
        source_path=_SOURCE,
        knowledge_type=KnowledgeType.DESIGN_DECISION,
    )
    data = rag.chunks_vdb.client_storage["data"]
    # Simulate 1.5.x mangling: our row's file_path loses its JSON prefix …
    for chunk in data:
        chunk["file_path"] = str(chunk["file_path"]).rsplit("/", 1)[-1]
    # … and a foreign writer parked an unrelated garbage row alongside.
    data.append({"id": "foreign-row", "content": "x", "file_path": "not json"})
    ids = [c["id"] for c in data if c["id"] != "foreign-row"]
    assert await svc._count_persisted_chunks(ids, _SOURCE) == result.chunks_indexed


async def test_read_back_counts_only_this_ingests_ids() -> None:
    """Stale rows at the same source can't mask a failed write (exactness)."""
    rag = _RecordingRag(persist=True)
    svc = _service(rag)
    # A previous ingest left rows at the same source…
    rag.chunks_vdb.client_storage["data"].append(
        {"id": "stale-chunk-000", "content": "old", "file_path": "{}"}
    )
    # …but THIS write's ids are what the read-back must confirm.
    assert await svc._count_persisted_chunks(["fresh-chunk-000"], _SOURCE) == 0


async def test_nanovectordb_dunder_id_rows_are_matched() -> None:
    """Real NanoVectorDB rows key ids as ``__id__`` — both shapes count."""
    rag = _RecordingRag(persist=True)
    svc = _service(rag)
    rag.chunks_vdb.client_storage["data"].append({"__id__": "dunder-chunk-000", "content": "x"})
    assert await svc._count_persisted_chunks(["dunder-chunk-000"], _SOURCE) == 1


def test_every_jsonb_cast_query_carries_the_guard() -> None:
    """MET-577 wiring guard: any SQL that casts ``file_path::jsonb`` must
    prefilter with ``_JSON_FILE_PATH_GUARD`` so one non-JSON row cannot
    error every search in the workspace."""
    import inspect

    from digital_twin.knowledge import lightrag_service as mod

    src = inspect.getsource(mod)
    for name in ("_search_pg", "_list_sources_pg", "_existing_content_sha256"):
        fn_src = inspect.getsource(getattr(LightRAGKnowledgeService, name))
        if "::jsonb" in fn_src:
            assert "_JSON_FILE_PATH_GUARD" in fn_src, (
                f"{name} casts file_path::jsonb without the non-JSON row guard"
            )
    # The read-back must stay cast-free entirely (id-based).
    readback_src = inspect.getsource(LightRAGKnowledgeService._count_persisted_chunks)
    assert "::jsonb" not in readback_src
    assert "_JSON_FILE_PATH_GUARD" in src
