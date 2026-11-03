"""The materials seed and stock sizes (FORGE-754).

For laser-cut wood the library held only 18 mm sheet goods, no stock sizes
and no dowels: the sheets a laser vendor stocks and a desktop laser cuts were
missing, and nesting had no sheet to nest on. Every seeded value must pass
the catalog's own checks and cite where it came from.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from twin_core.library import material_seed
from twin_core.library.catalog import CatalogError, SqliteCatalog, parse_stock


def _entries() -> list[dict[str, Any]]:
    return [e for doc in material_seed.seed_documents() for e in doc["entries"]]


def _refs(body: dict[str, Any]) -> list[str]:
    refs = [p.get("source_ref", "") for p in (body.get("properties") or {}).values()]
    refs += [p.get("source_ref", "") for p in (body.get("specs") or {}).values()]
    refs += [s.get("source_ref", "") for s in body.get("stock") or []]
    return refs


def test_the_seed_has_thin_birch_plywood_with_stock_sizes() -> None:
    docs = material_seed.seed_documents()
    assert docs and all(d.get("checked") and d.get("source") for d in docs)
    plywood = [
        e["body"] for e in _entries() if e["kind"] == "material" and "plywood" in e["body"]["name"]
    ]
    assert len(plywood) >= 3
    assert all(p.get("stock") for p in plywood)


def test_every_value_cites_where_it_came_from() -> None:
    for entry in _entries():
        for ref in _refs(entry["body"]):
            assert ref, entry["body"]["name"]


def test_every_entry_passes_the_catalogs_own_checks() -> None:
    catalog = SqliteCatalog()
    out = material_seed.seed_catalog(catalog)
    assert out["failed"] == []
    assert len(out["added"]) == len(_entries())


def test_loading_again_adds_nothing_and_keeps_a_persons_entry() -> None:
    catalog = SqliteCatalog()
    first = next(e for e in _entries() if e["kind"] == "material")["body"]
    catalog.register(
        "material",
        {"name": first["name"], "family": first["family"], "forms": ["sheet"]},
        by="user:fidel",
    )
    out = material_seed.seed_catalog(catalog)
    assert first["name"] in out["skipped"]
    assert material_seed.seed_catalog(catalog)["added"] == []
    assert catalog.get(first["name"], "material").properties == {}


def test_the_seed_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LIBRARY_SEED", "off")
    assert material_seed.seed_catalog(SqliteCatalog())["added"] == []


def test_a_sheet_size_needs_all_three_sizes_and_a_source() -> None:
    ok = parse_stock(
        [
            {
                "form": "sheet",
                "thickness_mm": 6.5,
                "width_mm": 1250,
                "length_mm": 2500,
                "source_kind": "manufacturer_spec",
                "source_ref": "https://example.test/datasheet",
            }
        ]
    )
    assert ok[0]["width_mm"] == 1250.0 and ok[0]["needs_review"] is False
    with pytest.raises(CatalogError, match="needs thickness_mm, width_mm and length_mm"):
        parse_stock([{"form": "sheet", "thickness_mm": 6, "source_kind": "user"}])
    with pytest.raises(CatalogError, match="source"):
        parse_stock([{"form": "sheet", "thickness_mm": 6, "width_mm": 1, "length_mm": 1}])


def test_an_entry_stored_before_stock_existed_still_loads() -> None:
    catalog = SqliteCatalog()
    entry = catalog.register("material", {"name": "old_sheet", "family": "sheet_good"})
    body = entry.as_dict()
    body.pop("stock")
    with catalog._lock:  # noqa: SLF001 - simulate a row written by the previous version
        catalog._conn.execute(  # noqa: SLF001
            "UPDATE catalog_entries SET body = ? WHERE id = ?", (json.dumps(body), entry.id)
        )
    assert catalog.get("old_sheet", "material").stock == []
