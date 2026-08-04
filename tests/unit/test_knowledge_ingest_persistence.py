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
    assert await svc._count_persisted_chunks(_SOURCE) is None
