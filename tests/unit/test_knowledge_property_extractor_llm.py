"""Unit tests for Tier-2/3 LLM property extraction (MET-462)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from digital_twin.knowledge.llm_property_extractor import (
    DERIVED_BAND,
    LLM_INFERRED_BAND,
    StubPropertyLLM,
    build_extraction_prompt,
    infer_property,
)
from digital_twin.knowledge.property_extractor import (
    ExtractionMethod,
    extract_properties_for_mpn,
)


def _resp(**kwargs: Any) -> str:
    return json.dumps(kwargs)


class _StubTwin:
    def __init__(self, datasheet: Any | None) -> None:
        self._datasheet = datasheet

    async def get_current_datasheet(self, mpn: str) -> Any | None:
        return self._datasheet


class _StubDatasheet:
    def __init__(
        self,
        *,
        tables: list[dict[str, Any]] | None = None,
        text: str | None = None,
        revision: str = "rev1",
    ) -> None:
        self.revision = revision
        self.published_at = None
        self.source_path = "datasheets/x.pdf"
        self.source_url = None
        self.metadata: dict[str, Any] = {"tables": tables or []}
        if text is not None:
            self.metadata["text"] = text


# ---------------------------------------------------------------------------
# infer_property — Tier 2 / 3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_inferred_value_is_banded_and_methoded():
    llm = StubPropertyLLM(
        {"supply_voltage": _resp(found=True, value="3.3", unit="V", confidence=0.95)}
    )
    prop = await infer_property(
        llm, mpn="STM32", property_name="supply_voltage", datasheet_text="VDD is 3.3 V nominal."
    )
    assert prop.found is True
    assert prop.value == "3.3"
    assert prop.unit == "V"
    assert prop.extraction_method is ExtractionMethod.LLM_INFERRED
    # 0.95 clamps down into the Tier-2 band.
    assert prop.confidence == LLM_INFERRED_BAND[1]
    assert LLM_INFERRED_BAND[0] <= prop.confidence <= LLM_INFERRED_BAND[1]


@pytest.mark.asyncio
async def test_derived_method_uses_derived_band():
    llm = StubPropertyLLM(
        {"power": _resp(found=True, value="0.66", unit="W", confidence=0.9, method="derived")}
    )
    prop = await infer_property(
        llm, mpn="STM32", property_name="power", datasheet_text="3.3V at 200mA typical."
    )
    assert prop.extraction_method is ExtractionMethod.DERIVED
    assert prop.confidence == DERIVED_BAND[1]


@pytest.mark.asyncio
async def test_low_confidence_clamps_up_to_band_floor():
    llm = StubPropertyLLM({"freq": _resp(found=True, value="80", unit="MHz", confidence=0.1)})
    prop = await infer_property(
        llm, mpn="STM32", property_name="freq", datasheet_text="Max clock 80 MHz."
    )
    assert prop.confidence == LLM_INFERRED_BAND[0]


@pytest.mark.asyncio
async def test_not_found_verdict_returns_not_found():
    llm = StubPropertyLLM({"weight": _resp(found=False)})
    prop = await infer_property(
        llm, mpn="STM32", property_name="weight", datasheet_text="Some text."
    )
    assert prop.found is False
    assert prop.extraction_method is ExtractionMethod.NOT_FOUND
    assert prop.confidence == 0.0


@pytest.mark.asyncio
async def test_malformed_json_fails_open_to_not_found():
    llm = StubPropertyLLM(lambda _prompt: "not json at all")
    prop = await infer_property(llm, mpn="STM32", property_name="anything", datasheet_text="text")
    assert prop.extraction_method is ExtractionMethod.NOT_FOUND


@pytest.mark.asyncio
async def test_empty_text_skips_llm_call():
    llm = StubPropertyLLM({"x": _resp(found=True, value="1")})
    prop = await infer_property(llm, mpn="STM32", property_name="x", datasheet_text="")
    assert prop.extraction_method is ExtractionMethod.NOT_FOUND
    assert llm.calls == []


@pytest.mark.asyncio
async def test_fenced_json_is_parsed():
    fenced = "```json\n" + _resp(found=True, value="85", unit="C") + "\n```"
    llm = StubPropertyLLM({"temp": fenced})
    prop = await infer_property(
        llm, mpn="STM32", property_name="temp", datasheet_text="Operating temp 85 C max."
    )
    assert prop.value == "85"
    assert prop.unit == "C"


def test_prompt_contains_mpn_property_and_text():
    prompt = build_extraction_prompt("STM32", "supply_voltage", "VDD 3.3V")
    assert "STM32" in prompt
    assert "supply_voltage" in prompt
    assert "VDD 3.3V" in prompt


# ---------------------------------------------------------------------------
# Orchestration — Tier-1 first, Tier-2/3 fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_hit_does_not_consult_llm():
    tables = [{"page": 1, "rows": [["supply_voltage", "3.3 V"]]}]
    twin = _StubTwin(_StubDatasheet(tables=tables, text="prose"))
    llm = StubPropertyLLM({"supply_voltage": _resp(found=True, value="9.9")})

    result = await extract_properties_for_mpn(twin, "STM32", ["supply_voltage"], llm=llm)

    item = result.items[0]
    assert item.extraction_method is ExtractionMethod.VERBATIM
    assert item.value == "3.3"
    assert llm.calls == []  # Tier-1 satisfied it; LLM untouched.


@pytest.mark.asyncio
async def test_tier1_miss_falls_back_to_llm():
    twin = _StubTwin(_StubDatasheet(tables=[], text="The part runs at 80 MHz core clock."))
    llm = StubPropertyLLM(
        {"core_clock": _resp(found=True, value="80", unit="MHz", confidence=0.75)}
    )

    result = await extract_properties_for_mpn(twin, "STM32", ["core_clock"], llm=llm)

    item = result.items[0]
    assert item.extraction_method is ExtractionMethod.LLM_INFERRED
    assert item.value == "80"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_no_llm_stays_tier1_only():
    twin = _StubTwin(_StubDatasheet(tables=[], text="80 MHz core clock"))
    result = await extract_properties_for_mpn(twin, "STM32", ["core_clock"])
    item = result.items[0]
    assert item.extraction_method is ExtractionMethod.NOT_FOUND


@pytest.mark.asyncio
async def test_mpn_not_found_short_circuits():
    twin = _StubTwin(None)
    llm = StubPropertyLLM({"x": _resp(found=True, value="1")})
    result = await extract_properties_for_mpn(twin, "MISSING", ["x"], llm=llm)
    assert result.mpn_found is False
    assert result.items == []
    assert llm.calls == []


# ---------------------------------------------------------------------------
# G4 (MET-477): LLM-over-chunks fallback when no Twin Datasheet exists
# ---------------------------------------------------------------------------


def _hit(content: str, source_path: str | None = "kb/bme280.txt") -> Any:
    from digital_twin.knowledge.service import SearchHit

    return SearchHit(
        content=content,
        similarity_score=0.9,
        source_path=source_path,
        heading=None,
        chunk_index=0,
        total_chunks=1,
    )


@pytest.mark.asyncio
async def test_g4_search_fallback_extracts_when_no_datasheet_node():
    """No Twin Datasheet, but search returns chunks + llm wired → LLM-over-chunks."""
    twin = _StubTwin(None)
    captured_query: dict[str, Any] = {}

    async def fake_search(query: str, top_k: int) -> list[Any]:
        captured_query["query"] = query
        captured_query["top_k"] = top_k
        return [
            _hit("BME280 supply voltage VDD = 3.3 V typical, range 1.71-3.6 V."),
            _hit("Operating temperature range -40 to +85 °C."),
        ]

    llm = StubPropertyLLM(
        {"supply_voltage": _resp(found=True, value="3.3", unit="V", confidence=0.75)}
    )

    result = await extract_properties_for_mpn(
        twin, "BME280", ["supply_voltage"], llm=llm, search=fake_search
    )

    assert captured_query["query"] == "BME280"
    assert result.mpn_found is True
    assert result.datasheet_revision is None
    assert result.datasheet_source_path == "kb/bme280.txt"
    assert len(result.items) == 1
    assert result.items[0].value == "3.3"
    assert result.items[0].extraction_method is ExtractionMethod.LLM_INFERRED


@pytest.mark.asyncio
async def test_g4_search_fallback_disabled_without_search():
    """Pre-G4 contract: no search wired → mpn_found=False even with llm."""
    twin = _StubTwin(None)
    llm = StubPropertyLLM({"x": _resp(found=True, value="1")})
    result = await extract_properties_for_mpn(twin, "BME280", ["x"], llm=llm)
    assert result.mpn_found is False
    assert result.items == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_g4_search_fallback_disabled_without_llm():
    """No llm wired → fallback inactive even when search would have hits."""
    twin = _StubTwin(None)
    search_calls: list[str] = []

    async def fake_search(query: str, top_k: int) -> list[Any]:
        search_calls.append(query)
        return [_hit("BME280 supply 3.3V")]

    result = await extract_properties_for_mpn(twin, "BME280", ["x"], search=fake_search)
    assert result.mpn_found is False
    assert result.items == []
    # No LLM means no point calling search.
    assert search_calls == []


@pytest.mark.asyncio
async def test_g4_search_fallback_empty_hits_returns_mpn_not_found():
    twin = _StubTwin(None)

    async def fake_search(query: str, top_k: int) -> list[Any]:
        return []

    llm = StubPropertyLLM({"x": _resp(found=True, value="1")})
    result = await extract_properties_for_mpn(twin, "MISSING", ["x"], llm=llm, search=fake_search)
    assert result.mpn_found is False
    assert result.items == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_g4_search_fallback_swallows_search_errors():
    """Backend failure in search must not crash extract — degrade to NOT_FOUND."""
    twin = _StubTwin(None)

    async def broken_search(query: str, top_k: int) -> list[Any]:
        raise RuntimeError("pgvector connection refused")

    llm = StubPropertyLLM({"x": _resp(found=True, value="1")})
    result = await extract_properties_for_mpn(twin, "BME280", ["x"], llm=llm, search=broken_search)
    assert result.mpn_found is False
    assert result.items == []


@pytest.mark.asyncio
async def test_g4_search_fallback_multiple_properties_against_chunks():
    """One search call, top-K chunks fuel multiple per-property LLM calls."""
    twin = _StubTwin(None)
    search_count = {"n": 0}

    async def fake_search(query: str, top_k: int) -> list[Any]:
        search_count["n"] += 1
        return [
            _hit("BME280 supply voltage 3.3V, range 1.71-3.6V."),
            _hit("BME280 measurement: temperature, humidity, pressure."),
        ]

    llm = StubPropertyLLM(
        {
            "supply_voltage": _resp(found=True, value="3.3", unit="V", confidence=0.75),
            "operating_temperature_max": _resp(found=True, value="85", unit="°C", confidence=0.7),
        }
    )

    result = await extract_properties_for_mpn(
        twin,
        "BME280",
        ["supply_voltage", "operating_temperature_max"],
        llm=llm,
        search=fake_search,
    )

    assert result.mpn_found is True
    # Only one search round-trip even though two properties were requested.
    assert search_count["n"] == 1
    assert len(result.items) == 2
    values = {item.property_name: item.value for item in result.items}
    assert values["supply_voltage"] == "3.3"
    assert values["operating_temperature_max"] == "85"


@pytest.mark.asyncio
async def test_five_properties_mixed_tiers_against_ground_truth():
    # Tier-1 covers two; the LLM covers three.
    tables = [
        {
            "page": 1,
            "rows": [
                ["supply_voltage", "3.3 V"],
                ["operating_temperature_max", "85 °C"],
            ],
        }
    ]
    text = "The MCU has 512 KB flash, runs at 80 MHz, and draws 200 mA typical."
    twin = _StubTwin(_StubDatasheet(tables=tables, text=text))
    llm = StubPropertyLLM(
        {
            "flash_size": _resp(found=True, value="512", unit="KB", confidence=0.75),
            "core_clock": _resp(found=True, value="80", unit="MHz", confidence=0.7),
            "supply_current": _resp(
                found=True, value="200", unit="mA", confidence=0.5, method="derived"
            ),
        }
    )

    props = [
        "supply_voltage",
        "operating_temperature_max",
        "flash_size",
        "core_clock",
        "supply_current",
    ]
    result = await extract_properties_for_mpn(twin, "STM32", props, llm=llm)

    assert result.mpn_found is True
    assert len(result.items) == 5
    found = {item.property_name: item for item in result.items}

    # Ground truth.
    assert found["supply_voltage"].value == "3.3"
    assert found["supply_voltage"].extraction_method is ExtractionMethod.VERBATIM
    assert found["operating_temperature_max"].value == "85"
    assert found["flash_size"].value == "512"
    assert found["flash_size"].extraction_method is ExtractionMethod.LLM_INFERRED
    assert found["core_clock"].value == "80"
    assert found["supply_current"].extraction_method is ExtractionMethod.DERIVED

    # Every found property carries a confidence in the documented 0.4-1.0 range.
    for item in result.items:
        assert 0.4 <= item.confidence <= 1.0
