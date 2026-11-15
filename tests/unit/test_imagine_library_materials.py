"""The studio's material figures come from the material library (FORGE-880).

Live evidence (night-light studio, 2026-11-15): the library holds PLA with a maker's sourced
density, stiffness and strength, but the studio screened and costed the enclosure with its
own built-in PLA figures; nothing in the library reached it.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine import library_materials

TDS = "https://cdn.shopify.com/s/files/1/0380/5149/files/Bambu_PLA_Basic_Technical_Data_Sheet.pdf"


def _prop(value: float, unit: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source_kind": "datasheet", "source_ref": TDS}


PLA = {
    "name": "pla",
    "title": "PLA (polylactic acid), FDM filament",
    "version": 1,
    "properties": {
        "density_kg_m3": _prop(1240.0, "kg/m3"),
        "youngs_modulus_mpa": _prop(2680.0, "MPa"),
        "youngs_modulus_across_mpa": _prop(2160.0, "MPa"),
        "ultimate_strength_mpa": _prop(39.0, "MPa"),
        "tensile_strength_across_mpa": _prop(35.0, "MPa"),
    },
}
PRUSAMENT = {  # a maker's grade, named for its family
    "name": "prusament_petg",
    "title": "Prusament PETG",
    "version": 2,
    "properties": {"density_kg_m3": _prop(1270.0, "kg/m3")},
}
LIB = {e["name"]: e for e in (PLA, PRUSAMENT)}


def _lookup(name: str) -> dict[str, Any] | None:
    return LIB.get(name)


def _studio(material: str, process: str = "fdm") -> dict[str, Any]:
    return {"process": {"id": process, "material": material}, "context": {}}


def _mats(out: dict[str, Any]) -> list[dict[str, Any]]:
    return ((out.get("context") or {}).get("machines") or {}).get("materials") or []


def test_the_library_figures_override_the_catalogue_material() -> None:
    out, notes = library_materials.derive_material(_studio("pla"), _lookup)
    (m,) = _mats(out)
    assert m["id"] == "pla" and m["process"] == "fdm"
    assert m["density"] == 1.24  # g/cm3, as the engine reads it
    # FDM parts are weakest across the layers; the across figures are used when the library has them
    assert m["E"] == 2160.0 and m["str"] == 35.0
    assert m["derived_from"] == "library" and "pla v1" in m["source"]
    # what the library does not hold stays the catalogue's, and says so
    assert {"costKg", "k", "ul94"} <= set(m["defaulted"])
    assert "costKg" not in m
    assert any("costKg" in n or "price" in n for n in notes)


def test_a_makers_grade_overrides_its_family() -> None:
    out, _ = library_materials.derive_material(_studio("prusament_petg"), _lookup)
    (m,) = _mats(out)
    assert m["id"] == "petg"  # the engine screens it as its family
    assert m["density"] == 1.27
    assert "prusament_petg" in m["source"]


def test_a_material_not_in_the_library_is_left_as_the_catalogue_has_it() -> None:
    # negative control: the catalogue's own figures, nothing derived
    out, notes = library_materials.derive_material(_studio("asa"), _lookup)
    assert _mats(out) == [] and notes == []


def test_derived_materials_are_rebuilt_and_the_agents_own_are_kept() -> None:
    studio = _studio("pla")
    studio["context"]["machines"] = {
        "materials": [
            {"id": "pla", "process": "fdm", "derived_from": "library", "density": 9},
            {"id": "mine", "process": "fdm", "name": "agent's own"},
        ]
    }
    out, _ = library_materials.derive_material(studio, _lookup)
    by_id = {m["id"]: m for m in _mats(out)}
    assert by_id["pla"]["density"] == 1.24 and "mine" in by_id


def test_the_input_config_is_not_changed() -> None:
    studio = _studio("pla")
    library_materials.derive_material(studio, _lookup)
    assert studio["context"] == {}


async def test_a_studio_write_uses_the_library_pla() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.library import catalog_routes
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI
    from twin_core.library.catalog import SqliteCatalog

    ops.init_operations(None)
    catalog_routes.init_catalog(SqliteCatalog())
    try:
        catalog_routes.get_catalog().register(
            "material",
            {
                "name": "pla",
                "title": "PLA",
                "family": "polymer",
                "properties": PLA["properties"],
            },
        )
        cfg = full_config()
        cfg["process"] = {"id": "fdm", "material": "pla", "qty": 10}
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        mats = out.config["context"]["machines"]["materials"]
        assert any(m["id"] == "pla" and m["density"] == 1.24 for m in mats)
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)
