"""Unit tests for the intent-search orchestrator (MET-436, mode 3)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from digital_twin.catalog.query import CatalogQueryResult, ComponentCatalogRow, ComponentFilter
from digital_twin.knowledge.intent_search import search_intent
from digital_twin.knowledge.intent_translator import StubIntentLLM
from digital_twin.knowledge.property_extractor import ExtractedProperty, ExtractionMethod
from digital_twin.knowledge.service import ExtractedProperties, SearchHit
from digital_twin.knowledge.subsystem_templates import KNOWN_SUBSYSTEMS

# ---------- fakes ----------


def _row(
    mpn: str,
    category: str,
    *,
    purchase_unit: str = "discrete_part",
    cost_usd: float | None = None,
) -> ComponentCatalogRow:
    return ComponentCatalogRow(
        id=uuid4(),
        mpn=mpn,
        manufacturer="Acme",
        category=category,
        purchase_unit=purchase_unit,  # type: ignore[arg-type]
        cost_usd=cost_usd,
        lifecycle="active",
        datasheet_url="",
        specs={},
        extraction_meta={},
        schema_version=1,
    )


def _hit(mpn: str) -> SearchHit:
    return SearchHit(
        content="",
        similarity_score=0.9,
        source_path=f"datasheets/{mpn}.pdf",
        heading="Electrical Characteristics",
        chunk_index=0,
        total_chunks=1,
        metadata={"mpn": mpn},
    )


class _FakeSearchParametric:
    """Tracks calls; returns stubbed rows per category, empty otherwise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[ComponentFilter], int]] = []
        self._rows_by_category: dict[str, list[ComponentCatalogRow]] = {}

    def stub(self, category: str, rows: list[ComponentCatalogRow]) -> None:
        self._rows_by_category[category] = rows

    async def __call__(
        self, category: str, filters: list[ComponentFilter], limit: int
    ) -> CatalogQueryResult:
        self.calls.append((category, filters, limit))
        return CatalogQueryResult(rows=self._rows_by_category.get(category, []), query_time_ms=1.0)


class _FakeKnowledgeService:
    """Minimal KnowledgeService double — search + extract_properties only,
    matching the Protocol's runtime-checkable shape. Tracks search calls so
    tests can assert whether the fuzzy fallback actually fired."""

    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self._hits_by_substring: dict[str, list[SearchHit]] = {}
        self._props: dict[str, dict[str, dict[str, Any]]] = {}

    def stub_search(self, query_substring: str, hits: list[SearchHit]) -> None:
        self._hits_by_substring[query_substring] = hits

    def stub_extract(self, mpn: str, props: dict[str, dict[str, Any]]) -> None:
        self._props[mpn] = props

    async def ingest(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: Any = None,
        filters: dict[str, Any] | None = None,
        project_id: Any = None,
        rerank: bool = False,
        actor_id: str | None = None,
        include_historical: bool = False,
        hybrid: bool = False,
    ) -> list[SearchHit]:
        self.search_calls.append(query)
        for substring, hits in self._hits_by_substring.items():
            if substring in query:
                return hits[:top_k]
        return []

    async def delete_by_source(self, *args: Any, **kwargs: Any) -> int:
        raise NotImplementedError

    async def list_sources(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise NotImplementedError

    async def extract_properties(
        self,
        mpn: str,
        properties: list[str],
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> ExtractedProperties:
        canned = self._props.get(mpn, {})
        items: list[ExtractedProperty] = []
        for name in properties:
            spec = canned.get(name)
            if spec is None:
                items.append(
                    ExtractedProperty(
                        property_name=name,
                        value=None,
                        confidence=0.0,
                        extraction_method=ExtractionMethod.NOT_FOUND,
                    )
                )
            else:
                items.append(
                    ExtractedProperty(
                        property_name=name,
                        value=str(spec.get("value")),
                        unit=spec.get("unit"),
                        confidence=float(spec.get("confidence", 1.0)),
                        extraction_method=ExtractionMethod(spec.get("method", "verbatim")),
                    )
                )
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=bool(canned),
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=items,
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


def _llm_response(
    *,
    subsystem: str | None,
    categories: list[dict[str, Any]],
    reasoning: str | None = None,
) -> str:
    return json.dumps({"subsystem": subsystem, "reasoning": reasoning, "categories": categories})


# ---------- parametric hit skips fallback ----------


async def test_parametric_hit_skips_fuzzy_fallback() -> None:
    search_parametric = _FakeSearchParametric()
    search_parametric.stub("buck_converter", [_row("MP2459", "buck_converter", cost_usd=0.42)])
    knowledge_service = _FakeKnowledgeService()
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem=None,
            categories=[
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [{"property": "v_out", "op": "==", "value": 5}],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="5V buck converter",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    assert len(result.build_from_parts) == 1
    role = result.build_from_parts[0]
    assert role.search_mode_used == "parametric"
    assert role.candidates[0].mpn == "MP2459"
    assert role.candidates[0].source == "parametric"
    assert result.buy_complete == ()
    assert knowledge_service.search_calls == []


# ---------- parametric miss falls back to fuzzy ----------


async def test_parametric_miss_falls_back_to_fuzzy_search() -> None:
    search_parametric = _FakeSearchParametric()  # no stub -> empty for every category
    knowledge_service = _FakeKnowledgeService()
    knowledge_service.stub_search("buck_converter", [_hit("MP2459")])
    knowledge_service.stub_extract(
        "MP2459", {"v_out": {"value": 5, "unit": "V", "confidence": 1.0, "method": "verbatim"}}
    )
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem=None,
            categories=[
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [{"property": "v_out", "op": "==", "value": 5}],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="5V buck converter",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    role = result.build_from_parts[0]
    assert role.search_mode_used == "fuzzy_fallback"
    assert role.candidates[0].mpn == "MP2459"
    assert role.candidates[0].source == "fuzzy_fallback"
    assert len(knowledge_service.search_calls) == 1


# ---------- buy_complete / build_from_parts never mixed ----------


async def test_buy_complete_and_build_from_parts_never_mixed() -> None:
    search_parametric = _FakeSearchParametric()
    search_parametric.stub(
        "flight_controller",
        [_row("CUBE-ORANGE", "flight_controller", purchase_unit="cots_assembly", cost_usd=219.0)],
    )
    search_parametric.stub("microcontroller", [_row("STM32F405", "microcontroller", cost_usd=6.42)])
    knowledge_service = _FakeKnowledgeService()
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem="flight_controller",
            categories=[
                {
                    "category": "flight_controller",
                    "purchase_unit": "cots_assembly",
                    "role": None,
                    "confidence": 0.8,
                    "constraints": [],
                },
                {
                    "category": "microcontroller",
                    "purchase_unit": "discrete_part",
                    "role": "mcu",
                    "confidence": 0.9,
                    "constraints": [],
                },
            ],
        )
    )

    result = await search_intent(
        intent_text="flight controller for 250mm quad",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    assert all(r.purchase_unit == "cots_assembly" for r in result.buy_complete)
    assert all(r.purchase_unit == "discrete_part" for r in result.build_from_parts)
    buy_mpns = {c.mpn for r in result.buy_complete for c in r.candidates}
    build_mpns = {c.mpn for r in result.build_from_parts for c in r.candidates}
    assert buy_mpns == {"CUBE-ORANGE"}
    assert build_mpns == {"STM32F405"}
    assert buy_mpns.isdisjoint(build_mpns)


# ---------- op downgrade never silently misapplies ----------


async def test_not_equal_op_dropped_not_silently_remapped_in_fallback() -> None:
    search_parametric = _FakeSearchParametric()  # empty -> forces fallback
    knowledge_service = _FakeKnowledgeService()
    knowledge_service.stub_search("buck_converter", [_hit("MP2459")])
    knowledge_service.stub_extract(
        "MP2459", {"package": {"value": "SOT-23-6", "confidence": 1.0, "method": "verbatim"}}
    )
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem=None,
            categories=[
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [{"property": "package", "op": "!=", "value": "QFN-16"}],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="buck converter",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    assert any("no fuzzy-fallback equivalent" in w and "dropped" in w for w in result.warnings)
    role = result.build_from_parts[0]
    # Original 8-op constraint preserved on the RoleResult — only the
    # internal fallback call got the constraint dropped, never remapped.
    assert role.constraints[0].op == "!="


async def test_strict_inequality_downgrades_with_warning() -> None:
    search_parametric = _FakeSearchParametric()  # empty -> forces fallback
    knowledge_service = _FakeKnowledgeService()
    knowledge_service.stub_search("buck_converter", [_hit("MP2459")])
    knowledge_service.stub_extract(
        "MP2459", {"efficiency": {"value": 0.95, "confidence": 1.0, "method": "verbatim"}}
    )
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem=None,
            categories=[
                {
                    "category": "buck_converter",
                    "purchase_unit": "discrete_part",
                    "role": None,
                    "confidence": 0.9,
                    "constraints": [{"property": "efficiency", "op": ">", "value": 0.9}],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="buck converter",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    assert any("downgraded to" in w for w in result.warnings)
    assert not any("dropped" in w for w in result.warnings)


# ---------- unknown subsystem falls through ----------


async def test_unknown_subsystem_falls_through_with_warning() -> None:
    search_parametric = _FakeSearchParametric()
    search_parametric.stub("motor_driver", [_row("DRV8833", "motor_driver", cost_usd=1.2)])
    knowledge_service = _FakeKnowledgeService()
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem="gimbal_controller",  # not in KNOWN_SUBSYSTEMS
            categories=[
                {
                    "category": "motor_driver",
                    "purchase_unit": "discrete_part",
                    "role": "driver",
                    "confidence": 0.7,
                    "constraints": [],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="gimbal controller",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
        subsystem_templates=KNOWN_SUBSYSTEMS,
    )

    assert result.subsystem_hint == "gimbal_controller"
    assert any("no known template" in w for w in result.warnings)
    assert len(result.build_from_parts) == 1  # only the LLM's own role, no template merge


# ---------- known subsystem template fills in missing roles ----------


async def test_known_subsystem_template_merges_missing_roles() -> None:
    search_parametric = _FakeSearchParametric()  # empty everywhere -> forces fuzzy fallback
    knowledge_service = _FakeKnowledgeService()  # no stubs -> fallback finds nothing, that's fine
    llm = StubIntentLLM(
        lambda _p: _llm_response(
            subsystem="flight_controller",
            categories=[
                {
                    "category": "microcontroller",
                    "purchase_unit": "discrete_part",
                    "role": "mcu",
                    "confidence": 0.9,
                    "constraints": [],
                }
            ],
        )
    )

    result = await search_intent(
        intent_text="flight controller",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
        subsystem_templates=KNOWN_SUBSYSTEMS,
    )

    roles_found = {r.role for r in result.build_from_parts}
    assert "mcu" in roles_found
    assert "imu" in roles_found
    assert len(result.build_from_parts) == 8
    assert len(result.buy_complete) == 1
    assert result.buy_complete[0].category == "flight_controller"
    assert any("added from the" in w for w in result.warnings)


# ---------- translation failure still returns a usable (empty) result ----------


async def test_translation_parse_failure_returns_empty_result_with_warning() -> None:
    search_parametric = _FakeSearchParametric()
    knowledge_service = _FakeKnowledgeService()
    llm = StubIntentLLM(lambda _p: "not json")

    result = await search_intent(
        intent_text="anything",
        llm=llm,
        search_parametric=search_parametric,
        knowledge_service=knowledge_service,
    )

    assert result.buy_complete == ()
    assert result.build_from_parts == ()
    assert any("intent translation issue" in w for w in result.warnings)
