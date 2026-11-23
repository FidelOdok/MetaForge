"""A brief studio: a size limit in place of a drawn form (FORGE-917..920)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import brief
from api_gateway.imagine.service import ImagineError, ImagineService
from api_gateway.twin.form_thread import form_parts_of
from tests.unit.imagine_fixtures import full_config
from twin_core.api import InMemoryTwinAPI
from twin_core.form_thread import CadItem, build_rows, check_form_changes

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"


def _brief(envelope: list[float] | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = {"template": "brief", "envelope_mm": envelope or [100, 50, 20]}
    return cfg


def test_a_brief_form_expands_to_one_size_limit_part() -> None:
    out = brief.expand(_brief())
    form = out["form"]
    assert form["template"] == "authored"
    assert form["brief"] == {"envelope_mm": [100.0, 50.0, 20.0]}
    assert form["params"] == {"env_l": 100.0, "env_w": 50.0, "env_h": 20.0}
    (part,) = form["authored"]["parts"]
    assert part["id"] == "size-limit" and part["kind"] == "generated"
    # Expanding again changes nothing, and a new limit redraws the box.
    assert brief.expand(out) == out
    out["form"]["brief"]["envelope_mm"] = [120, 50, 20]
    assert brief.expand(out)["form"]["params"]["env_l"] == 120.0


def test_what_the_studio_took_from_develop_stays_on_the_size_limit() -> None:
    out = brief.expand(_brief())
    out["form"]["authored"]["parts"][0]["develop_form"] = {"item_ref": "ASM-X@2"}
    again = brief.expand(out)
    assert again["form"]["authored"]["parts"][0]["develop_form"] == {"item_ref": "ASM-X@2"}


@pytest.mark.parametrize("bad", [None, [1, 2], [100, "wide", 20], [100, 50, 0], [True, 1, 1]])
def test_a_brief_form_needs_a_usable_size_limit(bad: Any) -> None:
    cfg = _brief()
    cfg["form"]["envelope_mm"] = bad
    with pytest.raises(brief.BriefError):
        brief.expand(cfg)


def test_a_full_studio_is_left_alone() -> None:
    cfg = copy.deepcopy(full_config())
    assert brief.expand(cfg) is cfg and not brief.is_brief(cfg)


@pytest.fixture
async def service() -> ImagineService:
    svc = ImagineService(InMemoryTwinAPI.create())
    await svc.put_studio(PROJECT, _brief(), actor="test")
    return svc


async def test_a_brief_studio_is_written_and_stored_expanded(service: ImagineService) -> None:
    head = await service._head(PROJECT)
    assert head is not None
    assert head.state["config"]["form"]["brief"]["envelope_mm"] == [100.0, 50.0, 20.0]
    html = await service.engine_html(PROJECT)
    assert "size-limit" in html


async def test_a_brief_studio_without_a_size_limit_is_refused() -> None:
    svc = ImagineService(InMemoryTwinAPI.create())
    cfg = _brief()
    del cfg["form"]["envelope_mm"]
    with pytest.raises(ImagineError) as caught:
        await svc.put_studio(PROJECT, cfg, actor="test")
    assert caught.value.status == 422


async def test_the_handover_does_not_wait_for_a_form_to_be_seen_or_screened(
    service: ImagineService,
) -> None:
    check = await service.checklist(PROJECT)
    items = {i.id: i for i in check.items}
    assert items["screening"].status == "assumed"
    concept = items["concept_assembly"]
    assert concept.status == "done"
    assert "seen the form" not in concept.detail and "concept mesh:" not in concept.detail


def test_the_size_limit_pairs_with_the_assembly_and_says_within_or_exceeds() -> None:
    parts = form_parts_of(brief.expand(_brief()))
    assert [p.limit for p in parts] == [True]
    box = {"min_x": -65, "max_x": 65, "min_y": -26, "max_y": 26, "min_z": 0, "max_z": 22}
    items = [
        CadItem("CAD-CASE-BASE", 4, "Case base", "n1", box),
        CadItem("ASM-FC", 5, "Flight controller assembly", "n2", box, assembly=True),
    ]
    roomy = {"size-limit": [140.0, 30.0, 60.0]}
    (row,) = build_rows(parts, roomy, items)
    assert row.status == "within" and row.develop and row.develop["item_ref"] == "ASM-FC@5"
    tight = {"size-limit": [100.0, 20.0, 50.0]}
    (row,) = build_rows(parts, tight, items)
    assert row.status == "exceeds" and "x +30 mm" in row.note


def test_the_size_limit_is_changed_as_one_value() -> None:
    ok = check_form_changes(
        [{"op": "replace", "path": "/form/brief/envelope_mm", "value": [130, 52, 22]}]
    )
    assert ok[0]["value"] == [130, 52, 22]
    with pytest.raises(ValueError):
        check_form_changes([{"op": "replace", "path": "/form/brief/envelope_mm", "value": 130}])


async def test_taking_a_larger_assembly_redraws_the_size_limit(service: ImagineService) -> None:
    row = {
        "key": "size-limit",
        "name": "Size limit",
        "status": "exceeds",
        "studio": {"part_id": "size-limit", "size_mm": [100, 20, 50]},
        "develop": {"item_ref": "ASM-FC@5", "size_mm": [130, 22, 52]},
    }
    out = await service.accept_form(
        PROJECT,
        1,
        row,
        [{"op": "replace", "path": "/form/brief/envelope_mm", "value": [130, 52, 22]}],
        actor="agent",
    )
    head = await service._head(PROJECT)
    assert head is not None and out.revision == 2
    form = head.state["config"]["form"]
    assert form["brief"]["envelope_mm"] == [130.0, 52.0, 22.0]
    assert form["params"]["env_l"] == 130.0
    assert form["authored"]["parts"][0]["develop_form"]["item_ref"] == "ASM-FC@5"
