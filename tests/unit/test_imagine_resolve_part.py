"""One call that gives a real part a 3D body, and says which rung it came from (plan C4).

The order is: a better model the part already has, KiCad's own model, the datasheet's
package drawing (not built yet, and listed as such), the closest approximation, and
last a placeholder. A part always gets a body, and the answer lists every rung tried.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine import resolve_part as rp
from api_gateway.imagine.formgraph import fg_context
from api_gateway.imagine.kicad_part import StepSize, measure_step
from api_gateway.imagine.service import ImagineService
from tests.unit.test_imagine_kicad_part import _kernel, _library
from twin_core.api import InMemoryTwinAPI


def _problems(geometry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fg_context.validate_geometry("part", {"geometry": geometry}, errors, [])
    return errors


def _rung(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(t for t in result["tried"] if t["rung"] == name)


def test_a_part_in_the_kicad_library_is_exact_and_the_answer_lists_every_rung(
    tmp_path: Path,
) -> None:
    config = _library(tmp_path)
    part = {"id": "p", "role": "fn:e_th", "mpn": "DEMO280"}
    result = rp.resolve_part(part, kicad_config=config, measure=_kernel([2.5, 2.0, 0.78]))
    assert result["rung"] == "exact"
    assert result["geometry"]["fidelity"] == "exact"
    assert _rung(result, "kicad")["outcome"] == "used"
    assert _rung(result, "datasheet_package")["outcome"] == "not_built"
    assert not _problems(result["geometry"])


def test_without_kicad_the_part_still_gets_a_body_and_the_answer_says_why_not_exact() -> None:
    part = {"id": "p", "role": "fn:e_btn", "name": "Tactile switch"}
    result = rp.resolve_part(part, kicad_config=None)
    assert result["rung"] == "approximation"
    assert _rung(result, "kicad")["outcome"] == "skipped"
    assert "not installed" in _rung(result, "kicad")["why"]
    assert _rung(result, "approximation")["outcome"] == "used"
    assert not _problems(result["geometry"])


def test_a_part_with_nothing_known_is_a_placeholder_and_every_rung_says_why() -> None:
    result = rp.resolve_part({"id": "mystery", "role": "other"}, kicad_config=None)
    assert result["rung"] == "placeholder"
    assert result["geometry"]["fidelity"] == "placeholder"
    assert all(t["why"] for t in result["tried"])
    assert "generic" in result["message"]


def test_a_better_model_the_part_already_has_is_kept_and_nothing_is_consulted() -> None:
    def boom(path: Path) -> StepSize | None:
        raise AssertionError("must not measure when the part already has a better model")

    geometry = {
        "envelope": [2.0, 2.5, 0.8],
        "source": "KiCad library",
        "fidelity": "exact",
        "fidelity_basis": "read from the library's own model",
        "body": [{"shape": "box", "at": [0, 0, 0.4], "size": [2.0, 2.5, 0.8]}],
    }
    result = rp.resolve_part(
        {"id": "p", "role": "other", "mpn": "X", "geometry": geometry},
        kicad_config=None,
        measure=boom,
    )
    assert result["rung"] == "existing" and result["geometry"] == geometry


def test_a_kicad_miss_falls_through_to_the_next_rung_with_the_reason(tmp_path: Path) -> None:
    config = _library(tmp_path)
    part = {"id": "p", "role": "fn:e_btn", "mpn": "NOT-IN-KICAD"}
    result = rp.resolve_part(part, kicad_config=config, measure=_kernel([1, 1, 1]))
    assert result["rung"] == "approximation"
    assert "no symbol named" in _rung(result, "kicad")["why"]


@pytest.mark.parametrize(
    "part",
    [{}, {"id": None}, {"id": "x", "role": 5, "name": ["a"], "geometry": "oops"}, {"mpn": 7}],
)
def test_garbage_in_still_gives_a_valid_placeholder_and_never_raises(part: Any) -> None:
    result = rp.resolve_part(part, kicad_config=None)
    assert result["rung"] in ("placeholder", "approximation")
    assert not _problems(result["geometry"])


def test_the_resolver_never_changes_the_part_it_was_given() -> None:
    part = {"id": "p", "role": "fn:e_led", "geometry": {"footprint": "LED_0603_1608Metric"}}
    before = {"id": "p", "role": "fn:e_led", "geometry": {"footprint": "LED_0603_1608Metric"}}
    rp.resolve_part(part, kicad_config=None)
    assert part == before


# -- the measurer: a CAD kernel when there is one, an estimate when there is not ------------


def _fake_cadquery(monkeypatch: pytest.MonkeyPatch, size: tuple[float, float, float]) -> None:
    box = types.SimpleNamespace(xlen=size[0], ylen=size[1], zlen=size[2])
    shape = types.SimpleNamespace(BoundingBox=lambda: box)
    workplane = types.SimpleNamespace(val=lambda: shape)
    cq = types.ModuleType("cadquery")
    cq.importers = types.SimpleNamespace(importStep=lambda p: workplane)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cadquery", cq)


def test_a_cad_kernel_in_the_process_is_used_and_called_a_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "m.step"
    model.write_text("ISO-10303-21;")
    _fake_cadquery(monkeypatch, (2.5, 2.0, 0.78))
    assert measure_step(model) == StepSize(size=[2.5, 2.0, 0.78], method="cad_kernel")


def test_with_no_kernel_the_estimate_is_used_and_called_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "cadquery", None)  # an import of it now fails
    model = tmp_path / "m.step"
    model.write_text(
        "DATA;\n#1 = CARTESIAN_POINT('', (0, 0, 0));\n#2 = CARTESIAN_POINT('', (1, 0, 0));\n"
        "#3 = CARTESIAN_POINT('', (1, 2, 0));\n#4 = CARTESIAN_POINT('', (1, 2, 3));\n"
    )
    result = measure_step(model)
    assert result is not None and result.method == "text_estimate"


# -- where the KiCad folders are -------------------------------------------------------------


def test_the_kicad_folders_come_from_one_named_variable_and_a_missing_one_is_none(
    tmp_path: Path,
) -> None:
    assert rp.kicad_config_from_env({}) is None
    assert rp.kicad_config_from_env({"METAFORGE_KICAD_LIBRARY_DIR": str(tmp_path / "nope")}) is None
    (tmp_path / "symbols").mkdir()
    config = rp.kicad_config_from_env({"METAFORGE_KICAD_LIBRARY_DIR": str(tmp_path)})
    assert config is not None
    assert config.symbol_dir == str(tmp_path / "symbols")
    assert config.footprint_dir == str(tmp_path / "footprints")
    assert config.model_dir == str(tmp_path / "3dmodels")


# -- the tool ---------------------------------------------------------------------------------


@pytest.fixture
def twin_server() -> Iterator[Any]:
    from tool_registry.tools.twin.adapter import TwinServer

    svc = ImagineService(InMemoryTwinAPI.create())
    yield TwinServer(twin=svc.twin, imagine_studio=svc)


def test_the_tool_is_a_read_in_the_imagine_stage_only(twin_server: Any) -> None:
    from mcp_core.annotations import annotations_for
    from mcp_core.profiles import CREATE_STAGE_TOOLS

    assert "twin.imagine_resolve_part" in twin_server.tool_ids
    assert "twin.imagine_resolve_part" in CREATE_STAGE_TOOLS["imagine"]
    for other in ("develop", "check", "make"):
        assert "twin.imagine_resolve_part" not in CREATE_STAGE_TOOLS.get(other, set()), other
    assert annotations_for("twin.imagine_resolve_part").get("readOnlyHint") is True


def test_the_description_says_what_to_do_with_the_answer(twin_server: Any) -> None:
    text = twin_server._tools["twin.imagine_resolve_part"].manifest.description.lower()
    for phrase in ("fidelity", "geometry", "never type a size", "nothing is written"):
        assert phrase in text, phrase


async def test_the_tool_returns_a_body_for_a_part_and_refuses_a_non_object(
    twin_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("METAFORGE_KICAD_LIBRARY_DIR", raising=False)
    result = await twin_server.imagine_resolve_part(
        {"part": {"id": "b", "role": "fn:e_btn", "name": "Tactile switch"}}
    )
    assert result["rung"] == "approximation" and result["geometry"]["fidelity"]
    with pytest.raises(ValueError, match="part"):
        await twin_server.imagine_resolve_part({"part": "not an object"})
    with pytest.raises(ValueError, match="part"):
        await twin_server.imagine_resolve_part({})
