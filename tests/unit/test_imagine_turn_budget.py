"""An Imagine design turn can write a studio (FORGE-896).

Live evidence (sandbox, 2026-11-17): two Imagine design turns asked to start a studio ended
at max_steps (40) without writing it: about 50 of the steps paged through large library
entries with read_tool_result, about ten read the playbook. A linked part now takes its
identity from the library on write, and the stage gets its own step budget.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.chat.harness_backend import chat_max_steps, imagine_max_steps
from api_gateway.imagine.library_facts import derive_part_facts

ENTRY = {
    "name": "seeed_xiao_esp32s3",
    "title": "Seeed Studio XIAO ESP32S3",
    "manufacturer": "Seeed Studio",
    "mpn": "113991114",
    "datasheet": "https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/",
    "version": 3,
    "updated_at": 1763337600.0,  # 2025-11-17
}


def _config(**part: Any) -> dict[str, Any]:
    return {"context": {"components": {"parts": [{"id": "board-a", "role": "board", **part}]}}}


def test_a_linked_part_takes_its_identity_from_the_library() -> None:
    out, notes = derive_part_facts(_config(library="seeed_xiao_esp32s3"), lambda n: ENTRY)
    part = out["context"]["components"]["parts"][0]
    assert part["manufacturer"] == "Seeed Studio" and part["mpn"] == "113991114"
    assert part["datasheet"].startswith("https://wiki.seeedstudio.com")
    assert part["name"] == "Seeed Studio XIAO ESP32S3" and part["retrieved"] == "2025-11-17"
    assert part["facts_from"].startswith("library seeed_xiao_esp32s3 v3:")
    assert notes and "board-a" in notes[0]


def test_what_the_agent_wrote_is_kept() -> None:
    out, _ = derive_part_facts(
        _config(library="seeed_xiao_esp32s3", mpn="102010635", retrieved="2026-11-01"),
        lambda n: ENTRY,
    )
    part = out["context"]["components"]["parts"][0]
    assert part["mpn"] == "102010635" and part["retrieved"] == "2026-11-01"
    assert part["manufacturer"] == "Seeed Studio"


def test_an_unlinked_or_unknown_part_is_left_alone() -> None:
    plain = _config(mpn="X")
    assert derive_part_facts(plain, lambda n: ENTRY) == (plain, [])
    missing = _config(library="nope")
    out, notes = derive_part_facts(missing, lambda n: None)
    assert out == missing and notes == []


def test_the_imagine_stage_has_its_own_step_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_CHAT_IMAGINE_MAX_STEPS", raising=False)
    monkeypatch.delenv("METAFORGE_CHAT_MAX_STEPS", raising=False)
    assert chat_max_steps() == 24 and imagine_max_steps() == 60
    monkeypatch.setenv("METAFORGE_CHAT_IMAGINE_MAX_STEPS", "80")
    assert imagine_max_steps() == 80
    # never below the general budget
    monkeypatch.setenv("METAFORGE_CHAT_MAX_STEPS", "100")
    assert imagine_max_steps() == 100


async def test_a_studio_write_fills_a_part_that_only_names_its_entry() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.library import catalog_routes
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI
    from twin_core.library.catalog import SqliteCatalog

    catalog = SqliteCatalog()
    catalog.register(
        "component",
        {
            "name": "acme_th_sensor",
            "title": "Acme TH-1 temperature sensor",
            "family": "electronic",
            "origin": "purchased",
            "manufacturer": "Acme",
            "mpn": "TH-1",
            "datasheet": "https://acme.example/th1.pdf",
        },
        by="test",
    )
    catalog_routes.init_catalog(catalog)
    ops.init_operations(None)
    try:
        cfg = full_config()
        part = cfg["context"]["components"]["parts"][0]
        for key in ("manufacturer", "mpn", "datasheet", "retrieved"):
            part.pop(key, None)
        part["library"] = "acme_th_sensor"
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        stored = out.config["context"]["components"]["parts"][0]
        assert stored["manufacturer"] == "Acme" and stored["mpn"] == "TH-1"
        assert stored["facts_from"].startswith("library acme_th_sensor v1:")
    finally:
        catalog_routes.init_catalog(None)
        ops.init_operations(None)
