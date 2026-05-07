"""Knowledge MCP tool error envelope tests (MET-385, L1-B4).

Pins the request-decode-time validation on ``knowledge_type`` for both
``knowledge.ingest`` and ``knowledge.search``. The contract is:

* Malformed values raise ``McpToolError`` with
  ``code="invalid_input"``, a message that lists the allowed enum
  members, and a ``data`` payload carrying ``field`` / ``value`` /
  ``allowed``.
* Empty strings are rejected with the same envelope (the legacy
  silent-coerce-to-``None`` behaviour is gone — see ``_coerce_knowledge_type``).
* Valid enum values pass through unchanged (regression).
* On the search side, an explicit ``None`` / missing key remains a
  no-filter — the validation only fires when the caller supplies a
  value it expects to mean something.
* After an INVALID_INPUT error the adapter remains responsive — the
  exception path doesn't poison handler state.

The fake ``KnowledgeService`` mirrors ``tests/unit/test_knowledge_mcp_adapter.py``
so the tests stay self-contained and don't depend on a real backend.
"""

from __future__ import annotations

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


class _FakeService:
    """Minimal ``KnowledgeService`` stand-in.

    Records ingest / search calls so the regression tests can assert
    that valid payloads still reach the service unchanged after the
    new validation layer landed.
    """

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.ingest_calls: list[dict[str, Any]] = []

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
        self.ingest_calls.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": metadata,
                "project_id": project_id,
                "actor_id": actor_id,
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
        return []

    async def delete_by_source(self, source_path: str) -> int:
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


@pytest.fixture
def server() -> KnowledgeServer:
    return KnowledgeServer(service=_FakeService())  # type: ignore[arg-type]


# Canonical, sorted list of enum values — pinned independently of the
# adapter's private constant so a drift between the two surfaces here.
_EXPECTED_ALLOWED = sorted(kt.value for kt in KnowledgeType)


# ---------------------------------------------------------------------------
# Malformed knowledge_type — the core contract
# ---------------------------------------------------------------------------


class TestMalformedKnowledgeType:
    async def test_ingest_rejects_malformed_knowledge_type(self, server: KnowledgeServer) -> None:
        with pytest.raises(McpToolError) as exc_info:
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "not_a_real_type",
                }
            )

        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.code == "invalid_input"  # StrEnum equality
        # Message must mention each allowed member so the human / agent
        # can self-correct without round-tripping through docs.
        for value in _EXPECTED_ALLOWED:
            assert value in err.message
        assert err.data["field"] == "knowledge_type"
        assert err.data["value"] == "not_a_real_type"
        assert isinstance(err.data["allowed"], list)
        assert all(isinstance(v, str) for v in err.data["allowed"])
        assert err.data["allowed"] == _EXPECTED_ALLOWED

    async def test_search_rejects_malformed_knowledge_type(self, server: KnowledgeServer) -> None:
        with pytest.raises(McpToolError) as exc_info:
            await server.handle_search({"query": "anything", "knowledge_type": "not_a_real_type"})

        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        for value in _EXPECTED_ALLOWED:
            assert value in err.message
        assert err.data["field"] == "knowledge_type"
        assert err.data["value"] == "not_a_real_type"
        assert err.data["allowed"] == _EXPECTED_ALLOWED

    async def test_ingest_rejects_empty_knowledge_type(self, server: KnowledgeServer) -> None:
        # The legacy ``_coerce_knowledge_type`` treated "" as "no
        # filter" silently. After L1-B4 the empty string is a hard
        # rejection — required fields cannot be sneakily bypassed.
        with pytest.raises(McpToolError) as exc_info:
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "",
                }
            )

        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.data["field"] == "knowledge_type"
        # Empty-string values round-trip as "" so callers can grep the
        # log line and find the offending request.
        assert err.data["value"] == ""
        assert err.data["allowed"] == _EXPECTED_ALLOWED

    async def test_search_rejects_empty_knowledge_type(self, server: KnowledgeServer) -> None:
        with pytest.raises(McpToolError) as exc_info:
            await server.handle_search({"query": "anything", "knowledge_type": ""})

        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.data["field"] == "knowledge_type"
        assert err.data["value"] == ""


# ---------------------------------------------------------------------------
# Regression — valid + optional pass-through
# ---------------------------------------------------------------------------


class TestValidKnowledgeTypeRegression:
    @pytest.mark.parametrize(
        "knowledge_type_value,expected_enum",
        [
            ("design_decision", KnowledgeType.DESIGN_DECISION),
            ("component", KnowledgeType.COMPONENT),
            ("failure", KnowledgeType.FAILURE),
            ("constraint", KnowledgeType.CONSTRAINT),
            ("session", KnowledgeType.SESSION),
        ],
    )
    async def test_ingest_accepts_valid_knowledge_type(
        self,
        server: KnowledgeServer,
        knowledge_type_value: str,
        expected_enum: KnowledgeType,
    ) -> None:
        result = await server.handle_ingest(
            {
                "content": "body",
                "source_path": "/x.md",
                "knowledge_type": knowledge_type_value,
            }
        )
        assert result["chunks_indexed"] == 1
        service = server.service  # type: ignore[attr-defined]
        call = service.ingest_calls[-1]  # type: ignore[attr-defined]
        assert call["knowledge_type"] == expected_enum

    async def test_search_accepts_none_knowledge_type(self, server: KnowledgeServer) -> None:
        # Optional-filter contract — search with ``None`` (or missing
        # key) must fall through to the service as ``None`` so the
        # caller gets unfiltered results, NOT a validation error.
        await server.handle_search({"query": "anything", "knowledge_type": None})
        await server.handle_search({"query": "anything"})

        service = server.service  # type: ignore[attr-defined]
        assert len(service.search_calls) == 2  # type: ignore[attr-defined]
        for call in service.search_calls:  # type: ignore[attr-defined]
            assert call["knowledge_type"] is None


# ---------------------------------------------------------------------------
# State recovery — adapter stays responsive after INVALID_INPUT
# ---------------------------------------------------------------------------


class TestInvalidInputDoesNotPoisonState:
    async def test_subsequent_call_after_invalid_input_succeeds(
        self, server: KnowledgeServer
    ) -> None:
        # Step 1: bad request — adapter rejects with the envelope.
        with pytest.raises(McpToolError):
            await server.handle_ingest(
                {
                    "content": "probe",
                    "source_path": "uat://kb/ing/010-bad-type",
                    "knowledge_type": "not_a_real_type",
                }
            )

        # Step 2: a well-formed call must still flow end-to-end and
        # land on the underlying service. Mirrors the KB-ING-010 UAT
        # recovery probe in ``docs/uat/kb-test-plan.md``.
        result = await server.handle_ingest(
            {
                "content": "ok",
                "source_path": "uat://kb/ing/010-after",
                "knowledge_type": "design_decision",
            }
        )
        assert result["chunks_indexed"] == 1
        assert result["source_path"] == "uat://kb/ing/010-after"

        service = server.service  # type: ignore[attr-defined]
        # Only the recovery call reached the service — the rejected one
        # short-circuited at the adapter boundary.
        assert len(service.ingest_calls) == 1  # type: ignore[attr-defined]
        assert (
            service.ingest_calls[0]["source_path"]  # type: ignore[attr-defined]
            == "uat://kb/ing/010-after"
        )

        # And search recovers symmetrically — the rejection in one
        # handler must not bleed across to the other.
        with pytest.raises(McpToolError):
            await server.handle_search({"query": "anything", "knowledge_type": "not_a_real_type"})
        await server.handle_search({"query": "anything"})
        assert len(service.search_calls) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Envelope wire-shape — the raised exception round-trips to the MET-385
# Pydantic envelope so transports can serialise it without ad-hoc glue.
# ---------------------------------------------------------------------------


class TestEnvelopeWireShape:
    async def test_exception_carries_canonical_envelope(self, server: KnowledgeServer) -> None:
        with pytest.raises(McpToolError) as exc_info:
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "garbage",
                }
            )

        envelope = exc_info.value.envelope
        # ``invalid_input`` is non-retryable by default — pin that so
        # transports don't have to re-derive it.
        assert envelope.code == ErrorCode.INVALID_INPUT
        assert envelope.retryable is False
        assert envelope.details is not None
        assert envelope.details["field"] == "knowledge_type"
        assert envelope.details["value"] == "garbage"

    async def test_exception_is_value_error_for_legacy_callers(
        self, server: KnowledgeServer
    ) -> None:
        # Existing handler tests assert ``pytest.raises(ValueError, ...)``
        # against the malformed-type message. The new exception
        # subclasses ``ValueError`` so those callers keep working
        # transparently.
        with pytest.raises(ValueError, match="knowledge_type"):
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "not-a-type",
                }
            )
