"""A studio draws a library part with the library's own model (MetaForge, 2026-11-12).

A component library entry may carry ``form_code``: the code that draws the part once, in
mm, centred on it. A studio part places it with ``g.model("name")``. When the studio is
written, the gateway copies each model a part names into ``form.authored.models``, so the
revision holds the exact code it drew with, and the page (which has no network) can run it.
A component linked to a real part whose library entry has a model is drawn with that model
instead of the part's bounding box.
"""

from __future__ import annotations

import copy
from typing import Any

from api_gateway.imagine import component_link, library_models

XIAO = "return [g.box(21, 3, 17.8).name('pcb'), g.box(9, 3.2, 7.4).at(-8, 3, 0).name('usb')];"
LIBRARY: dict[str, dict[str, Any]] = {
    "seeed_xiao": {"code": XIAO, "version": 3, "entry": "co_x"},
    "bare_cell": {"code": "", "version": 1, "entry": "co_c"},
}


def lookup(name: str) -> dict[str, Any] | None:
    return LIBRARY.get(name)


def form(*codes: str, models: dict[str, Any] | None = None) -> dict[str, Any]:
    parts = [
        {"id": f"p{i}", "name": f"P{i}", "kind": "component", "code": c}
        for i, c in enumerate(codes)
    ]
    authored: dict[str, Any] = {"parts": parts}
    if models is not None:
        authored["models"] = models
    return {"form": {"template": "authored", "authored": authored}}


def test_a_named_model_is_copied_into_the_revision_with_its_version() -> None:
    cfg, notes = library_models.embed(form("return [g.model('seeed_xiao').at(1, 2, 3)];"), lookup)
    assert cfg["form"]["authored"]["models"] == {
        "seeed_xiao": {"code": XIAO, "version": 3, "entry": "co_x"}
    }
    assert notes == []


def test_models_no_part_names_any_more_are_dropped() -> None:
    stale = {"old_part": {"code": "return [g.box(1, 1, 1)];", "version": 1, "entry": "co_o"}}
    cfg, _ = library_models.embed(form("return [g.box(1, 1, 1)];", models=stale), lookup)
    assert "models" not in cfg["form"]["authored"]


def test_a_missing_entry_and_an_entry_without_a_model_are_named() -> None:
    cfg, notes = library_models.embed(
        form("return [g.model('nope'), g.model('bare_cell')];"), lookup
    )
    assert "models" not in cfg["form"]["authored"]
    assert len(notes) == 2
    assert any('"nope"' in n and "no entry" in n for n in notes)
    assert any('"bare_cell"' in n and "form_code" in n for n in notes)


def test_the_input_config_is_not_changed() -> None:
    before = form("return [g.model('seeed_xiao')];")
    kept = copy.deepcopy(before)
    library_models.embed(before, lookup)
    assert before == kept


def test_a_broken_lookup_is_skipped_and_said() -> None:
    def broken(name: str) -> dict[str, Any] | None:
        raise RuntimeError("catalog unavailable")

    cfg, notes = library_models.embed(form("return [g.model('seeed_xiao')];"), broken)
    assert "models" not in cfg["form"]["authored"]
    assert notes and "could not be read" in notes[0]


def _linked(library: str | None) -> dict[str, Any]:
    real: dict[str, Any] = {"id": "mcu-a", "geometry": {"envelope": [21.0, 17.8, 3.0]}}
    if library:
        real["library"] = library
    return {
        "form": {
            "template": "authored",
            "authored": {
                "parts": [
                    {
                        "id": "mcu",
                        "name": "MCU",
                        "kind": "component",
                        "component_ref": "mcu-a",
                        "at": [0, 5, 0],
                    }
                ]
            },
        },
        "context": {"components": {"schemaVersion": 1, "parts": [real]}},
    }


def has_model(name: str) -> bool:
    return bool((LIBRARY.get(name) or {}).get("code"))


def test_a_linked_part_whose_entry_has_a_model_is_drawn_with_it() -> None:
    cfg, notes = component_link.expand_component_parts(_linked("seeed_xiao"), has_model=has_model)
    part = cfg["form"]["authored"]["parts"][0]
    assert part["code"] == 'return [g.model("seeed_xiao").at(0, 5, 0)];'
    assert "library model seeed_xiao" in notes[0]


def test_a_linked_part_without_a_model_keeps_its_box() -> None:
    for library in ("bare_cell", None):
        cfg, _ = component_link.expand_component_parts(_linked(library), has_model=has_model)
        assert cfg["form"]["authored"]["parts"][0]["code"].startswith("return [g.box(21, 3, 17.8)")


def test_a_derived_model_part_is_derived_again_on_the_next_write() -> None:
    first, _ = component_link.expand_component_parts(_linked("seeed_xiao"), has_model=has_model)
    first["form"]["authored"]["parts"][0]["at"] = [9, 9, 9]
    again, _ = component_link.expand_component_parts(first, has_model=has_model)
    assert again["form"]["authored"]["parts"][0]["code"].endswith(".at(9, 9, 9)];")


def test_the_page_config_keeps_the_models() -> None:
    from api_gateway.imagine import engine_page
    from tests.unit.imagine_fixtures import full_config

    cfg = full_config()
    cfg["form"]["authored"]["parts"][0]["code"] = "return [g.model('seeed_xiao')];"
    cfg, _ = library_models.embed(cfg, lookup)
    compiled = engine_page.validate_config(cfg).compiled
    assert compiled["form"]["authored"]["models"]["seeed_xiao"]["code"] == XIAO


async def test_a_studio_write_copies_a_registered_model() -> None:
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
            "component",
            {"name": "seeed_xiao", "family": "module", "origin": "purchased", "form_code": XIAO},
        )
        svc = ImagineService(InMemoryTwinAPI.create())
        cfg = full_config()
        cfg["form"]["authored"]["parts"][0]["code"] = "return [g.model('seeed_xiao')];"
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        models = out.config["form"]["authored"]["models"]
        assert models["seeed_xiao"]["code"] == XIAO
        assert models["seeed_xiao"]["version"] == 1
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)
