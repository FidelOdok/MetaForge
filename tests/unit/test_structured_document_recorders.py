"""Unit tests for the five structured-document recorders (follow-up to
MET-747's CAD-lifecycle mapping: hazard analysis, system architecture,
technical drawing, compliance checklist, procurement record).

Exercises the shared persistence path (single markdown blob -> MinIO,
structured metadata, project link, PARENT_OF edges where applicable) via
each public ``make_X_recorder`` factory, plus the corresponding
``twin.commit_X`` MCP tool registration + handler on ``TwinServer``.
"""

from __future__ import annotations

import pytest

from api_gateway.twin.structured_document_recorder import (
    make_compliance_checklist_recorder,
    make_hazard_analysis_recorder,
    make_procurement_record_recorder,
    make_system_architecture_recorder,
    make_technical_drawing_recorder,
    render_hazard_analysis_markdown,
    render_procurement_record_markdown,
    render_system_architecture_markdown,
)


class _FakeTwin:
    def __init__(self) -> None:
        self.created: list = []
        self.edges: list = []

    async def create_work_product(self, wp):  # type: ignore[no-untyped-def]
        self.created.append(wp)
        return wp

    async def add_edge(self, source_id, target_id, edge_type, metadata=None):  # type: ignore[no-untyped-def]
        self.edges.append((source_id, target_id, edge_type))


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list = []

    async def link_work_product(self, project_id, node_id, name, kind):  # type: ignore[no-untyped-def]
        self.links.append((project_id, node_id, name, kind))


@pytest.fixture()
def patched_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {"calls": []}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        captured["calls"].append({"filename": filename, "content": content, "key": key})
        return key

    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)
    return captured


_HAZARDS = [
    {
        "hazard": "Pinch point",
        "cause": "Exposed gear mesh",
        "effect": "Finger injury",
        "severity": 4,
        "likelihood": 3,
        "mitigation": "Add guard",
    },
    {
        "hazard": "Thermal runaway",
        "cause": "Undersized heatsink",
        "effect": "Fire",
        "severity": 5,
        "likelihood": 2,
        "mitigation": "",
    },
]

_COMPONENTS = [
    {"name": "MCU", "discipline": "electronics", "description": "Main controller"},
    {"name": "Motor Driver", "discipline": "electronics", "description": "H-bridge"},
]
_INTERFACES = [
    {"from": "MCU", "to": "Motor Driver", "interface_type": "SPI", "description": "cmd bus"},
    {"from": "MCU", "to": "Sensor Hub", "interface_type": "I2C", "description": "telemetry"},
]

_DIMENSIONS = [
    {"feature": "bore_dia", "nominal_mm": 8.0, "tolerance_plus_mm": 0.05, "tolerance_minus_mm": 0.0}
]

_LINE_ITEMS = [
    {
        "part_number": "R-1001",
        "description": "10k resistor",
        "quantity": 100,
        "unit_cost": 0.02,
        "currency": "USD",
        "distributor": "digikey",
        "lead_time_days": 5,
    },
    {
        "part_number": "C-2002",
        "description": "100nF cap",
        "quantity": 50,
        "unit_cost": 0.05,
        "currency": "USD",
        "distributor": "mouser",
        "lead_time_days": 10,
    },
]


class TestHazardAnalysisRecorder:
    async def test_render_computes_risk_scores_and_sorts_descending(self) -> None:
        md, meta = render_hazard_analysis_markdown("Quadruped Leg", "Quadruped Leg", _HAZARDS)
        assert "Hazard Analysis: Quadruped Leg" in md
        assert meta["hazard_count"] == 2
        assert meta["highest_risk_score"] == 12  # 4*3
        assert meta["unmitigated_count"] == 1
        # Higher risk score (12) sorts before the lower one (10)
        assert md.index("Pinch point") < md.index("Thermal runaway")

    async def test_commit_persists_work_product(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        projects = _FakeProjectBackend()
        commit = make_hazard_analysis_recorder(twin, projects)
        result = await commit(
            name="Quadruped Leg Hazards",
            system_name="Quadruped Leg",
            hazards=_HAZARDS,
            project_id="11111111-1111-1111-1111-111111111111",
        )
        assert len(twin.created) == 1
        wp = twin.created[0]
        assert wp.type == WorkProductType.HAZARD_ANALYSIS
        assert wp.format == "md"
        assert wp.metadata["hazard_count"] == 2
        assert result["project_linked"] is True
        assert projects.links[0][3] == "hazard_analysis"

    async def test_missing_name_raises(self) -> None:
        commit = make_hazard_analysis_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="name"):
            await commit(name="", system_name="x", hazards=_HAZARDS)

    async def test_empty_hazards_raises(self) -> None:
        commit = make_hazard_analysis_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="hazards"):
            await commit(name="x", system_name="x", hazards=[])


class TestSystemArchitectureRecorder:
    async def test_render_flags_dangling_interfaces(self) -> None:
        md, meta = render_system_architecture_markdown(
            "Drone Arch", "Drone", _COMPONENTS, _INTERFACES
        )
        assert "```mermaid" in md
        assert meta["component_count"] == 2
        assert meta["interface_count"] == 2
        # "Sensor Hub" isn't a declared component -> dangling
        assert len(meta["dangling_interfaces"]) == 1
        assert meta["dangling_interfaces"][0]["to"] == "Sensor Hub"

    async def test_commit_persists_work_product(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        commit = make_system_architecture_recorder(twin, None)
        result = await commit(name="Drone Arch", system_name="Drone", components=_COMPONENTS)
        assert twin.created[0].type == WorkProductType.SYSTEM_ARCHITECTURE
        assert result["node_id"]

    async def test_empty_components_raises(self) -> None:
        commit = make_system_architecture_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="components"):
            await commit(name="x", system_name="x", components=[])


class TestTechnicalDrawingRecorder:
    async def test_commit_persists_and_links_source_cad(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        commit = make_technical_drawing_recorder(twin, None)
        result = await commit(
            name="Bracket Drawing",
            part_name="Bracket",
            dimensions=_DIMENSIONS,
            gdt_callouts=[
                {
                    "feature": "bore_dia",
                    "symbol": "⌖",
                    "tolerance_value_mm": 0.02,
                    "datum_refs": ["A"],
                }
            ],
            surface_finishes=[{"feature": "bore_dia", "ra_um": 1.6}],
            inspection_requirements=["CMM bore check"],
            source_node_ids=["aaaaaaaa-0000-0000-0000-000000000001"],
        )
        assert twin.created[0].type == WorkProductType.TECHNICAL_DRAWING
        assert twin.created[0].metadata["dimension_count"] == 1
        assert len(twin.edges) == 1
        assert result["node_id"]

    async def test_empty_dimensions_raises(self) -> None:
        commit = make_technical_drawing_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="dimensions"):
            await commit(name="x", part_name="x", dimensions=[])


class TestComplianceChecklistRecorder:
    async def test_commit_persists_work_product(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        commit = make_compliance_checklist_recorder(twin, None)
        items = [
            {
                "regime": "fcc",
                "category": "emc",
                "requirement": "Part 15B",
                "standard": "47 CFR 15",
                "evidence_type": "test_report",
                "evidence_status": "missing",
            }
        ]
        result = await commit(
            name="Drone Checklist", target_markets=["fcc"], items=items, coverage_percent=50.0
        )
        wp = twin.created[0]
        assert wp.type == WorkProductType.COMPLIANCE_CHECKLIST
        assert wp.metadata["total_items"] == 1
        assert wp.metadata["coverage_percent"] == 50.0
        assert result["node_id"]

    async def test_empty_items_raises(self) -> None:
        commit = make_compliance_checklist_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="items"):
            await commit(name="x", target_markets=[], items=[])


class TestProcurementRecordRecorder:
    async def test_render_computes_totals(self) -> None:
        md, meta = render_procurement_record_markdown("PO-1", _LINE_ITEMS, "")
        assert meta["line_item_count"] == 2
        assert meta["total_cost"] == pytest.approx(100 * 0.02 + 50 * 0.05)
        assert meta["currency"] == "USD"
        assert meta["max_lead_time_days"] == 10
        assert "PO-1" in md

    async def test_mixed_currency_raises(self) -> None:
        mixed = [*_LINE_ITEMS, {**_LINE_ITEMS[0], "currency": "EUR"}]
        with pytest.raises(ValueError, match="mixed currencies"):
            render_procurement_record_markdown("PO-2", mixed, "")

    async def test_commit_persists_and_links_bom(self, patched_blob_store: dict) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        commit = make_procurement_record_recorder(twin, None)
        result = await commit(
            name="PO-1",
            line_items=_LINE_ITEMS,
            source_node_ids=["bbbbbbbb-0000-0000-0000-000000000002"],
        )
        assert twin.created[0].type == WorkProductType.PROCUREMENT_RECORD
        assert len(twin.edges) == 1
        assert result["node_id"]

    async def test_empty_line_items_raises(self) -> None:
        commit = make_procurement_record_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="line_items"):
            await commit(name="x", line_items=[])


# ---------------------------------------------------------------------------
# MCP adapter registration + handlers
# ---------------------------------------------------------------------------


class TestStructuredDocumentAdapterTools:
    @staticmethod
    def _patch_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )

    async def test_all_five_tools_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin,
            hazard_analysis_recorder=make_hazard_analysis_recorder(twin, None),
            system_architecture_recorder=make_system_architecture_recorder(twin, None),
            technical_drawing_recorder=make_technical_drawing_recorder(twin, None),
            compliance_checklist_recorder=make_compliance_checklist_recorder(twin, None),
            procurement_record_recorder=make_procurement_record_recorder(twin, None),
        )
        assert {
            "twin.commit_hazard_analysis",
            "twin.commit_system_architecture",
            "twin.commit_technical_drawing",
            "twin.commit_compliance_checklist",
            "twin.commit_procurement_record",
        } <= set(server.tool_ids)

    def test_tools_absent_without_recorders(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        server = TwinServer(twin=InMemoryTwinAPI.create())
        for tool_id in [
            "twin.commit_hazard_analysis",
            "twin.commit_system_architecture",
            "twin.commit_technical_drawing",
            "twin.commit_compliance_checklist",
            "twin.commit_procurement_record",
        ]:
            assert tool_id not in server.tool_ids

    async def test_commit_hazard_analysis_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from uuid import UUID as _UUID

        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI
        from twin_core.models.enums import WorkProductType

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        recorder = make_hazard_analysis_recorder(twin, None)
        server = TwinServer(twin=twin, hazard_analysis_recorder=recorder)
        out = await server.commit_hazard_analysis(
            {"name": "Hazards", "system_name": "Sys", "hazards": _HAZARDS}
        )
        wp = await twin.get_work_product(_UUID(out["node_id"]))
        assert wp is not None
        assert wp.type == WorkProductType.HAZARD_ANALYSIS

    async def test_commit_hazard_analysis_validates_required_fields(self) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        recorder = make_hazard_analysis_recorder(twin, None)
        server = TwinServer(twin=twin, hazard_analysis_recorder=recorder)
        with pytest.raises(ValueError, match="name"):
            await server.commit_hazard_analysis({"hazards": _HAZARDS})
        with pytest.raises(ValueError, match="hazards"):
            await server.commit_hazard_analysis({"name": "x"})

    async def test_commit_system_architecture_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, system_architecture_recorder=make_system_architecture_recorder(twin, None)
        )
        out = await server.commit_system_architecture(
            {"name": "Arch", "components": _COMPONENTS, "interfaces": _INTERFACES}
        )
        assert out["node_id"]

    async def test_commit_technical_drawing_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, technical_drawing_recorder=make_technical_drawing_recorder(twin, None)
        )
        out = await server.commit_technical_drawing(
            {"name": "Drawing", "part_name": "Bracket", "dimensions": _DIMENSIONS}
        )
        assert out["node_id"]

    async def test_commit_compliance_checklist_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, compliance_checklist_recorder=make_compliance_checklist_recorder(twin, None)
        )
        out = await server.commit_compliance_checklist(
            {
                "name": "Checklist",
                "target_markets": ["fcc"],
                "items": [
                    {
                        "regime": "fcc",
                        "category": "emc",
                        "requirement": "Part 15B",
                        "standard": "47 CFR 15",
                        "evidence_type": "test_report",
                        "evidence_status": "missing",
                    }
                ],
            }
        )
        assert out["node_id"]

    async def test_commit_procurement_record_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        self._patch_blobs(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, procurement_record_recorder=make_procurement_record_recorder(twin, None)
        )
        out = await server.commit_procurement_record({"name": "PO-1", "line_items": _LINE_ITEMS})
        assert out["node_id"]
