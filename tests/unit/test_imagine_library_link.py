"""A studio's real parts point at the component library (MetaForge, 2026-11-11).

A part researched in one studio was lost to every other project. Each real part in
``context.components.parts`` may now name its library entry in ``library``. A write says
which real parts are not linked, and which name an entry the library does not have (still
awaiting approval, or misspelt). It is a warning: the write still goes through.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine import library_link


def config(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"context": {"components": {"schemaVersion": 1, "parts": list(parts)}}}


def part(pid: str, **over: Any) -> dict[str, Any]:
    return {"id": pid, "name": pid.title(), "mpn": f"{pid}-1", **over}


KNOWN = {"ublox_max_m10s"}


def find(name: str) -> bool:
    return name in KNOWN


def test_a_linked_part_that_exists_says_nothing() -> None:
    assert library_link.library_notes(config(part("gnss", library="ublox_max_m10s")), find) == []


def test_an_unlinked_real_part_is_named_with_how_to_link_it() -> None:
    notes = library_link.library_notes(config(part("gnss")), find)
    assert len(notes) == 1
    assert "gnss" in notes[0]
    assert "library.register_component" in notes[0]
    assert '"library"' in notes[0]


def test_a_link_to_an_entry_the_library_lacks_says_so() -> None:
    notes = library_link.library_notes(config(part("gnss", library="ublox_max_m10x")), find)
    assert len(notes) == 1
    assert "ublox_max_m10x" in notes[0] and "not in the library" in notes[0]


def test_a_stand_in_is_not_asked_to_be_registered() -> None:
    assert library_link.library_notes(config(part("gnss", standIn=True)), find) == []


def test_unlinked_parts_are_listed_in_one_note() -> None:
    notes = library_link.library_notes(config(part("a"), part("b"), part("c")), find)
    assert len(notes) == 1
    assert all(p in notes[0] for p in ("a", "b", "c"))


def test_no_components_and_a_broken_lookup_say_nothing() -> None:
    assert library_link.library_notes({}, find) == []

    def broken(name: str) -> bool:
        raise RuntimeError("catalog unavailable")

    notes = library_link.library_notes(config(part("gnss", library="x")), broken)
    assert notes == []


async def test_a_studio_write_carries_the_note_until_the_part_is_linked() -> None:
    import copy

    from api_gateway.imagine.service import ImagineService
    from api_gateway.library import catalog_routes
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI
    from twin_core.library.catalog import SqliteCatalog

    ops.init_operations(None)
    catalog_routes.init_catalog(SqliteCatalog())
    try:
        svc = ImagineService(InMemoryTwinAPI.create())
        cfg = full_config()
        parts = cfg["context"]["components"]["parts"]
        assert parts, "the fixture has real parts"
        project = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12"
        first = await svc.put_studio(project, cfg, actor="test")
        assert any("not in the component library" in w for w in first.warnings)

        for p in parts:
            catalog_routes.get_catalog().register(
                "component",
                {"name": f"lib_{p['id']}", "family": "module", "origin": "purchased"},
            )
        linked = copy.deepcopy(cfg)
        for p in linked["context"]["components"]["parts"]:
            p["library"] = f"lib_{p['id']}"
        second = await svc.put_studio(project, linked, actor="test")
        assert not [w for w in second.warnings if "library" in w], second.warnings
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)


def test_the_imagine_stage_can_look_up_and_register_a_part() -> None:
    from mcp_core.profiles import CREATE_STAGE_TOOLS, MAX_TOOLS

    imagine = CREATE_STAGE_TOOLS["imagine"]
    for tool in ("library.list_components", "library.get_component", "library.register_component"):
        assert tool in imagine
    assert len(imagine) <= MAX_TOOLS
