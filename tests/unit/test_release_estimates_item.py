"""R8.2: a part resting on unaccepted estimates is a release gap, not a pass."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.library import estimate_gaps
from tests.unit.test_library_estimates import _register, _sourced
from twin_core.dfm import release_checklist
from twin_core.dfm.release_checklist import (
    GATE_ITEMS,
    PROTOTYPE_ITEMS,
    estimates_item,
    set_estimate_gap_source,
)
from twin_core.library.catalog import SqliteCatalog


@pytest.fixture(autouse=True)
def _no_source() -> Any:
    set_estimate_gap_source(None)
    yield
    set_estimate_gap_source(None)


async def test_without_the_library_it_says_so_and_never_passes() -> None:
    item = await estimates_item(None, uuid4())
    assert item.status == "not_applicable" and "not connected" in item.detail
    assert "estimates_accepted" in GATE_ITEMS and "estimates_accepted" in PROTOTYPE_ITEMS


@pytest.mark.parametrize(
    ("gaps", "status"),
    [([], "pass"), (["adafruit-4480: its estimates (width_mm) are proposed"], "fail")],
)
async def test_gaps_fail_the_item_and_none_pass_it(gaps: list[str], status: str) -> None:
    async def source(twin: Any, pid: Any) -> list[str]:
        return gaps

    set_estimate_gap_source(source)
    item = await estimates_item(None, uuid4())
    assert item.status == status and item.blockers == gaps
    if gaps:
        assert item.next_steps == ["Have a person accept or replace the estimates on adafruit-4480"]


async def test_an_unreadable_library_is_a_gap() -> None:
    async def broken(twin: Any, pid: Any) -> list[str]:
        raise RuntimeError("catalog down")

    set_estimate_gap_source(broken)
    item = await estimates_item(None, uuid4())
    assert item.status == "fail" and "catalog down" in item.blockers[0]


async def test_the_gateway_source_follows_bom_items_to_their_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = SqliteCatalog()
    guessed = _register(catalog)  # proposed estimates
    sourced = _register(
        catalog, name="sourced", specs={"length_mm": _sourced(25.4), "width_mm": _sourced(17.78)}
    )
    accepted = _register(catalog, name="accepted-guess")
    catalog.decide_estimate(accepted.id, "accept", decided_by="ada")
    import api_gateway.library.catalog_routes as routes

    monkeypatch.setattr(routes, "get_catalog", lambda: catalog)

    def item(entry: Any) -> Any:
        return SimpleNamespace(
            specifications={"library_entry": {"id": entry.id, "name": entry.name}},
            part_number="",
        )

    twin = SimpleNamespace()

    async def list_bom_items(pid: Any) -> list[Any]:
        return [item(guessed), item(sourced), item(accepted), item(guessed)]

    twin.list_bom_items = list_bom_items
    gaps = await estimate_gaps.project_estimate_gaps(twin, uuid4())
    assert len(gaps) == 1 and gaps[0].startswith("adafruit-4480: its estimates")
    assert release_checklist.ITEM_ORDER[1] == "estimates_accepted"
