"""BOM read API (MET-504) — mapping + the /v1/bom route over the twin."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_gateway.bom.routes import _item_to_component, init_twin, list_bom
from twin_core.api import InMemoryTwinAPI
from twin_core.models.bom_item import BOMItem


class TestMapping:
    def test_maps_fields_to_dashboard_shape(self) -> None:
        item = BOMItem(
            part_number="STM32F405RGT6",
            manufacturer="STMicroelectronics",
            description="MCU",
            quantity=2,
            reference_designators=["U1", "U2"],
            unit_cost=3.5,
            specifications={"status": "low_stock", "category": "ic"},
        )
        c = _item_to_component(item)
        assert c.partNumber == "STM32F405RGT6"
        assert c.designator == "U1, U2"
        assert c.quantity == 2
        assert c.unitPrice == 3.5
        assert c.status == "low_stock"
        assert c.category == "ic"

    def test_unknown_status_defaults_available(self) -> None:
        item = BOMItem(part_number="x", manufacturer="y", specifications={"status": "bogus"})
        assert _item_to_component(item).status == "available"

    def test_missing_unit_cost_is_zero(self) -> None:
        item = BOMItem(part_number="x", manufacturer="y")
        c = _item_to_component(item)
        assert c.unitPrice == 0.0
        assert c.category == "uncategorized"


class TestRoute:
    async def test_empty_returns_empty_not_error(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        r = await list_bom()
        assert r.total == 0
        assert r.components == []

    async def test_lists_and_scopes_by_project(self) -> None:
        twin = InMemoryTwinAPI.create()
        pid = uuid4()
        await twin._graph.add_node(BOMItem(part_number="A", manufacturer="m", project_id=pid))
        await twin._graph.add_node(BOMItem(part_number="B", manufacturer="m"))  # unscoped
        init_twin(twin)

        scoped = await list_bom(project_id=str(pid))
        assert scoped.total == 1
        assert scoped.components[0].partNumber == "A"
        assert scoped.components[0].projectId == str(pid)

        all_items = await list_bom()
        assert all_items.total == 2

    async def test_invalid_project_id_400(self) -> None:
        init_twin(InMemoryTwinAPI.create())
        with pytest.raises(HTTPException) as exc:
            await list_bom(project_id="not-a-uuid")
        assert exc.value.status_code == 400
