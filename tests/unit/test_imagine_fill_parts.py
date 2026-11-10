"""A studio write gives every real part a 3D body (plan C5).

A part with no size gets one from the resolver, labelled with how it was made, and the
write says so in its warnings. A part that already has a size is left exactly as the
agent wrote it, and an excluded part is never touched.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine import resolve_part as rp
from api_gateway.imagine.service import ImagineService
from tests.unit.imagine_fixtures import full_config
from tests.unit.test_imagine_kicad_part import _library
from twin_core.api import InMemoryTwinAPI

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"


def _parts(config: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = config["context"]["components"]["parts"]
    return parts


def _without_geometry(config: dict[str, Any], part_id: str) -> dict[str, Any]:
    out = copy.deepcopy(config)
    for p in _parts(out):
        if p["id"] == part_id:
            p.pop("geometry")
    return out


def test_a_part_with_no_size_gets_a_body_and_a_note_while_the_others_are_untouched() -> None:
    config = _without_geometry(full_config(), "btn-a")
    before = copy.deepcopy(config)
    filled, notes = rp.fill_part_bodies(config)
    assert config == before, "the input was changed"
    by_id = {p["id"]: p for p in _parts(filled)}
    g = by_id["btn-a"]["geometry"]
    assert g["envelope"] and g["body"] and g["fidelity"] in ("approximation", "placeholder")
    for p in _parts(before):
        if p["id"] != "btn-a":
            assert by_id[p["id"]] == p, p["id"]
    assert len(notes) == 1 and "btn-a" in notes[0] and "drew its 3D" in notes[0]


def test_a_part_that_already_has_a_size_is_left_exactly_as_written() -> None:
    config = full_config()
    filled, notes = rp.fill_part_bodies(config)
    assert filled == config and notes == []


def test_an_excluded_part_is_never_touched() -> None:
    config = _without_geometry(full_config(), "btn-a")
    next(p for p in _parts(config) if p["id"] == "btn-a")["excluded"] = True
    filled, notes = rp.fill_part_bodies(config)
    assert "geometry" not in next(p for p in _parts(filled) if p["id"] == "btn-a")
    assert notes == []


@pytest.mark.parametrize(
    "config",
    [{}, {"context": None}, {"context": {}}, {"context": {"components": {"parts": "x"}}}],
)
def test_a_config_with_no_parts_comes_back_unchanged(config: dict[str, Any]) -> None:
    filled, notes = rp.fill_part_bodies(config)
    assert filled == config and notes == []


def test_a_part_found_in_kicad_is_filled_from_it_and_the_note_names_the_source(
    tmp_path: Path,
) -> None:
    kicad = _library(tmp_path)
    config = _without_geometry(full_config(), "th-a")
    next(p for p in _parts(config) if p["id"] == "th-a")["mpn"] = "DEMO280"
    filled, notes = rp.fill_part_bodies(config, kicad_config=kicad)
    g = next(p for p in _parts(filled) if p["id"] == "th-a")["geometry"]
    assert g["source"].startswith("KiCad library Sensor_Demo:DEMO280")
    assert "th-a" in notes[0] and "KiCad" in notes[0]


# -- through the real write --------------------------------------------------------------------


@pytest.fixture
def service() -> ImagineService:
    return ImagineService(InMemoryTwinAPI.create())


async def test_a_write_stores_the_body_and_says_so_in_its_warnings(
    service: ImagineService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(rp.LIBRARY_ENV, raising=False)
    config = _without_geometry(full_config(), "btn-a")
    response = await service.put_studio(PROJECT, config, actor="test")
    stored = next(p for p in _parts(response.config or {}) if p["id"] == "btn-a")
    assert stored["geometry"]["fidelity"] in ("approximation", "placeholder")
    assert any("btn-a" in w and "drew its 3D" in w for w in response.warnings), response.warnings
    # The C0 warning that the 3D is not the real part is there too.
    assert any("btn-a" in w and "not the real part" in w for w in response.warnings)


async def test_a_refused_write_stores_nothing_even_when_a_body_would_have_been_drawn(
    service: ImagineService,
) -> None:
    from api_gateway.imagine.service import ImagineError

    config = _without_geometry(full_config(), "btn-a")
    config["color"] = "red"  # refused by the checker
    with pytest.raises(ImagineError):
        await service.put_studio(PROJECT, config, actor="test")
    assert (await service.get_studio(PROJECT)).status == "none"
