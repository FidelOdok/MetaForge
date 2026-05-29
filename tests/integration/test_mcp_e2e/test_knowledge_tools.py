"""Phase 3 — knowledge.* MCP tools happy-path coverage (MET-477).

One canonical call per tool against an in-process MCP HTTP app wired
to a tiny ``_FakeKnowledgeService``. The fake implements just enough
of the ``KnowledgeService`` protocol to drive ``handle_search``,
``handle_ingest``, ``handle_extract``, and ``handle_populate_bom``
through the MCP envelope.

Live mode (``METAFORGE_MCP_URL`` set) re-uses the module-level live
client from ``conftest.mcp_client``; the in-process fixture below is
skipped automatically. Live mode is where the G4 search-fallback
behaviour gets exercised against the populated KB.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from digital_twin.knowledge.property_extractor import (
    ExtractedProperty,
    ExtractionMethod,
)
from digital_twin.knowledge.service import (
    ExtractedProperties,
    IngestResult,
    KnowledgeType,
    SearchHit,
)

from ._helpers import call_tool, rpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fake KnowledgeService — minimal protocol surface, deterministic responses.
# ---------------------------------------------------------------------------


class _FakeKnowledgeService:
    """Deterministic stand-in driving the four MCP knowledge tools.

    Records every call so tests can assert on the args the adapter
    forwarded after request-decode (``knowledge_type`` validation,
    filter normalisation, project_id stamping).
    """

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.ingest_calls: list[dict[str, Any]] = []
        self.extract_calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        rerank: bool = False,
        actor_id: str | None = None,
        include_historical: bool = False,
        hybrid: bool = False,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_type": knowledge_type,
                "filters": filters,
                "project_id": project_id,
                "actor_id": actor_id,
                "include_historical": include_historical,
            }
        )
        # Deterministic two-hit response — covers citation round-trip.
        return [
            SearchHit(
                content=f"Result 1 for {query}: STM32H7 runs at 480 MHz max core clock.",
                similarity_score=0.92,
                source_path="datasheets/stm32h7.txt",
                heading="Clock tree",
                chunk_index=0,
                total_chunks=4,
                metadata={"mpn": "STM32H743"},
                knowledge_type=KnowledgeType.COMPONENT,
                source_work_product_id=None,
            ),
            SearchHit(
                content=f"Result 2 for {query}: STM32H7 supports up to 2MB Flash.",
                similarity_score=0.81,
                source_path="datasheets/stm32h7.txt",
                heading="Memory map",
                chunk_index=1,
                total_chunks=4,
                metadata={"mpn": "STM32H743"},
                knowledge_type=KnowledgeType.COMPONENT,
                source_work_product_id=None,
            ),
        ]

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
                "content_len": len(content),
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "project_id": project_id,
                "actor_id": actor_id,
            }
        )
        # Fake one chunk per ~500 chars; minimum 1.
        chunks = max(1, len(content) // 500)
        return IngestResult(
            entry_ids=[],
            chunks_indexed=chunks,
            source_path=source_path,
        )

    async def extract_properties(
        self,
        mpn: str,
        properties: list[str],
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> ExtractedProperties:
        self.extract_calls.append({"mpn": mpn, "properties": list(properties), "aliases": aliases})
        items = [
            ExtractedProperty(
                property_name=name,
                value="3.3" if "voltage" in name else "85",
                unit="V" if "voltage" in name else "C",
                confidence=0.75,
                extraction_method=ExtractionMethod.LLM_INFERRED,
            )
            for name in properties
        ]
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=True,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path="datasheets/fake.pdf",
            items=items,
        )

    async def delete_by_source(self, source_path: str, project_id: UUID | None = None) -> int:
        return 0

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        return []

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


# ---------------------------------------------------------------------------
# Fixture: knowledge-aware MCP client. Live mode reuses METAFORGE_MCP_URL.
# ---------------------------------------------------------------------------


@pytest.fixture
async def knowledge_service() -> _FakeKnowledgeService:
    return _FakeKnowledgeService()


@pytest.fixture
async def knowledge_mcp_client(
    knowledge_service: _FakeKnowledgeService,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an MCP HTTP client wired to ``_FakeKnowledgeService``.

    Mirrors ``conftest.mcp_client`` but injects ``knowledge_service``
    into ``build_unified_server`` so the four knowledge.* tools are
    registered and routed.
    """
    live_url = os.environ.get("METAFORGE_MCP_URL") or None
    if live_url:
        async with httpx.AsyncClient(base_url=live_url, timeout=60.0) as client:
            yield client
        return

    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    embedder = create_embedding_service("local")
    memory_client = MemoryClient(store=InMemoryExperienceStore(), embeddings=embedder)

    server = await build_unified_server(
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=None,
        project_backend=None,
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    app = build_http_app(server, enable_sse=False, api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


async def test_knowledge_search_returns_hits_with_citations(
    knowledge_mcp_client: httpx.AsyncClient,
    knowledge_service: _FakeKnowledgeService,
) -> None:
    """knowledge.search returns hits with source_path / heading / chunk_index."""
    envelope = await call_tool(
        knowledge_mcp_client,
        "knowledge.search",
        {"query": "STM32H7 clock speed", "top_k": 3},
    )

    assert envelope["status"] == "success"
    assert envelope["tool_id"] == "knowledge.search"
    payload = envelope["data"]
    assert "hits" in payload, f"missing hits key: {payload}"
    hits = payload["hits"]
    assert isinstance(hits, list) and len(hits) == 2

    # Citation round-trip: every hit carries the four citation fields.
    for hit in hits:
        assert "content" in hit
        assert "similarity_score" in hit
        assert "source_path" in hit
        assert "heading" in hit
        assert "chunk_index" in hit

    # First hit is the highest-scoring (deterministic ordering preserved).
    assert hits[0]["similarity_score"] >= hits[1]["similarity_score"]
    assert hits[0]["source_path"] == "datasheets/stm32h7.txt"

    # Adapter forwarded top_k and didn't fabricate a knowledge_type filter.
    assert knowledge_service.search_calls[0]["query"] == "STM32H7 clock speed"
    assert knowledge_service.search_calls[0]["top_k"] == 3
    assert knowledge_service.search_calls[0]["knowledge_type"] is None


async def test_knowledge_search_with_knowledge_type_filter(
    knowledge_mcp_client: httpx.AsyncClient,
    knowledge_service: _FakeKnowledgeService,
) -> None:
    """knowledge_type enum is validated + forwarded as the typed enum."""
    await call_tool(
        knowledge_mcp_client,
        "knowledge.search",
        {"query": "regulator", "knowledge_type": "component"},
    )
    assert knowledge_service.search_calls[-1]["knowledge_type"] is KnowledgeType.COMPONENT


async def test_knowledge_ingest_single_payload(
    knowledge_mcp_client: httpx.AsyncClient,
    knowledge_service: _FakeKnowledgeService,
) -> None:
    """knowledge.ingest single-payload mode returns chunks_indexed."""
    envelope = await call_tool(
        knowledge_mcp_client,
        "knowledge.ingest",
        {
            "content": "BME280 component. Operating voltage 1.71-3.6 V. " * 60,
            "source_path": "datasheets/bme280.txt",
            "knowledge_type": "component",
        },
    )

    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert payload.get("source_path") == "datasheets/bme280.txt"
    assert payload.get("chunks_indexed", 0) >= 1

    forwarded = knowledge_service.ingest_calls[0]
    assert forwarded["source_path"] == "datasheets/bme280.txt"
    assert forwarded["knowledge_type"] is KnowledgeType.COMPONENT


async def test_knowledge_extract_returns_properties_envelope(
    knowledge_mcp_client: httpx.AsyncClient,
    knowledge_service: _FakeKnowledgeService,
) -> None:
    """knowledge.extract returns the documented ExtractedProperties envelope."""
    envelope = await call_tool(
        knowledge_mcp_client,
        "knowledge.extract",
        {
            "mpn": "BME280",
            "properties": ["supply_voltage", "operating_temperature_max"],
        },
    )

    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert payload["mpn"] == "BME280"
    assert payload["mpn_found"] is True
    assert payload["datasheet_source_path"] == "datasheets/fake.pdf"

    props = payload["properties"]
    assert set(props.keys()) == {"supply_voltage", "operating_temperature_max"}
    voltage = props["supply_voltage"]
    assert voltage["value"] == "3.3"
    assert voltage["unit"] == "V"
    assert voltage["extraction_method"] == "llm_inferred"
    assert 0.0 <= voltage["confidence"] <= 1.0

    # Adapter forwarded the property list in input order.
    assert knowledge_service.extract_calls[0]["properties"] == [
        "supply_voltage",
        "operating_temperature_max",
    ]


async def test_knowledge_extract_requires_mpn_and_properties(
    knowledge_mcp_client: httpx.AsyncClient,
) -> None:
    """Missing required args yields a clean JSON-RPC error envelope."""
    from ._helpers import McpRpcError

    with pytest.raises(McpRpcError):
        await call_tool(
            knowledge_mcp_client,
            "knowledge.extract",
            {"properties": ["supply_voltage"]},  # missing mpn
        )

    with pytest.raises(McpRpcError):
        await call_tool(
            knowledge_mcp_client,
            "knowledge.extract",
            {"mpn": "BME280", "properties": []},  # empty list
        )


async def test_knowledge_populate_bom_ranks_candidates(
    knowledge_mcp_client: httpx.AsyncClient,
    knowledge_service: _FakeKnowledgeService,
) -> None:
    """knowledge.populate_bom returns ranked suggestions from search + extract."""
    envelope = await call_tool(
        knowledge_mcp_client,
        "knowledge.populate_bom",
        {
            "search_query": "low-power MCU for sensor",
            "constraints": [
                {"property": "supply_voltage", "op": "<=", "value": 3.6},
            ],
            "top_k": 3,
        },
    )

    assert envelope["status"] == "success", envelope
    payload = envelope["data"]
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)
    assert payload["candidates_evaluated"] >= 1
    # The fake's hits carry mpn=STM32H743 → one candidate after dedup.
    assert any(s.get("mpn") == "STM32H743" for s in payload["suggestions"])


async def test_knowledge_tools_appear_in_tools_list(
    knowledge_mcp_client: httpx.AsyncClient,
) -> None:
    """All four knowledge.* tools register when knowledge_service is wired."""
    result = await rpc(knowledge_mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    expected = {
        "knowledge.search",
        "knowledge.ingest",
        "knowledge.extract",
        "knowledge.populate_bom",
    }
    missing = expected - tool_ids
    assert not missing, f"missing knowledge tools in tools/list: {missing}"
