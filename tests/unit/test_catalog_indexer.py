"""Unit tests for extract -> validate -> upsert orchestration (MET-436).

Follows the fake-double style used in
``tests/unit/test_knowledge_property_extractor_llm.py``'s
``_StubTwin``/``_StubDatasheet`` — no real Postgres, no real LLM. Tier-1
verbatim table matches exercise the real ``extract_properties_for_mpn``
pipeline end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.catalog.indexer import index_component
from digital_twin.catalog.query import ComponentCatalogRow


class _FakeDatasheet:
    def __init__(self, tables: list[dict[str, Any]]) -> None:
        self.revision = "rev1"
        self.published_at = None
        self.source_path = "datasheets/fake.pdf"
        self.source_url = None
        self.metadata: dict[str, Any] = {"tables": tables}


class _FakeTwin:
    def __init__(self, datasheet: _FakeDatasheet | None) -> None:
        self._datasheet = datasheet

    async def get_current_datasheet(self, mpn: str) -> _FakeDatasheet | None:
        return self._datasheet


class _FakeStore:
    def __init__(self) -> None:
        self.upserted: list[ComponentCatalogRow] = []

    async def upsert(self, row: ComponentCatalogRow) -> ComponentCatalogRow:
        self.upserted.append(row)
        return row


def _full_buck_converter_table() -> list[dict[str, Any]]:
    return [
        {
            "page": 3,
            "heading": "Electrical Characteristics",
            "rows": [
                ["v_in_min", "4.5 V"],
                ["v_in_max", "28 V"],
                ["v_out", "5.0 V"],
                ["i_out_max", "2.0 A"],
                ["efficiency", "92 %"],
                ["package", "QFN-24"],
            ],
        }
    ]


@pytest.mark.asyncio
async def test_all_required_fields_present_gives_indexed_status():
    twin = _FakeTwin(_FakeDatasheet(_full_buck_converter_table()))
    store = _FakeStore()

    outcome = await index_component(
        twin,
        mpn="MP2459",
        manufacturer="MPS",
        category="buck_converter",
        store=store,
    )

    assert outcome.status == "indexed"
    assert outcome.missing_required == []
    assert outcome.errors == []
    assert outcome.specs["v_in_min"] == 4.5
    assert outcome.specs["v_in_max"] == 28.0
    assert outcome.specs["i_out_max"] == 2.0
    assert outcome.specs["package"] == "QFN-24"
    # "92 %" -> canonical ratio 0.92, not 92 — see units.py's efficiency note.
    assert outcome.specs["efficiency"] == pytest.approx(0.92)
    assert len(store.upserted) == 1
    assert store.upserted[0].mpn == "MP2459"
    assert store.upserted[0].purchase_unit == "discrete_part"


@pytest.mark.asyncio
async def test_missing_required_field_gives_partial_but_still_upserts():
    table = _full_buck_converter_table()
    table[0]["rows"] = [r for r in table[0]["rows"] if r[0] != "package"]  # drop required field
    twin = _FakeTwin(_FakeDatasheet(table))
    store = _FakeStore()

    outcome = await index_component(
        twin,
        mpn="MP2459",
        manufacturer="MPS",
        category="buck_converter",
        store=store,
    )

    assert outcome.status == "partial"
    assert "package" in outcome.missing_required
    assert "package" not in outcome.specs
    # Still written — a NULL specs field just excludes the row on a range filter.
    assert len(store.upserted) == 1


@pytest.mark.asyncio
async def test_mpn_not_found_gives_failed_and_writes_nothing():
    twin = _FakeTwin(None)
    store = _FakeStore()

    outcome = await index_component(
        twin,
        mpn="UNKNOWN-MPN",
        manufacturer="ACME",
        category="buck_converter",
        store=store,
    )

    assert outcome.status == "failed"
    assert outcome.errors == ["mpn_not_found"]
    assert store.upserted == []
    assert set(outcome.missing_required) == {"v_in_min", "v_in_max", "i_out_max", "package"}


@pytest.mark.asyncio
async def test_unrecognized_unit_is_recorded_as_error_and_treated_as_missing():
    table = _full_buck_converter_table()
    for row in table[0]["rows"]:
        if row[0] == "v_in_min":
            row[1] = "4.5 XYZ"  # unit XYZ has no known conversion to V
    twin = _FakeTwin(_FakeDatasheet(table))
    store = _FakeStore()

    outcome = await index_component(
        twin,
        mpn="MP2459",
        manufacturer="MPS",
        category="buck_converter",
        store=store,
    )

    assert outcome.status == "partial"
    assert "v_in_min" in outcome.missing_required
    assert "v_in_min" not in outcome.specs
    assert any("v_in_min" in e for e in outcome.errors)
    assert len(store.upserted) == 1


@pytest.mark.asyncio
async def test_unknown_category_raises_keyerror():
    twin = _FakeTwin(_FakeDatasheet(_full_buck_converter_table()))
    store = _FakeStore()
    with pytest.raises(KeyError):
        await index_component(
            twin,
            mpn="X",
            manufacturer="Y",
            category="not_a_real_category",
            store=store,
        )


@pytest.mark.asyncio
async def test_datasheet_url_falls_back_to_extracted_source_path():
    twin = _FakeTwin(_FakeDatasheet(_full_buck_converter_table()))
    store = _FakeStore()

    await index_component(
        twin,
        mpn="MP2459",
        manufacturer="MPS",
        category="buck_converter",
        store=store,
    )

    assert store.upserted[0].datasheet_url == "datasheets/fake.pdf"


@pytest.mark.asyncio
async def test_explicit_datasheet_url_overrides_extracted_source_path():
    twin = _FakeTwin(_FakeDatasheet(_full_buck_converter_table()))
    store = _FakeStore()

    await index_component(
        twin,
        mpn="MP2459",
        manufacturer="MPS",
        category="buck_converter",
        store=store,
        datasheet_url="https://example.com/explicit.pdf",
    )

    assert store.upserted[0].datasheet_url == "https://example.com/explicit.pdf"
