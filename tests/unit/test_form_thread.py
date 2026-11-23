"""The form thread: studio parts beside Develop's CAD, and taking a difference (FORGE-915)."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.imagine.service import ImagineError, ImagineService
from api_gateway.twin.form_thread import cad_heads, form_parts_of, gather_form_thread
from tests.unit.imagine_fixtures import authored_lamp_form, full_config
from twin_core.api import InMemoryTwinAPI
from twin_core.form_thread import (
    CadItem,
    FormPart,
    build_rows,
    cad_size_on_studio_axes,
    check_form_changes,
    pair,
)

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"


def _box(x: float, y: float, z: float) -> dict[str, float]:
    return {
        "min_x": -x / 2,
        "max_x": x / 2,
        "min_y": -y / 2,
        "max_y": y / 2,
        "min_z": 0.0,
        "max_z": z,
    }


def test_a_cad_box_turns_onto_the_studio_axes() -> None:
    # CAD Z up: 130 long, 52 deep, 17.5 high is studio [130, 17.5, 52] (Y up).
    assert cad_size_on_studio_axes(_box(130, 52, 17.5)) == [130.0, 17.5, 52.0]


def test_a_part_pairs_by_name_only_when_one_item_fits() -> None:
    base = CadItem("CAD-CASE-BASE", 4, "Flight controller case base", "n1")
    lid = CadItem("CAD-CASE-LID", 3, "Flight controller case lid", "n2")
    assert pair(FormPart("case-base", "Case base"), [base, lid]) is base
    assert pair(FormPart("case", "Case"), [base, lid]) is None  # both fit: no guess
    named = FormPart("case", "Case", develop_item="CAD-CASE-LID")
    assert pair(named, [base, lid]) is lid


def test_rows_say_same_differs_accepted_unmeasured_and_unpaired() -> None:
    items = [
        CadItem("CAD-CASE-BASE", 4, "Flight controller case base", "n1", _box(130, 52, 17.5)),
        CadItem("CAD-CASE-LID", 3, "Flight controller case lid", "n2", _box(84, 44, 2)),
        CadItem("CAD-STAND", 1, "Stand", "n3", None),
    ]
    parts = [
        FormPart("case-base", "Case base"),
        FormPart("case-lid", "Case lid", accepted=None),
        FormPart("stand", "Stand"),
        FormPart("knob", "Knob"),
    ]
    sizes = {"case-base": [114.0, 17.0, 44.0], "case-lid": [84.2, 2.0, 44.0], "stand": [1, 1, 1]}
    rows = {r.key: r for r in build_rows(parts, sizes, items)}
    assert rows["case-base"].status == "differs"
    assert rows["case-base"].delta_mm == [16.0, 0.5, 8.0]
    assert (
        "x +16 mm" in rows["case-base"].note
        and "y" not in rows["case-base"].note.split("(")[1][:12]
    )
    assert rows["case-lid"].status == "same"
    assert rows["stand"].status == "unmeasured"
    assert rows["knob"].status == "unpaired"
    took = FormPart("case-base", "Case base", accepted={"item_ref": "CAD-CASE-BASE@4", "at": "x"})
    assert build_rows([took], sizes, items)[0].status == "accepted"
    # A newer Develop revision is a new difference again.
    newer = [CadItem("CAD-CASE-BASE", 5, "Flight controller case base", "n4", _box(130, 52, 17.5))]
    assert build_rows([took], sizes, newer)[0].status == "differs"


def test_only_form_parameters_may_change() -> None:
    assert check_form_changes(None) == []
    ok = check_form_changes([{"op": "replace", "path": "/form/params/case_l", "value": 100}])
    assert ok == [{"op": "replace", "path": "/form/params/case_l", "value": 100}]
    for bad in (
        [{"op": "replace", "path": "/context/components", "value": 1}],
        [{"op": "add", "path": "/form/params/x", "value": 1}],
        [{"op": "replace", "path": "/form/params/x", "value": "wide"}],
    ):
        with pytest.raises(ValueError):
            check_form_changes(bad)


def test_cad_heads_keep_the_newest_revision_and_its_box() -> None:
    wp = lambda rev, box: SimpleNamespace(  # noqa: E731
        id=uuid4(),
        name="Flight controller case base",
        metadata={
            "item_key": "CAD-CASE-BASE",
            "item_revision": rev,
            "validity": {"bounding_box": box},
        },
    )
    heads = cad_heads([wp(3, _box(1, 1, 1)), wp(4, _box(130, 52, 17.5)), wp(2, None)])
    assert len(heads) == 1 and heads[0].ref == "CAD-CASE-BASE@4"
    assert heads[0].bbox == _box(130, 52, 17.5)


# -- the studio side -----------------------------------------------------


def _config() -> dict[str, Any]:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = authored_lamp_form()
    return cfg


@pytest.fixture
async def service() -> ImagineService:
    svc = ImagineService(InMemoryTwinAPI.create())
    await svc.put_studio(PROJECT, _config(), actor="test")
    return svc


async def _hand_over(service: ImagineService) -> None:
    head = await service._head(PROJECT)
    assert head is not None
    head.state["status"] = "handed_over"
    head.state["handover"] = {
        "op_id": "h1",
        "revision": head.revision,
        "at": "2026-11-23T00:00:00+00:00",
        "by": "test",
        "created": ["CS-IMAGINE@1"],
    }
    await service._save(head)


def test_the_form_parts_are_the_ones_the_studio_draws() -> None:
    ids = [p.id for p in form_parts_of(_config())]
    assert "base" in ids and "shade" in ids and "button" not in ids  # a component is bought


async def test_part_sizes_are_kept_on_a_handed_over_revision(service: ImagineService) -> None:
    await _hand_over(service)
    await service.record_form_sizes(PROJECT, 1, [{"id": "base", "size_mm": [110, 12, 110]}])
    record = await service.get_form_sizes(PROJECT)
    assert record and record["sizes"] == [{"id": "base", "size_mm": [110.0, 12.0, 110.0]}]
    with pytest.raises(ImagineError) as caught:
        await service.record_form_sizes(PROJECT, 2, [])
    assert caught.value.status == 409


def _differs(size: list[float]) -> dict[str, Any]:
    return {
        "key": "base",
        "name": "Weighted base",
        "status": "differs",
        "studio": {"part_id": "base", "size_mm": [110, 12, 110]},
        "develop": {"item_ref": "CAD-BASE@2", "node_id": "n1", "size_mm": size},
    }


async def test_accepting_a_difference_keeps_the_handover(service: ImagineService) -> None:
    await _hand_over(service)
    out = await service.accept_form(
        PROJECT,
        1,
        _differs([130, 12, 130]),
        [{"op": "replace", "path": "/form/params/base_r", "value": 65}],
        actor="agent",
    )
    assert out.revision == 2 and out.status == "handed_over"
    head = await service._head(PROJECT)
    assert head is not None
    assert head.state["handover"]["op_id"] == "h1"
    assert head.state["config"]["form"]["params"]["base_r"] == 65
    base = next(p for p in head.state["config"]["form"]["authored"]["parts"] if p["id"] == "base")
    assert base["develop_form"]["item_ref"] == "CAD-BASE@2"
    assert head.state["form_syncs"][-1]["part"] == "base"
    # Any other change to a handed-over studio is still refused.
    with pytest.raises(ImagineError) as caught:
        await service.patch_studio(
            PROJECT, 2, [{"op": "replace", "path": "/form/params/base_r", "value": 60}], actor="a"
        )
    assert caught.value.status == 409


async def test_accepting_needs_a_difference_and_the_current_revision(
    service: ImagineService,
) -> None:
    same = {**_differs([110, 12, 110]), "status": "same"}
    with pytest.raises(ImagineError) as caught:
        await service.accept_form(PROJECT, 1, same, None, actor="agent")
    assert caught.value.status == 409
    with pytest.raises(ImagineError) as caught:
        await service.accept_form(PROJECT, 7, _differs([130, 12, 130]), None, actor="agent")
    assert caught.value.status == 409
    with pytest.raises(ImagineError) as caught:
        await service.accept_form(
            PROJECT,
            1,
            _differs([130, 12, 130]),
            [{"op": "replace", "path": "/context/x", "value": 1}],
            actor="agent",
        )
    assert caught.value.status == 400


async def test_the_thread_reads_the_studio_sizes_and_the_cad(service: ImagineService) -> None:
    await service.record_form_sizes(PROJECT, 1, [{"id": "base", "size_mm": [110, 12, 110]}])
    cad = SimpleNamespace(
        id=uuid4(),
        name="Lamp weighted base",
        metadata={
            "item_key": "CAD-BASE",
            "item_revision": 2,
            "validity": {"bounding_box": _box(130, 130, 12)},
        },
    )

    class Twin:
        async def list_work_products(self, **_: Any) -> list[Any]:
            return [cad]

    data = await gather_form_thread(Twin(), PROJECT, studio_service=service)
    row = next(r for r in data["rows"] if r["key"] == "base")
    assert row["status"] == "differs" and row["develop"]["item_ref"] == "CAD-BASE@2"
    assert row["delta_mm"] == [20.0, 0.0, 20.0]
    assert data["studio"]["revision"] == 1 and not data["warnings"]
