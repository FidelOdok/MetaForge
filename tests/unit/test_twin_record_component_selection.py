"""twin.record_component_selection — persisting a chosen component search
result as a real work product (MET-436 follow-up).

Fixes a real gap: ``component.search_parametric``/``component.search_intent``
return candidate MPNs but persist nothing — a chosen result was pure chat
output with no reviewable, versioned trace in the Digital Twin. This recorder
persists one chosen result as a ``BOMItem`` graph node and links it to its
project, mirroring ``test_twin_record_decision.py``'s coverage shape (the
recorder itself, then the twin adapter handler that calls it).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.component_recorder import make_component_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import NodeType


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(
        self, project_id: str, wp_id: str, name: str, link_type: str
    ) -> None:
        self.links.append((project_id, wp_id, name, link_type))


class TestRecorder:
    async def test_creates_bom_item_node(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)

        result = await record(
            mpn="MP2459",
            manufacturer="Monolithic Power Systems",
            category="buck_converter",
            purchase_unit="discrete_part",
            role="power_regulation",
            unit_cost_usd=0.42,
            specs={"v_out": 5.0},
            source="parametric",
        )

        node_id = result["node_id"]
        assert result["mpn"] == "MP2459"
        assert result["category"] == "buck_converter"
        item = await twin.graph.get_node(UUID(node_id))
        assert item is not None
        assert item.node_type == NodeType.BOM_ITEM
        assert item.part_number == "MP2459"
        assert item.manufacturer == "Monolithic Power Systems"
        assert item.unit_cost == 0.42
        assert item.specifications["category"] == "buck_converter"
        assert item.specifications["role"] == "power_regulation"
        assert item.specifications["v_out"] == 5.0
        assert item.global_asset_id == "urn:metaforge:bom:Monolithic-Power-Systems:MP2459"

    async def test_stores_media_geometry_fields(self) -> None:
        """Follow-up: image/footprint/CAD/datasheet URLs must be stored on
        the BOMItem, not just accepted and dropped."""
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            datasheet_url="https://example.com/mp2459.pdf",
            image_url="https://example.com/mp2459.png",
            footprint="SOT65P210X110-6N",
            cad_model_url="https://example.com/mp2459.step",
        )

        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.datasheet_url == "https://example.com/mp2459.pdf"
        assert item.image_url == "https://example.com/mp2459.png"
        assert item.footprint == "SOT65P210X110-6N"
        assert item.cad_model_url == "https://example.com/mp2459.step"

    async def test_quantity_defaults_to_one_and_floors_at_one(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)
        r1 = await record(
            mpn="A", manufacturer="Acme", category="resistor", purchase_unit="discrete_part"
        )
        item1 = await twin.graph.get_node(UUID(r1["node_id"]))
        assert item1.quantity == 1

        r2 = await record(
            mpn="B",
            manufacturer="Acme",
            category="resistor",
            purchase_unit="discrete_part",
            quantity=0,
        )
        item2 = await twin.graph.get_node(UUID(r2["node_id"]))
        assert item2.quantity == 1

    async def test_links_project_only_when_given(self) -> None:
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_component_recorder(twin, be)
        r = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            project_id=pid,
        )
        assert r["project_linked"] is True
        assert be.links and be.links[0][0] == pid
        assert be.links[0][3] == "bom_item"

        twin2 = InMemoryTwinAPI.create()
        be2 = _FakeProjectBackend()
        record2 = make_component_recorder(twin2, be2)
        r2 = await record2(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
        )
        assert r2["project_linked"] is False
        assert be2.links == []

    async def test_requires_mpn_manufacturer_category(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)
        with pytest.raises(ValueError, match="mpn"):
            await record(mpn="", manufacturer="M", category="c", purchase_unit="discrete_part")
        with pytest.raises(ValueError, match="manufacturer"):
            await record(mpn="X", manufacturer="", category="c", purchase_unit="discrete_part")
        with pytest.raises(ValueError, match="category"):
            await record(mpn="X", manufacturer="M", category="", purchase_unit="discrete_part")


class TestAdapterHandler:
    async def test_record_component_selection_tool_registered_and_calls_recorder(self) -> None:
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, component_recorder=make_component_recorder(twin, None))
        assert "twin.record_component_selection" in server.tool_ids
        out = await server.record_component_selection(
            {
                "mpn": "MP2459",
                "manufacturer": "MPS",
                "category": "buck_converter",
                "purchase_unit": "discrete_part",
            }
        )
        assert out["node_id"]
        item = await twin.graph.get_node(UUID(out["node_id"]))
        assert item.part_number == "MP2459"

    async def test_handler_passes_through_media_geometry_fields(self) -> None:
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, component_recorder=make_component_recorder(twin, None))
        out = await server.record_component_selection(
            {
                "mpn": "MP2459",
                "manufacturer": "MPS",
                "category": "buck_converter",
                "purchase_unit": "discrete_part",
                "datasheet_url": "https://example.com/mp2459.pdf",
                "image_url": "https://example.com/mp2459.png",
                "footprint": "SOT65P210X110-6N",
                "cad_model_url": "https://example.com/mp2459.step",
            }
        )
        item = await twin.graph.get_node(UUID(out["node_id"]))
        assert item.datasheet_url == "https://example.com/mp2459.pdf"
        assert item.image_url == "https://example.com/mp2459.png"
        assert item.footprint == "SOT65P210X110-6N"
        assert item.cad_model_url == "https://example.com/mp2459.step"

    def test_record_component_selection_absent_without_recorder(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_component_selection" not in server.tool_ids

    async def test_handler_validates_required_fields(self) -> None:
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, component_recorder=make_component_recorder(twin, None))
        with pytest.raises(ValueError, match="mpn"):
            await server.record_component_selection(
                {"manufacturer": "M", "category": "c", "purchase_unit": "discrete_part"}
            )
        with pytest.raises(ValueError, match="purchase_unit"):
            await server.record_component_selection(
                {"mpn": "X", "manufacturer": "M", "category": "c", "purchase_unit": "not_valid"}
            )
