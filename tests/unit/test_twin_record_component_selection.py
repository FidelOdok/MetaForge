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

from datetime import UTC, datetime
from uuid import UUID

import pytest

from api_gateway.twin.component_recorder import make_component_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, NodeType, WorkProductType


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(
        self, project_id: str, wp_id: str, name: str, link_type: str
    ) -> None:
        self.links.append((project_id, wp_id, name, link_type))


class _FakeRow:
    def __init__(self, **kwargs: object) -> None:
        self.datasheet_url = kwargs.get("datasheet_url", "")
        self.image_url = kwargs.get("image_url", "")
        self.footprint = kwargs.get("footprint", "")
        self.cad_model_url = kwargs.get("cad_model_url", "")
        self.cost_usd = kwargs.get("cost_usd")


class _FakeCatalogStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], _FakeRow] = {}
        self.get_calls: list[tuple[str, str]] = []

    def stub(self, mpn: str, manufacturer: str, **fields: object) -> None:
        self.rows[(mpn, manufacturer)] = _FakeRow(**fields)

    async def get(self, mpn: str, manufacturer: str) -> _FakeRow | None:
        self.get_calls.append((mpn, manufacturer))
        return self.rows.get((mpn, manufacturer))


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

    async def test_auto_fills_media_geometry_and_cost_from_catalog_store(self) -> None:
        """Without this, a caller has to manually copy every field from a
        prior component.search_parametric hit even though the same row is
        already sitting in the catalog under (mpn, manufacturer)."""
        twin = InMemoryTwinAPI.create()
        store = _FakeCatalogStore()
        store.stub(
            "MP2459",
            "MPS",
            datasheet_url="https://example.com/mp2459.pdf",
            image_url="https://example.com/mp2459.png",
            footprint="SOT65P210X110-6N",
            cad_model_url="https://example.com/mp2459.step",
            cost_usd=0.42,
        )
        record = make_component_recorder(twin, None, catalog_store=store)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
        )

        assert store.get_calls == [("MP2459", "MPS")]
        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.datasheet_url == "https://example.com/mp2459.pdf"
        assert item.image_url == "https://example.com/mp2459.png"
        assert item.footprint == "SOT65P210X110-6N"
        assert item.cad_model_url == "https://example.com/mp2459.step"
        assert item.unit_cost == 0.42
        assert item.priced_at is not None  # auto-filled cost still gets a timestamp

    async def test_explicit_fields_win_over_catalog_store(self) -> None:
        twin = InMemoryTwinAPI.create()
        store = _FakeCatalogStore()
        store.stub(
            "MP2459", "MPS", image_url="https://catalog.example.com/wrong.png", cost_usd=0.99
        )
        record = make_component_recorder(twin, None, catalog_store=store)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            image_url="https://caller.example.com/explicit.png",
            unit_cost_usd=0.42,
        )

        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.image_url == "https://caller.example.com/explicit.png"
        assert item.unit_cost == 0.42

    async def test_catalog_lookup_skipped_when_no_store_given(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)  # no catalog_store
        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
        )
        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.image_url is None

    async def test_catalog_miss_leaves_fields_none(self) -> None:
        twin = InMemoryTwinAPI.create()
        store = _FakeCatalogStore()  # nothing stubbed -> get() returns None
        record = make_component_recorder(twin, None, catalog_store=store)
        result = await record(
            mpn="UNKNOWN-MPN",
            manufacturer="Acme",
            category="buck_converter",
            purchase_unit="discrete_part",
        )
        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.image_url is None
        assert item.unit_cost is None

    async def test_purchase_url_and_pricing_provenance_stored(self) -> None:
        """Follow-up: a price is a snapshot, not a fact -- priced_at must be
        auto-captured at record time (never caller-supplied), and
        priced_distributor defaults to the buy-from supplier."""
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)
        before = datetime.now(UTC)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            unit_cost_usd=0.42,
            distributor="DigiKey",
            purchase_url="https://www.digikey.com/en/products/detail/x/497-17363-ND",
        )
        after = datetime.now(UTC)

        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.purchase_url == "https://www.digikey.com/en/products/detail/x/497-17363-ND"
        assert item.price_currency == "USD"
        assert item.priced_distributor == "DigiKey"
        assert item.priced_at is not None
        assert before <= item.priced_at <= after

    async def test_priced_distributor_can_differ_from_supplier(self) -> None:
        """A price captured via a comparison (e.g. resolve_offers) against a
        cheaper source than the buy-from supplier must say so."""
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            unit_cost_usd=0.35,
            distributor="Mouser",
            priced_distributor="DigiKey",
        )

        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.supplier == "Mouser"
        assert item.priced_distributor == "DigiKey"

    async def test_priced_at_none_when_no_cost_given(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
        )

        item = await twin.graph.get_node(UUID(result["node_id"]))
        assert item.priced_at is None
        assert item.purchase_url is None

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

    async def test_links_to_project_bom_work_product_via_real_edge(self) -> None:
        """The orphan gap: a BOMItem with only the Postgres project-junction
        link and no graph edge is flagged by TwinAPI.find_orphans() (BOM_ITEM
        is a "dependent" node type) and unreachable via twin.thread_for. A
        real CONTAINS edge from the project's BOM work product fixes both."""
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_component_recorder(twin, be)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            project_id=pid,
        )

        bom_wp_id = result["bom_work_product_id"]
        assert bom_wp_id is not None
        wp = await twin.get_work_product(UUID(bom_wp_id))
        assert wp is not None
        assert wp.type == WorkProductType.BOM
        assert str(wp.project_id) == pid

        edges = await twin.get_edges(UUID(bom_wp_id), direction="outgoing")
        assert any(
            e.edge_type == EdgeType.CONTAINS and e.target_id == UUID(result["node_id"])
            for e in edges
        )

        orphans = await twin.find_orphans()
        assert UUID(result["node_id"]) not in orphans.orphan_bom_items

    async def test_reuses_the_same_bom_work_product_across_calls(self) -> None:
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_component_recorder(twin, be)

        r1 = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            project_id=pid,
        )
        r2 = await record(
            mpn="TPS62840",
            manufacturer="TI",
            category="buck_converter",
            purchase_unit="discrete_part",
            project_id=pid,
        )

        assert r1["bom_work_product_id"] == r2["bom_work_product_id"]
        # Exactly one "bom" link created (on the first call only), plus one
        # "bom_item" link per recorded selection.
        bom_links = [link_type for *_rest, link_type in be.links if link_type == "bom"]
        assert len(bom_links) == 1

        edges = await twin.get_edges(UUID(r1["bom_work_product_id"]), direction="outgoing")
        linked_targets = {e.target_id for e in edges if e.edge_type == EdgeType.CONTAINS}
        assert linked_targets == {UUID(r1["node_id"]), UUID(r2["node_id"])}

    async def test_no_bom_work_product_when_unscoped(self) -> None:
        """No project_id means no parent work product to attach to -- the
        BOMItem stays an orphan, same as before this fix (unavoidable
        without a project scope)."""
        twin = InMemoryTwinAPI.create()
        record = make_component_recorder(twin, None)

        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
        )

        assert result["bom_work_product_id"] is None
        edges = await twin.get_edges(UUID(result["node_id"]), direction="incoming")
        assert edges == []

    async def test_does_not_reuse_a_document_shaped_bom_work_product(self) -> None:
        """A BOM work product created by the unrelated whole-CSV-blob
        recorder (api_gateway/twin/bom_recorder.py) or the CSV importer has
        no 'kind' marker -- it must never be silently reused as this
        recorder's container, which would conflate two different kinds of
        artifact under one node."""
        from twin_core.models.work_product import WorkProduct

        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        twin = InMemoryTwinAPI.create()

        foreign_bom = await twin.create_work_product(
            WorkProduct(
                name="electronics-bom.csv",
                type=WorkProductType.BOM,
                domain="electronics",
                file_path="",
                content_hash="deadbeef",
                format="csv",
                metadata={"line_items": 3},
                created_by="electronics.record_bom",
                project_id=pid,
            )
        )

        record = make_component_recorder(twin, None)
        result = await record(
            mpn="MP2459",
            manufacturer="MPS",
            category="buck_converter",
            purchase_unit="discrete_part",
            project_id=pid,
        )

        assert result["bom_work_product_id"] is not None
        assert result["bom_work_product_id"] != str(foreign_bom.id)
        new_wp = await twin.get_work_product(UUID(result["bom_work_product_id"]))
        assert new_wp.metadata.get("kind") == "component_selection_container"

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

    async def test_handler_passes_through_purchase_and_pricing_fields(self) -> None:
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, component_recorder=make_component_recorder(twin, None))
        out = await server.record_component_selection(
            {
                "mpn": "MP2459",
                "manufacturer": "MPS",
                "category": "buck_converter",
                "purchase_unit": "discrete_part",
                "unit_cost_usd": 0.42,
                "purchase_url": "https://www.digikey.com/en/products/detail/x/497-17363-ND",
                "price_currency": "EUR",
                "priced_distributor": "DigiKey",
            }
        )
        item = await twin.graph.get_node(UUID(out["node_id"]))
        assert item.purchase_url == "https://www.digikey.com/en/products/detail/x/497-17363-ND"
        assert item.price_currency == "EUR"
        assert item.priced_distributor == "DigiKey"
        assert item.priced_at is not None

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
