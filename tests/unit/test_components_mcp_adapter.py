"""Unit tests for the component catalog MCP adapter (MET-436)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from digital_twin.catalog.query import CatalogQuery, CatalogQueryResult, ComponentCatalogRow
from digital_twin.knowledge.intent_translator import StubIntentLLM
from tool_registry.tools.components.adapter import ComponentServer


def _row(mpn: str, category: str, cost_usd: float | None = None) -> ComponentCatalogRow:
    return ComponentCatalogRow(
        id=uuid4(),
        mpn=mpn,
        manufacturer="Acme",
        category=category,
        purchase_unit="discrete_part",
        cost_usd=cost_usd,
        lifecycle="active",
        datasheet_url="",
        specs={"v_out": 5.0},
        extraction_meta={},
        schema_version=1,
    )


class _FakeStore:
    """Duck-typed ``ComponentCatalogStore`` double — no live Postgres."""

    def __init__(self) -> None:
        self.queries: list[CatalogQuery] = []
        self._rows_by_category: dict[str, list[ComponentCatalogRow]] = {}

    def stub(self, category: str, rows: list[ComponentCatalogRow]) -> None:
        self._rows_by_category[category] = rows

    async def query(self, catalog_query: CatalogQuery) -> CatalogQueryResult:
        self.queries.append(catalog_query)
        return CatalogQueryResult(
            rows=self._rows_by_category.get(catalog_query.category or "", []), query_time_ms=1.0
        )


class _FakeKnowledgeService:
    """Minimal KnowledgeService double — only used as a fallback dependency,
    not exercised by these adapter-level tests (all searches hit the
    parametric path via ``_FakeStore``)."""

    async def ingest(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def delete_by_source(self, *args: Any, **kwargs: Any) -> int:
        raise NotImplementedError

    async def list_sources(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise NotImplementedError

    async def extract_properties(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


@pytest.fixture
def server(store: _FakeStore) -> ComponentServer:
    return ComponentServer(
        search_store=store,  # type: ignore[arg-type]
        knowledge_service=_FakeKnowledgeService(),  # type: ignore[arg-type]
        llm=StubIntentLLM(),
    )


class TestToolRegistration:
    def test_registers_two_tools(self, server: ComponentServer) -> None:
        assert set(server.tool_ids) == {
            "component.search_parametric",
            "component.search_intent",
        }

    def test_search_parametric_manifest_shape(self, server: ComponentServer) -> None:
        manifest = server._tools["component.search_parametric"].manifest
        assert manifest.adapter_id == "component"
        assert "category" in manifest.input_schema["required"]

    def test_search_intent_manifest_shape(self, server: ComponentServer) -> None:
        manifest = server._tools["component.search_intent"].manifest
        assert manifest.adapter_id == "component"
        assert "intent_text" in manifest.input_schema["required"]


class TestSearchParametric:
    async def test_happy_path(self, server: ComponentServer, store: _FakeStore) -> None:
        store.stub("buck_converter", [_row("MP2459", "buck_converter", cost_usd=0.42)])
        result = await server.handle_search_parametric(
            {
                "category": "buck_converter",
                "filters": [{"property": "v_out", "op": "==", "value": 5}],
            }
        )
        assert result["rows"][0]["mpn"] == "MP2459"
        assert len(store.queries) == 1
        assert store.queries[0].category == "buck_converter"
        assert store.queries[0].filters[0].property == "v_out"

    async def test_missing_category_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="category"):
            await server.handle_search_parametric({})

    async def test_invalid_filter_op_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="op"):
            await server.handle_search_parametric(
                {
                    "category": "buck_converter",
                    "filters": [{"property": "v_out", "op": "nope", "value": 5}],
                }
            )

    async def test_filter_missing_value_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="value"):
            await server.handle_search_parametric(
                {
                    "category": "buck_converter",
                    "filters": [{"property": "v_out", "op": "=="}],
                }
            )

    async def test_limit_out_of_range_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="limit"):
            await server.handle_search_parametric({"category": "buck_converter", "limit": 0})

    async def test_no_filters_defaults_to_empty_list(
        self, server: ComponentServer, store: _FakeStore
    ) -> None:
        await server.handle_search_parametric({"category": "buck_converter"})
        assert store.queries[0].filters == []


class TestSearchIntent:
    async def test_happy_path(self, store: _FakeStore) -> None:
        store.stub("buck_converter", [_row("MP2459", "buck_converter", cost_usd=0.42)])
        llm = StubIntentLLM(
            lambda _p: json.dumps(
                {
                    "subsystem": None,
                    "reasoning": None,
                    "categories": [
                        {
                            "category": "buck_converter",
                            "purchase_unit": "discrete_part",
                            "role": None,
                            "confidence": 0.9,
                            "constraints": [{"property": "v_out", "op": "==", "value": 5}],
                        }
                    ],
                }
            )
        )
        server = ComponentServer(
            search_store=store,  # type: ignore[arg-type]
            knowledge_service=_FakeKnowledgeService(),  # type: ignore[arg-type]
            llm=llm,
        )
        result = await server.handle_search_intent({"intent_text": "5V buck converter"})
        assert result["build_from_parts"][0]["candidates"][0]["mpn"] == "MP2459"
        assert result["buy_complete"] == []

    async def test_missing_intent_text_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="intent_text"):
            await server.handle_search_intent({})

    async def test_top_k_out_of_range_raises(self, server: ComponentServer) -> None:
        with pytest.raises(ValueError, match="top_k_per_role"):
            await server.handle_search_intent({"intent_text": "anything", "top_k_per_role": 0})
