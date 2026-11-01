"""twin.record_document — generic text/markdown work products (MET-58X).

Fixes a real gap: the chat agent had no direct way to save a document
(requirements, notes, a spec) and fell back to ``twin.propose_change``, whose
apply-on-approve executor only implements a ``record_decision`` action — any
other diff shape (including one the model invents, e.g. ``create_work_product``)
silently no-ops even after a human approves it (see ``test_proposal_apply.py``).
``document_recorder.py`` already existed (used by the deterministic requirements
run-phase) but was never exposed as an MCP tool the chat agent could call.

Covers the recorder (blob → validated WorkProduct → project link, mirroring
``test_twin_record_decision.py``) and the twin adapter handler that calls it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.document_recorder import make_document_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(
        self, project_id: str, wp_id: str, name: str, link_type: str
    ) -> None:
        self.links.append((project_id, wp_id, name, link_type))


def _patch_blob(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def _store(
        node_id: str, filename: str, content: bytes, *, content_type: str | None = None
    ) -> str:
        if fail:
            raise RuntimeError("minio down")
        calls["node_id"] = node_id
        calls["filename"] = filename
        calls["content"] = content
        calls["content_type"] = content_type
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr("digital_twin.storage.work_product_blobs.store_work_product_blob", _store)
    return calls


class TestRecorder:
    async def test_creates_valid_prd_work_product(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)

        result = await record(
            content="# Requirements\n\nThe widget shall be blue.",
            name="Widget PRD",
            wp_type="prd",
            domain="requirements",
            fmt="md",
            link_type="prd",
            source_tool="twin.record_document",
        )

        node_id = result["node_id"]
        assert result["content_hash"]
        wp = await twin.get_work_product(UUID(node_id))
        assert wp is not None
        assert wp.type == WorkProductType.PRD
        assert wp.domain == "requirements"
        assert wp.format == "md"
        assert wp.metadata["minio_object_key"].endswith(".md")
        assert wp.metadata["original_filename"] == "widget-prd.md"
        assert wp.metadata["authored_by"] == "twin.record_document"

    async def test_links_project_only_when_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_document_recorder(twin, be)
        r = await record(
            content="notes",
            name="D1",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
            project_id=pid,
        )
        assert r["project_linked"] is True
        assert be.links and be.links[0][0] == pid
        assert be.links[0][3] == "documentation"  # link_type passed through

        twin2 = InMemoryTwinAPI.create()
        be2 = _FakeProjectBackend()
        record2 = make_document_recorder(twin2, be2)
        r2 = await record2(
            content="notes",
            name="D2",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
        )
        assert r2["project_linked"] is False
        assert be2.links == []

    async def test_blob_failure_degrades_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch, fail=True)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        r = await record(
            content="notes",
            name="D",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
        )
        assert r["minio_object_key"] is None
        wp = await twin.get_work_product(UUID(r["node_id"]))
        assert wp is not None
        assert wp.content_hash == r["content_hash"]
        assert "minio_object_key" not in wp.metadata

    async def test_source_part_node_ids_creates_parent_of_edges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-241: a robot_description derived from real cad_model parts
        records provenance edges, mirroring robot_description_recorder.py's
        established PARENT_OF pattern for the REST route."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        part = await twin.create_work_product(
            WorkProduct(
                name="Upper Arm Link",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
            )
        )
        record = make_document_recorder(twin, None)
        result = await record(
            content="<robot name='bot' />",
            name="Bot URDF",
            wp_type="robot_description",
            domain="mechanical",
            fmt="urdf",
            link_type="robot_description",
            source_tool="cadquery.export_urdf_assembly",
            source_part_node_ids=[str(part.id)],
        )
        edges = await twin.get_edges(UUID(result["node_id"]))
        assert any(e.target_id == part.id and e.edge_type == EdgeType.PARENT_OF for e in edges)

    async def test_a_bad_source_id_does_not_block_the_commit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        result = await record(
            content="<robot name='bot' />",
            name="Bot URDF",
            wp_type="robot_description",
            domain="mechanical",
            fmt="urdf",
            link_type="robot_description",
            source_tool="cadquery.export_urdf_assembly",
            source_part_node_ids=["00000000-0000-0000-0000-000000000000"],
        )
        assert result["node_id"]  # the commit itself still succeeds

    async def test_source_edge_type_overrides_the_parent_of_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-246: a simulation_result's dependency on its source cad_model
        is 'derives_from' -- source_edge_type lets a caller pick a more
        accurate relation than the generic parent_of default."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        part = await twin.create_work_product(
            WorkProduct(
                name="Bracket",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="cafed00d",
                format="step",
                created_by="test",
            )
        )
        record = make_document_recorder(twin, None)
        result = await record(
            content='{"max_von_mises_mpa": 42.0}',
            name="Bracket FEA Result",
            wp_type="simulation_result",
            domain="mechanical",
            fmt="json",
            link_type="simulation_result",
            source_tool="calculix.extract_results",
            source_part_node_ids=[str(part.id)],
            source_edge_type="derives_from",
        )
        edges = await twin.get_edges(UUID(result["node_id"]))
        assert any(e.target_id == part.id and e.edge_type == EdgeType.DERIVES_FROM for e in edges)

    async def test_evidence_node_id_creates_a_generated_from_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-246: links the evidence EngineeringEntity's claim back to
        the real, structured artifact it's about."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        from twin_core.models.engineering_entity import EngineeringEntity

        evidence = await twin.create_engineering_entity(
            EngineeringEntity(entity_type="evidence", statement="FEA evidence")
        )
        record = make_document_recorder(twin, None)
        result = await record(
            content='{"max_von_mises_mpa": 42.0}',
            name="Bracket FEA Result",
            wp_type="simulation_result",
            domain="mechanical",
            fmt="json",
            link_type="simulation_result",
            source_tool="calculix.extract_results",
            evidence_node_id=str(evidence.id),
        )
        edges = await twin.get_edges(evidence.id)
        assert any(
            e.target_id == UUID(result["node_id"]) and e.edge_type == EdgeType.GENERATED_FROM
            for e in edges
        )

    async def test_a_bad_evidence_node_id_does_not_block_the_commit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        result = await record(
            content='{"max_von_mises_mpa": 42.0}',
            name="Bracket FEA Result",
            wp_type="simulation_result",
            domain="mechanical",
            fmt="json",
            link_type="simulation_result",
            source_tool="calculix.extract_results",
            evidence_node_id="00000000-0000-0000-0000-000000000000",
        )
        assert result["node_id"]  # the commit itself still succeeds

    async def test_requires_name_and_content(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        with pytest.raises(ValueError, match="name"):
            await record(
                content="x",
                name="",
                wp_type="documentation",
                domain="documentation",
                fmt="md",
                link_type="documentation",
                source_tool="t",
            )
        with pytest.raises(ValueError, match="content"):
            await record(
                content="",
                name="x",
                wp_type="documentation",
                domain="documentation",
                fmt="md",
                link_type="documentation",
                source_tool="t",
            )


class TestAdapterHandler:
    async def test_record_document_tool_registered_and_calls_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, allow_mutations=True, document_recorder=make_document_recorder(twin, None)
        )
        assert "twin.record_document" in server.tool_ids
        out = await server.record_document({"name": "N", "content": "body"})
        assert out["node_id"]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        # Defaults to 'documentation' when document_type is omitted.
        assert wp.type == WorkProductType.DOCUMENTATION

    async def test_prd_document_type_sets_requirements_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document({"name": "N", "content": "body", "document_type": "prd"})
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.type == WorkProductType.PRD
        assert wp.domain == "requirements"

    async def test_robot_description_document_type_sets_mechanical_domain_and_urdf_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-241: the fix's core assertion — a robot-description export
        committed through twin.record_document lands as a real
        ROBOT_DESCRIPTION work product, not cad_model."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "6-DOF Arm URDF",
                "content": "<robot name='arm' />",
                "document_type": "robot_description",
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.type == WorkProductType.ROBOT_DESCRIPTION
        assert wp.domain == "mechanical"
        assert wp.format == "urdf"  # default when 'format' is omitted

    async def test_robot_description_honors_explicit_format_and_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "6-DOF Arm SDF",
                "content": "<sdf />",
                "document_type": "robot_description",
                "format": "sdf",
                "metadata": {"robot_name": "arm", "assembly": {"parts": [], "joints": []}},
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.format == "sdf"
        assert wp.metadata["robot_name"] == "arm"
        assert wp.metadata["assembly"] == {"parts": [], "joints": []}

    async def test_robot_description_source_part_node_ids_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        part = await twin.create_work_product(
            WorkProduct(
                name="Base Link",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="cafebabe",
                format="step",
                created_by="test",
            )
        )
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Bot URDF",
                "content": "<robot name='bot' />",
                "document_type": "robot_description",
                "source_part_node_ids": [str(part.id)],
            }
        )
        edges = await twin.get_edges(UUID(out["node_id"]))
        assert any(e.target_id == part.id and e.edge_type == EdgeType.PARENT_OF for e in edges)

    async def test_simulation_result_document_type_sets_mechanical_domain_and_json_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-246: the fix's core assertion -- FEA results committed
        through twin.record_document land as a real SIMULATION_RESULT work
        product, not restated only as text on an evidence entity."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Bracket FEA Result",
                "content": '{"max_von_mises_mpa": 42.0, "max_displacement_mm": 0.8}',
                "document_type": "simulation_result",
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.type == WorkProductType.SIMULATION_RESULT
        assert wp.domain == "mechanical"
        assert wp.format == "json"  # default when 'format' is omitted

    async def test_simulation_result_source_part_node_ids_use_derives_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-246: a simulation_result's dependency on its source
        cad_model is 'derives_from', not the generic 'parent_of' every
        other document_type gets -- unlike robot_description's own test
        for the same source_part_node_ids field, just above."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        part = await twin.create_work_product(
            WorkProduct(
                name="Bracket",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
            )
        )
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Bracket FEA Result",
                "content": '{"max_von_mises_mpa": 42.0}',
                "document_type": "simulation_result",
                "source_part_node_ids": [str(part.id)],
            }
        )
        edges = await twin.get_edges(UUID(out["node_id"]))
        assert any(e.target_id == part.id and e.edge_type == EdgeType.DERIVES_FROM for e in edges)

    async def test_simulation_result_evidence_node_id_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        from twin_core.models.engineering_entity import EngineeringEntity

        evidence = await twin.create_engineering_entity(
            EngineeringEntity(entity_type="evidence", statement="FEA evidence")
        )
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Bracket FEA Result",
                "content": '{"max_von_mises_mpa": 42.0}',
                "document_type": "simulation_result",
                "evidence_node_id": str(evidence.id),
            }
        )
        edges = await twin.get_edges(evidence.id)
        assert any(
            e.target_id == UUID(out["node_id"]) and e.edge_type == EdgeType.GENERATED_FROM
            for e in edges
        )

    async def test_metadata_flattens_summary_fields_for_constraint_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-246: the summary must reach top-level metadata keys (like
        FORGE-100's measured-property flattening), not just the raw JSON
        body -- so a constraint expression can read
        wp.metadata.get('max_von_mises_mpa', 0) directly."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Bracket FEA Result",
                "content": '{"max_von_mises_mpa": 42.0}',
                "document_type": "simulation_result",
                "metadata": {
                    "max_von_mises_mpa": 42.0,
                    "max_displacement_mm": 0.8,
                    "load_case": "static_1g",
                },
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.metadata["max_von_mises_mpa"] == 42.0
        assert wp.metadata["max_displacement_mm"] == 0.8
        assert wp.metadata["load_case"] == "static_1g"

    def test_record_document_absent_without_recorder(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_document" not in server.tool_ids

    async def test_handler_validates_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        with pytest.raises(ValueError, match="name"):
            await server.record_document({"content": "c"})
        with pytest.raises(ValueError, match="content"):
            await server.record_document({"name": "n"})

    async def test_handler_rejects_unknown_document_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        with pytest.raises(ValueError, match="document_type"):
            await server.record_document(
                {"name": "n", "content": "c", "document_type": "create_work_product"}
            )

    async def test_load_case_document_type_sets_mechanical_domain_and_json_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE-278: the fix's core assertion -- a load case committed
        through twin.record_document lands as a real LOAD_CASE work
        product, reusable across design versions instead of being retyped
        inline on every calculix.run_fea call."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Cantilever Static Load",
                "content": '{"fixed_node_set": "Surface1", "load_node_set": "Surface2"}',
                "document_type": "load_case",
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.type == WorkProductType.LOAD_CASE
        assert wp.domain == "mechanical"
        assert wp.format == "json"  # default when 'format' is omitted

    async def test_load_case_metadata_flattens_fields_for_dashboard_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A load case's material/node-set/force fields must reach
        top-level metadata (mirrors simulation_result's own flattening test
        above) so the dashboard's Sim-tab list can render columns without
        fetching each work product's blob."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Cantilever Static Load",
                "content": '{"material": {"name": "steel"}}',
                "document_type": "load_case",
                "metadata": {
                    "material": {"name": "steel"},
                    "fixed_node_set": "Surface1",
                    "load_node_set": "Surface2",
                    "load_force_n": [0, 0, -100],
                    "source_of_loads": "Requirement REQ-12",
                },
            }
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.metadata["material"] == {"name": "steel"}
        assert wp.metadata["fixed_node_set"] == "Surface1"
        assert wp.metadata["load_node_set"] == "Surface2"
        assert wp.metadata["load_force_n"] == [0, 0, -100]
        assert wp.metadata["source_of_loads"] == "Requirement REQ-12"

    async def test_load_case_source_part_node_ids_use_parent_of(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unlike simulation_result, a load_case has no override in
        _DOCUMENT_TYPE_EDGE_TYPE -- it keeps the generic parent_of default,
        linking to the cad_model/mesh it applies to."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        part = await twin.create_work_product(
            WorkProduct(
                name="Bracket",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="deadbeef",
                format="step",
                created_by="test",
            )
        )
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document(
            {
                "name": "Cantilever Static Load",
                "content": "{}",
                "document_type": "load_case",
                "source_part_node_ids": [str(part.id)],
            }
        )
        edges = await twin.get_edges(UUID(out["node_id"]))
        assert any(e.target_id == part.id and e.edge_type == EdgeType.PARENT_OF for e in edges)
