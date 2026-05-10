"""Integration tests for streaming progress on knowledge ingest (MET-388, L1-B2).

Verifies the three KB-PRG-001..003 acceptance contracts:

* Multi-file ``knowledge.ingest`` payloads emit one
  ``notifications/progress`` per file with a monotonically advancing
  ``progress`` value (KB-PRG-001).
* The originating tool/call ``request_id`` round-trips through every
  notification (KB-PRG-002).
* ``tools/list`` advertises ``supports_progress=True`` for
  ``knowledge.ingest`` so harnesses can pre-wire a sink (KB-PRG-003).

Backward-compatibility canary: a single-payload ingest (no ``files``
key) emits zero progress events.

Mocks ``KnowledgeService.ingest`` with the same ``_FakeService`` shape
used in ``tests/unit/test_knowledge_mcp_adapter.py`` so no Postgres /
LightRAG / sentence-transformers backend is required.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.service import IngestResult, SourceSummary
from digital_twin.knowledge.types import KnowledgeType
from mcp_core.schemas import ToolProgress
from tool_registry.tools.knowledge.adapter import KnowledgeServer


class _CaptureSink:
    """Async-callable sink that records every ToolProgress event."""

    def __init__(self) -> None:
        self.events: list[ToolProgress] = []

    async def __call__(self, event: ToolProgress) -> None:
        self.events.append(event)


class _FakeService:
    """Fake ``KnowledgeService`` — records every ``ingest`` call.

    Mirrors the structure used in ``test_knowledge_mcp_adapter.py`` so
    the adapter believes it is talking to the real Protocol while we
    sidestep every heavy backend dep (asyncpg, pgvector,
    sentence-transformers, lightrag-hku).
    """

    def __init__(self) -> None:
        self.ingest_calls: list[dict[str, Any]] = []

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
                "source_work_product_id": source_work_product_id,
                "metadata": metadata,
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
    ) -> list[Any]:
        return []

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


def _request(method: str, params: dict[str, Any], request_id: str = "req-1") -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})


@pytest.fixture
def server() -> KnowledgeServer:
    return KnowledgeServer(service=_FakeService())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# KB-PRG-001 — multi-file ingest emits ≥ N progress notifications
# ---------------------------------------------------------------------------


class TestProgressOnMultiFileIngest:
    async def test_knowledge_ingest_emits_progress_per_file(self, server: KnowledgeServer) -> None:
        """Five inline files => five progress events with monotonic ``progress``."""
        sink = _CaptureSink()
        server.set_progress_sink(sink)

        files = [
            {
                "content": f"body {i}",
                "source_path": f"docs/file-{i}.md",
                "knowledge_type": "design_decision",
            }
            for i in range(5)
        ]

        raw = await server.handle_request(
            _request(
                "tool/call",
                {
                    "tool_id": "knowledge.ingest",
                    "arguments": {"files": files, "request_id": "batch-A"},
                },
            )
        )
        body = json.loads(raw)
        assert body["result"]["status"] == "success", body
        assert body["result"]["data"]["files_ingested"] == 5
        assert body["result"]["data"]["chunks_indexed"] == 5

        # ≥ 5 events with monotonically advancing progress in (0, 1].
        assert len(sink.events) == 5
        progresses = [e.progress for e in sink.events]
        assert progresses == sorted(progresses)
        assert all(0.0 < p <= 1.0 for p in progresses)
        assert progresses[-1] == pytest.approx(1.0)
        # Final progress event must arrive before the success response —
        # ``handle_request`` is synchronous wrt awaits inside the
        # handler, so by the time we read ``raw`` every event is in.
        for ev in sink.events:
            assert ev.message.startswith("ingested ")

    async def test_progress_carries_request_id(self, server: KnowledgeServer) -> None:
        """KB-PRG-002: every progress event carries the originating request_id."""
        sink = _CaptureSink()
        server.set_progress_sink(sink)

        files = [
            {
                "content": f"body {i}",
                "source_path": f"docs/file-{i}.md",
                "knowledge_type": "design_decision",
            }
            for i in range(3)
        ]
        await server.handle_request(
            _request(
                "tool/call",
                {
                    "tool_id": "knowledge.ingest",
                    "arguments": {"files": files, "request_id": "batch-B"},
                },
            )
        )
        assert all(e.request_id == "batch-B" for e in sink.events)
        assert len(sink.events) == 3


# ---------------------------------------------------------------------------
# KB-PRG-003 — capability advertised on tools/list
# ---------------------------------------------------------------------------


class TestProgressCapabilityAdvertised:
    async def test_progress_capability_advertised_for_knowledge_ingest(
        self, server: KnowledgeServer
    ) -> None:
        raw = await server.handle_request(_request("tool/list", {}))
        body = json.loads(raw)
        tools = body["result"]["tools"]
        ingest = next(t for t in tools if t["tool_id"] == "knowledge.ingest")
        assert ingest["supports_progress"] is True

    async def test_search_does_not_advertise_progress(self, server: KnowledgeServer) -> None:
        """Sanity check: only the long-running tool advertises the capability."""
        raw = await server.handle_request(_request("tool/list", {}))
        body = json.loads(raw)
        tools = body["result"]["tools"]
        search = next(t for t in tools if t["tool_id"] == "knowledge.search")
        assert search["supports_progress"] is False


# ---------------------------------------------------------------------------
# Backward compatibility — single-file path stays silent
# ---------------------------------------------------------------------------


class TestSingleFileBackwardCompatibility:
    async def test_single_file_ingest_emits_no_progress(self, server: KnowledgeServer) -> None:
        sink = _CaptureSink()
        server.set_progress_sink(sink)

        raw = await server.handle_request(
            _request(
                "tool/call",
                {
                    "tool_id": "knowledge.ingest",
                    "arguments": {
                        "content": "single body",
                        "source_path": "docs/single.md",
                        "knowledge_type": "design_decision",
                    },
                },
            )
        )
        body = json.loads(raw)
        assert body["result"]["status"] == "success"
        # Legacy shape — entry_ids + chunks_indexed + source_path only.
        assert body["result"]["data"]["chunks_indexed"] == 1
        assert "files_ingested" not in body["result"]["data"]
        # No progress events.
        assert sink.events == []

    async def test_empty_batch_emits_no_progress(self, server: KnowledgeServer) -> None:
        """An empty ``files`` list is a no-op, not an error."""
        sink = _CaptureSink()
        server.set_progress_sink(sink)
        raw = await server.handle_request(
            _request(
                "tool/call",
                {"tool_id": "knowledge.ingest", "arguments": {"files": []}},
            )
        )
        body = json.loads(raw)
        assert body["result"]["status"] == "success"
        assert body["result"]["data"] == {
            "files_ingested": 0,
            "chunks_indexed": 0,
            "files": [],
        }
        assert sink.events == []
