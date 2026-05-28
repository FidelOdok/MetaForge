"""Unit tests for the knowledge MCP tool adapter (MET-335).

Verifies:

* The adapter registers ``knowledge.search`` and ``knowledge.ingest``.
* The handlers delegate 1:1 to the underlying ``KnowledgeService`` and
  produce JSON-serialisable output (UUIDs / enums coerced to strings).
* The adapter never imports a concrete backend (no LightRAG / LlamaIndex
  symbols leak in).
* ``set_service`` late-binds a service constructed without one.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.knowledge.service import (
    IngestResult,
    KnowledgeService,
    SearchHit,
    SourceSummary,
)
from digital_twin.knowledge.types import KnowledgeType
from tool_registry.tools.knowledge.adapter import KnowledgeServer


class _FakeService:
    """Records calls so the test can assert exact delegation."""

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.ingest_calls: list[dict[str, Any]] = []
        self.extract_calls: list[dict[str, Any]] = []

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
            entry_ids=[uuid4(), uuid4()],
            chunks_indexed=2,
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
        include_historical: bool = False,
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
        return [
            SearchHit(
                content=f"hit for {query}",
                similarity_score=0.91,
                source_path="docs/decision.md",
                heading="Decision",
                chunk_index=2,
                total_chunks=5,
                metadata={"author": "mech"},
                knowledge_type=KnowledgeType.DESIGN_DECISION,
                source_work_product_id=uuid4(),
            ),
        ]

    async def delete_by_source(self, source_path: str, project_id: UUID | None = None) -> int:
        return 0

    async def list_sources(
        self,
        project_id: UUID | None = None,
        knowledge_type: KnowledgeType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSummary]:
        return []

    # MET-433: test double for extract_properties. Returns a canned
    # ``ExtractedProperties`` so handle_extract tests can assert on
    # the wire shape without going through extract_property_from_tables.
    async def extract_properties(
        self,
        mpn: str,
        properties: list[str],
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> Any:
        from datetime import UTC, datetime

        from digital_twin.knowledge.property_extractor import (
            ExtractedProperty,
            ExtractionMethod,
        )
        from digital_twin.knowledge.service import ExtractedProperties

        self.extract_calls.append({"mpn": mpn, "properties": list(properties), "aliases": aliases})
        items = [
            ExtractedProperty(
                property_name=name,
                value="3.3" if name == "supply_voltage" else None,
                unit="V" if name == "supply_voltage" else None,
                confidence=1.0 if name == "supply_voltage" else 0.0,
                extraction_method=(
                    ExtractionMethod.VERBATIM
                    if name == "supply_voltage"
                    else ExtractionMethod.NOT_FOUND
                ),
                page=12 if name == "supply_voltage" else None,
                heading="Electrical Characteristics" if name == "supply_voltage" else None,
                table_row=4 if name == "supply_voltage" else None,
            )
            for name in properties
        ]
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=True,
            datasheet_revision="rev3",
            datasheet_published_at=datetime(2026, 1, 15, tzinfo=UTC),
            datasheet_source_path="datasheets/test.pdf",
            items=items,
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "fake"}


@pytest.fixture
def server() -> KnowledgeServer:
    return KnowledgeServer(service=_FakeService())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registers_four_tools(self, server: KnowledgeServer) -> None:
        # MET-433: knowledge.extract joins the search + ingest surface.
        # MET-473: knowledge.populate_bom adds Auto-BOM ranking.
        assert set(server.tool_ids) == {
            "knowledge.search",
            "knowledge.ingest",
            "knowledge.extract",
            "knowledge.populate_bom",
        }

    def test_search_manifest_shape(self, server: KnowledgeServer) -> None:
        registration = server._tools["knowledge.search"]  # noqa: SLF001
        manifest = registration.manifest
        assert manifest.adapter_id == "knowledge"
        assert "query" in manifest.input_schema["properties"]
        assert "top_k" in manifest.input_schema["properties"]
        assert manifest.input_schema["required"] == ["query"]

    def test_ingest_manifest_shape(self, server: KnowledgeServer) -> None:
        registration = server._tools["knowledge.ingest"]  # noqa: SLF001
        manifest = registration.manifest
        assert manifest.adapter_id == "knowledge"
        required = manifest.input_schema["required"]
        assert set(required) == {"content", "source_path", "knowledge_type"}


# ---------------------------------------------------------------------------
# Handler delegation
# ---------------------------------------------------------------------------


class TestSearchHandler:
    async def test_delegates_query_top_k_and_returns_serialised_hits(
        self, server: KnowledgeServer
    ) -> None:
        result = await server.handle_search(
            {"query": "titanium bracket", "top_k": 3, "knowledge_type": "design_decision"}
        )
        # Delegation
        service = server.service  # type: ignore[attr-defined]
        assert len(service.search_calls) == 1  # type: ignore[attr-defined]
        call = service.search_calls[0]  # type: ignore[attr-defined]
        assert call["query"] == "titanium bracket"
        assert call["top_k"] == 3
        assert call["knowledge_type"] == KnowledgeType.DESIGN_DECISION

        # Serialisation — UUID / enum should be strings
        assert result["hits"]
        hit = result["hits"][0]
        assert hit["similarity_score"] == 0.91
        assert hit["heading"] == "Decision"
        assert isinstance(hit["source_work_product_id"], str)
        assert hit["knowledge_type"] == "design_decision"

    async def test_missing_query_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="query"):
            await server.handle_search({})


class TestIngestHandler:
    async def test_delegates_and_serialises_uuid_list(self, server: KnowledgeServer) -> None:
        wp = uuid4()
        result = await server.handle_ingest(
            {
                "content": "Decision body",
                "source_path": "/tmp/d.md",
                "knowledge_type": "design_decision",
                "source_work_product_id": str(wp),
                "metadata": {"reviewer": "mech"},
            }
        )
        service = server.service  # type: ignore[attr-defined]
        assert len(service.ingest_calls) == 1  # type: ignore[attr-defined]
        call = service.ingest_calls[0]  # type: ignore[attr-defined]
        assert call["source_path"] == "/tmp/d.md"
        assert call["source_work_product_id"] == wp
        assert call["knowledge_type"] == KnowledgeType.DESIGN_DECISION

        assert result["chunks_indexed"] == 2
        assert result["source_path"] == "/tmp/d.md"
        assert len(result["entry_ids"]) == 2
        assert all(isinstance(eid, str) for eid in result["entry_ids"])

    async def test_missing_required_fields_raise(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="content"):
            await server.handle_ingest({"source_path": "x", "knowledge_type": "session"})
        with pytest.raises(ValueError, match="source_path"):
            await server.handle_ingest({"content": "x", "knowledge_type": "session"})
        with pytest.raises(ValueError, match="knowledge_type"):
            await server.handle_ingest(
                {"content": "x", "source_path": "y", "knowledge_type": "not-a-type"}
            )


# ---------------------------------------------------------------------------
# Late binding
# ---------------------------------------------------------------------------


class TestLateBinding:
    def test_construct_without_service_then_bind(self) -> None:
        server = KnowledgeServer()
        with pytest.raises(RuntimeError, match="set_service"):
            _ = server.service
        server.set_service(_FakeService())  # type: ignore[arg-type]
        assert server.service is not None


# ---------------------------------------------------------------------------
# Independence from any concrete backend
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_adapter_module_imports_no_concrete_backend(self) -> None:
        """The adapter must depend only on the ``KnowledgeService`` Protocol.

        Walks the source AST of ``tool_registry.tools.knowledge.adapter``
        and asserts that no LightRAG / LlamaIndex symbols are imported.
        """
        module = importlib.import_module("tool_registry.tools.knowledge.adapter")
        source = inspect.getsource(module)
        forbidden = ["lightrag", "llama_index", "lightrag_service", "LightRAGKnowledge"]
        offenders = [needle for needle in forbidden if needle in source]
        assert not offenders, f"adapter leaks concrete backend imports: {offenders}"

    def test_satisfies_runtime_checkable_service(self, server: KnowledgeServer) -> None:
        """The fake injected into the server still passes the Protocol check."""
        assert isinstance(server.service, KnowledgeService)


# ---------------------------------------------------------------------------
# MET-401: project_id forwarding from MCP call context
# ---------------------------------------------------------------------------


class TestProjectIdForwarding:
    """The adapter must forward ``current_context().project_id`` to both methods."""

    async def test_search_forwards_project_id_from_context(self, server: KnowledgeServer) -> None:
        from mcp_core.context import McpCallContext, with_context

        project = UUID("11111111-1111-4111-8111-111111111111")
        with with_context(McpCallContext(project_id=project)):
            await server.handle_search({"query": "anything"})

        service = server.service  # type: ignore[attr-defined]
        assert service.search_calls[-1]["project_id"] == project  # type: ignore[attr-defined]

    async def test_ingest_forwards_project_id_from_context(self, server: KnowledgeServer) -> None:
        from mcp_core.context import McpCallContext, with_context

        project = UUID("22222222-2222-4222-8222-222222222222")
        with with_context(McpCallContext(project_id=project)):
            await server.handle_ingest(
                {
                    "content": "body",
                    "source_path": "/x.md",
                    "knowledge_type": "session",
                }
            )

        service = server.service  # type: ignore[attr-defined]
        assert service.ingest_calls[-1]["project_id"] == project  # type: ignore[attr-defined]

    async def test_search_forwards_none_when_no_context_project(
        self, server: KnowledgeServer
    ) -> None:
        # Default sentinel context has project_id=None — adapter must
        # forward that as-is so the service applies its default-tenant
        # fallback (not silently scope to some random project).
        await server.handle_search({"query": "anything"})
        service = server.service  # type: ignore[attr-defined]
        assert service.search_calls[-1]["project_id"] is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# knowledge.extract — MET-433
# ---------------------------------------------------------------------------


class TestExtractHandler:
    async def test_registers_extract_manifest(self, server: KnowledgeServer) -> None:
        registration = server._tools["knowledge.extract"]  # noqa: SLF001
        manifest = registration.manifest
        assert manifest.adapter_id == "knowledge"
        assert manifest.input_schema["required"] == ["mpn", "properties"]
        assert manifest.input_schema["properties"]["properties"]["items"]["type"] == "string"

    async def test_delegates_to_service_and_emits_dict_wire_shape(
        self, server: KnowledgeServer
    ) -> None:
        result = await server.handle_extract(
            {"mpn": "ESP32-WROOM-32", "properties": ["supply_voltage", "missing"]}
        )

        service = server.service  # type: ignore[attr-defined]
        assert len(service.extract_calls) == 1  # type: ignore[attr-defined]
        call = service.extract_calls[0]  # type: ignore[attr-defined]
        assert call["mpn"] == "ESP32-WROOM-32"
        assert call["properties"] == ["supply_voltage", "missing"]
        assert call["aliases"] is None

        # Top-level fields ride along with the per-property dict.
        assert result["mpn"] == "ESP32-WROOM-32"
        assert result["mpn_found"] is True
        assert result["datasheet_revision"] == "rev3"
        assert result["datasheet_published_at"] == "2026-01-15T00:00:00+00:00"
        assert result["datasheet_source_path"] == "datasheets/test.pdf"

        # Properties keyed by name (not a list) — easier for clients
        # to read without index-zip.
        assert set(result["properties"].keys()) == {"supply_voltage", "missing"}
        sv = result["properties"]["supply_voltage"]
        assert sv["value"] == "3.3"
        assert sv["unit"] == "V"
        assert sv["confidence"] == 1.0
        assert sv["extraction_method"] == "verbatim"
        assert sv["page"] == 12
        assert sv["heading"] == "Electrical Characteristics"
        assert sv["table_row"] == 4
        missing = result["properties"]["missing"]
        assert missing["value"] is None
        assert missing["extraction_method"] == "not_found"

    async def test_aliases_forwarded_to_service(self, server: KnowledgeServer) -> None:
        await server.handle_extract(
            {
                "mpn": "X",
                "properties": ["supply_voltage"],
                "aliases": {"supply_voltage": ["VCC", "VDD"]},
            }
        )
        service = server.service  # type: ignore[attr-defined]
        assert service.extract_calls[-1]["aliases"] == {  # type: ignore[attr-defined]
            "supply_voltage": ["VCC", "VDD"]
        }

    async def test_missing_mpn_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="'mpn'"):
            await server.handle_extract({"properties": ["x"]})

    async def test_empty_properties_array_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="'properties'"):
            await server.handle_extract({"mpn": "X", "properties": []})

    async def test_non_string_property_name_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            await server.handle_extract({"mpn": "X", "properties": [42]})

    async def test_malformed_aliases_raises(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="aliases"):
            await server.handle_extract(
                {"mpn": "X", "properties": ["x"], "aliases": ["not-a-dict"]}
            )

    async def test_aliases_value_must_be_list_of_strings(self, server: KnowledgeServer) -> None:
        with pytest.raises(ValueError, match="list of strings"):
            await server.handle_extract(
                {
                    "mpn": "X",
                    "properties": ["x"],
                    "aliases": {"x": "not-a-list"},
                }
            )
