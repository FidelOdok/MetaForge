"""The studio's machine comes from the machine library (FORGE-879).

Live evidence (night-light studio, 2026-11-15): ``process.machine: "mi_f00e2f9c3cf2"``, the
owner's CR-10 V3 instance, was refused with "not in context/machines.json"; the studio read
machines only from its own context, a vendored P1S-class ``fdm-generic`` and a UK supplier
list, so it screened a CR-10 print with P1S figures.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine import library_machines
from twin_core.library.machines import SqliteMachineLibrary

SPEC = "https://store.creality.com/blogs/all/cr-series-3d-printers-parameter-comparison"


def _cap(value: Any, kind: str = "manufacturer_spec", ref: str = SPEC) -> dict[str, Any]:
    return {"value": value, "unit": "mm", "source_kind": kind, "source_ref": ref}


@pytest.fixture
def library() -> SqliteMachineLibrary:
    lib = SqliteMachineLibrary()
    model = lib.register(
        {
            "layer": "model",
            "kind": "fdm_printer",
            "make": "Creality",
            "model": "CR-10 V3",
            "processes": ["fdm_printing"],
            "capabilities": {"build_volume_mm": _cap([300, 300, 400])},
        }
    )
    lib.register(
        {
            "layer": "instance",
            "kind": "fdm_printer",
            "name": "My CR-10 V3 (home)",
            "model_id": model.id,
            "route": "in_house",
            "processes": ["fdm_printing"],
            "capabilities": {
                "min_wall_mm": _cap(0.8, "measurement", "calibration print 2026-10-01"),
            },
        }
    )
    lib.register(
        {
            "layer": "instance",
            "kind": "cnc_mill",
            "name": "Shop mill",
            "route": "in_house",
            "processes": ["cnc_milling"],
        }
    )
    return lib


def _instance(lib: SqliteMachineLibrary, name: str) -> Any:
    return next(m for m in lib.list(layer="instance") if m.name == name)


def _studio(machine: str | None = None) -> dict[str, Any]:
    process: dict[str, Any] = {"id": "fdm", "material": "pla", "qty": 10}
    if machine:
        process["machine"] = machine
    return {"process": process, "context": {"components": {"parts": []}}}


def _machines(out: dict[str, Any]) -> list[dict[str, Any]]:
    return out["context"]["machines"]["machines"]


def test_a_library_instance_named_as_the_machine_is_built_from_the_library(
    library: SqliteMachineLibrary,
) -> None:
    cr10 = _instance(library, "My CR-10 V3 (home)")
    out, notes = library_machines.derive_machines(_studio(cr10.id), library)
    m = next(x for x in _machines(out) if x["id"] == cr10.id)
    assert m["process"] == "fdm" and m["ruleBase"] == "fdm"
    assert m["buildVolume"] == [300.0, 300.0, 400.0]  # from the model
    assert m["limits"]["wallMin"] == 0.8  # the instance's own measurement wins
    assert m["derived_from"] == "library"
    assert "library" in m["source"]
    # figures the library lacks come from the process default, and say so
    assert "tolerance" in m["defaulted"] and m["limits"]["tolerance"] > 0
    assert any("tolerance" in n for n in notes)


def test_in_house_machines_for_the_process_are_offered_beside_the_choice(
    library: SqliteMachineLibrary,
) -> None:
    out, _ = library_machines.derive_machines(_studio(), library)
    names = {m["name"] for m in _machines(out)}
    assert "My CR-10 V3 (home)" in names
    assert "Shop mill" not in names  # a CNC mill is not an FDM machine


def test_a_machine_of_another_process_is_named_and_not_used(
    library: SqliteMachineLibrary,
) -> None:
    mill = _instance(library, "Shop mill")
    out, notes = library_machines.derive_machines(_studio(mill.id), library)
    assert all(m["id"] != mill.id for m in _machines(out))
    assert any(mill.id in n and "cnc" in n for n in notes)


def test_an_unknown_machine_id_is_left_for_the_validator(
    library: SqliteMachineLibrary,
) -> None:
    # negative control: a name that is not in the library is not invented into one
    out, notes = library_machines.derive_machines(_studio("fdm-generic"), library)
    assert all(m["id"] != "fdm-generic" for m in _machines(out))
    assert notes == [] or all("fdm-generic" not in n for n in notes)


def test_derived_machines_are_rebuilt_and_the_agents_own_are_kept(
    library: SqliteMachineLibrary,
) -> None:
    studio = _studio()
    studio["context"]["machines"] = {
        "machines": [
            {"id": "old", "process": "fdm", "source": "library x", "derived_from": "library"},
            {"id": "mine", "process": "fdm", "source": "user"},
        ]
    }
    out, _ = library_machines.derive_machines(studio, library)
    ids = {m["id"] for m in _machines(out)}
    assert "old" not in ids and "mine" in ids


def test_the_input_config_is_not_changed(library: SqliteMachineLibrary) -> None:
    studio = _studio(_instance(library, "My CR-10 V3 (home)").id)
    library_machines.derive_machines(studio, library)
    assert "machines" not in studio["context"]


def test_a_derived_machine_passes_the_formgraph_context_checks(
    library: SqliteMachineLibrary,
) -> None:
    from api_gateway.imagine.formgraph import fg_context

    cr10 = _instance(library, "My CR-10 V3 (home)")
    out, _ = library_machines.derive_machines(_studio(cr10.id), library)
    machines, _, _ = fg_context.merged_machines(out["context"])
    assert cr10.id in {m["id"] for m in machines}


async def test_a_studio_write_accepts_the_owners_library_machine(
    library: SqliteMachineLibrary,
) -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.library import routes as library_routes
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI

    ops.init_operations(None)
    library_routes.init_machine_library(library)
    try:
        cr10 = _instance(library, "My CR-10 V3 (home)")
        cfg = full_config()
        cfg["process"] = {**(cfg.get("process") or {}), "id": "fdm", "material": "pla"}
        cfg["process"]["machine"] = cr10.id
        cfg["process"].pop("finish", None)
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        ids = [m["id"] for m in out.config["context"]["machines"]["machines"]]
        assert cr10.id in ids
        assert out.config["process"]["machine"] == cr10.id
    finally:
        library_routes.init_machine_library(None)
        ops.init_operations(None)
