"""Desktop FDM and an LED driver in the Imagine studio catalogue (FORGE-827).

A headless lamp run found no FDM process ("sls/pa12 stands in for FDM PETG on
the P1S") and no function for the lamp's LED driver. FDM is now a catalogue
process with its own rule book (not SLS rules under another name), a library
material named for its family (``prusament_petg``) screens as that family,
and ``e_ldrv`` is a constant-current buck LED driver.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine import engine_page
from api_gateway.imagine.engine_page import ConfigError, validate_config
from api_gateway.imagine.formgraph import build_page
from api_gateway.imagine.service import _mfc_from_config
from tests.unit.imagine_fixtures import full_config

DATA = Path(build_page.__file__).parent / "data"
ENGINE = Path(engine_page.__file__).parent / "engine" / "engine.js"


def _fdm(material: str = "petg", **process: Any) -> dict[str, Any]:
    cfg = full_config()
    cfg["process"] = {
        "id": "fdm",
        "material": material,
        "finish": "asp",
        "fixings": "ins25",
        "qty": 20,
        **process,
    }
    return cfg


@pytest.mark.parametrize("material", ["petg", "pla", "petgcf", "asa"])
def test_fdm_takes_its_filaments(material: str) -> None:
    validated = validate_config(_fdm(material))
    assert validated.compiled["process"]["material"] == material


def test_the_fdm_process_is_a_p1s_class_desktop_printer() -> None:
    fdm = engine_page.catalogue()["processes"]["fdm"]
    assert set(fdm["materials"]) == {"petg", "pla", "petgcf", "asa"}
    assert fdm["limits"]["buildVolume"] == [256, 256, 256]
    assert fdm["limits"]["wallMin"] >= 2 * fdm["limits"]["lineWidth"]
    assert "P1S" in fdm["machine"]


@pytest.mark.parametrize(
    ("library", "family"),
    [
        ("prusament_petg", "petg"),
        ("bambu-petg-cf", "petgcf"),
        ("PETG-CF", "petgcf"),
        ("polymaker_asa", "asa"),
        ("bambu_pla_basic", "pla"),
    ],
)
def test_a_library_material_screens_as_its_family(library: str, family: str) -> None:
    validated = validate_config(_fdm(library))
    # The page screens with the family's figures ...
    assert validated.compiled["process"]["material"] == family
    assert validated.compiled["process"]["libraryMaterial"] == library
    assert any("screens as" in w for w in validated.warnings)
    # ... and the twin keeps the person's own material.
    assert validated.config["process"]["material"] == library
    assert _mfc_from_config(validated.config)["selected_material"] == library


def test_an_unknown_material_is_still_refused() -> None:
    with pytest.raises(ConfigError, match="not available for FDM printing"):
        validate_config(_fdm("unobtainium"))


def test_a_family_name_does_not_cross_processes() -> None:
    cfg = full_config()
    cfg["process"] = {"id": "sls", "material": "prusament_petg", "qty": 20}
    with pytest.raises(ConfigError, match="not available for SLS"):
        validate_config(cfg)


def test_the_fdm_machine_uses_the_fdm_rule_book() -> None:
    machines = json.loads((DATA / "machines.default.json").read_text(encoding="utf-8"))
    assert "fdm" not in machines["processes"], "fdm is native, not SLS rules renamed"
    (fdm,) = [m for m in machines["machines"] if m["id"] == "fdm-generic"]
    assert fdm["ruleBase"] == "fdm"
    assert fdm["buildVolume"] == [256, 256, 256]
    assert set(fdm["finishes"]) <= set(engine_page.catalogue()["processes"]["fdm"]["finishes"])


def test_a_context_cannot_replace_the_native_fdm_rules() -> None:
    cfg = _fdm("petg")
    cfg["context"] = copy.deepcopy(cfg["context"])
    cfg["context"]["machines"] = {
        "schemaVersion": 1,
        "processes": {"fdm": {"name": "FDM", "ruleBase": "sls", "materials": ["pa12"]}},
    }
    # Still FDM's own materials: the SLS-based entry is ignored.
    validated = validate_config(cfg)
    assert validated.compiled["process"]["material"] == "petg"


def test_the_engine_screens_fdm_with_fdm_rules() -> None:
    js = ENGINE.read_text(encoding="utf-8")
    assert "fdm:{id:'fdm'" in js
    assert "env:[256,256,256]" in js
    for rule in (
        "Wall in whole perimeters",
        "Print orientation",
        "Warp risk",
        "Screws in printed plastic",
    ):
        assert rule in js, rule
    # The SLS panel rule stays SLS only; FDM rules run for fdm and its derivatives.
    assert "if(P.id==='sls'&&Math.max(s.W,s.D)>200" in js
    assert "const isFDM=P=>P.id==='fdm'||P.base==='fdm';" in js
    assert "fdm:[{id:'petg'" in js
    assert "procs:['im','sls','fdm']" in js


def test_the_led_driver_is_a_function() -> None:
    cat = engine_page.catalogue()
    assert cat["functions"]["e_ldrv"]["block"] == "Constant-current LED driver (buck)"
    assert "EB.e_ldrv={name:'Constant-current LED driver (buck)'" in ENGINE.read_text(
        encoding="utf-8"
    )
    cfg = _fdm("petg")
    cfg["functions"] = [*cfg["functions"], "e_ldrv"]
    validate_config(cfg)
    schema = json.loads((DATA / "product.schema.json").read_text(encoding="utf-8"))
    assert "e_ldrv" in json.dumps(schema)


def test_resolve_material_prefers_the_longest_family() -> None:
    fdm = engine_page.catalogue()["processes"]["fdm"]
    assert build_page.resolve_material(fdm, "petg") == "petg"
    assert build_page.resolve_material(fdm, "sunlu_petg_cf_black") == "petgcf"
    assert build_page.resolve_material(fdm, "nylon") is None
    assert build_page.resolve_material(None, "petg") is None
