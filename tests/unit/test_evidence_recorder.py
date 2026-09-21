"""twin.record_evidence -- tool-generated Evidence entities (FORGE-64,
epic FORGE-35, Phase 6: Evidence Integration).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.evidence_recorder import make_evidence_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.staleness import StalenessEngine
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity, EdgeType


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, project_id: str, wp_id: str, name: str, wp_type: str) -> None:
        self.links.append((project_id, wp_id, name, wp_type))


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return str(uuid4())


REAL_FEA_RESULT = {
    "max_von_mises": {"leg_joint": 142.3},
    "solver_time": 4.2,
    "mesh_elements": 18432,
}


class TestRecordEvidence:
    async def test_creates_evidence_entity_with_real_result(self, twin, project_id):
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea", "version": "2.20"},
            inputs={"mesh_file": "leg.inp", "load_case": "static_1g"},
            result=REAL_FEA_RESULT,
            project_id=project_id,
        )
        stored = await twin.get_engineering_entity(UUID(out["node_id"]))
        assert stored is not None
        assert stored.entity_type == "evidence"
        assert stored.metadata["evidence_type"] == "simulation"
        assert stored.metadata["producer"] == {"tool": "calculix.run_fea", "version": "2.20"}
        assert stored.metadata["result"] == REAL_FEA_RESULT
        assert stored.metadata["staleness"] == "current"
        assert "execution_timestamp" in stored.metadata

    async def test_result_hash_is_deterministic_and_key_order_independent(self, twin, project_id):
        record = make_evidence_recorder(twin)
        out1 = await record(
            evidence_type="calculation",
            producer={"tool": "torque_analysis"},
            inputs={"a": 1},
            result={"x": 1, "y": 2},
        )
        out2 = await record(
            evidence_type="calculation",
            producer={"tool": "torque_analysis"},
            inputs={"a": 1},
            result={"y": 2, "x": 1},
        )
        assert out1["result_hash"] == out2["result_hash"]

    async def test_invalid_evidence_type_rejected(self, twin, project_id):
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="evidence_type"):
            await record(
                evidence_type="hunch",
                producer={"tool": "x"},
                inputs={},
                result={"a": 1},
            )

    async def test_producer_without_tool_key_rejected(self, twin, project_id):
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="producer"):
            await record(
                evidence_type="calculation",
                producer={"version": "1.0"},
                inputs={},
                result={"a": 1},
            )

    async def test_empty_result_rejected(self, twin, project_id):
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="result"):
            await record(
                evidence_type="calculation",
                producer={"tool": "x"},
                inputs={},
                result={},
            )

    async def test_links_project_only_when_given(self, twin, project_id):
        be = _FakeProjectBackend()
        record = make_evidence_recorder(twin, be)
        out = await record(
            evidence_type="test",
            producer={"tool": "bench"},
            inputs={},
            result={"pass": True},
            project_id=project_id,
        )
        assert out["project_linked"] is True
        assert be.links and be.links[0][0] == project_id


class TestSupportsContradicts:
    async def test_supports_creates_satisfies_edge(self, twin, project_id):
        req = await twin.create_constraint(
            Constraint(
                name="actuator_torque",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=UUID(project_id),
            )
        )
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="calculation",
            producer={"tool": "torque_analysis"},
            inputs={"system_mass_kg": 4.0},
            result={"required_joint_torque_nm": 16.7},
            supports=["actuator_torque"],
            project_id=project_id,
        )
        assert out["supports"] == [str(req.id)]
        edges = await twin.get_edges(UUID(out["node_id"]), edge_type=EdgeType.SATISFIES)
        assert len(edges) == 1
        assert edges[0].target_id == req.id

    async def test_contradicts_creates_conflicts_with_edge(self, twin, project_id):
        req = await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=UUID(project_id),
            )
        )
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="test",
            producer={"tool": "scale"},
            inputs={},
            result={"measured_mass_kg": 6.2},
            contradicts=["mass_budget"],
            project_id=project_id,
        )
        assert out["contradicts"] == [str(req.id)]
        edges = await twin.get_edges(UUID(out["node_id"]), edge_type=EdgeType.CONFLICTS_WITH)
        assert len(edges) == 1
        assert edges[0].target_id == req.id

    async def test_unresolvable_support_ref_raises(self, twin, project_id):
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="did not resolve"):
            await record(
                evidence_type="test",
                producer={"tool": "x"},
                inputs={},
                result={"a": 1},
                supports=["nonexistent"],
            )


class TestValidAgainst:
    async def test_pins_current_revision_when_omitted(self, twin, project_id):
        await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=UUID(project_id),
            )
        )
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"a": 1},
            valid_against=[{"ref": "mass_budget", "entity_kind": "constraint"}],
            project_id=project_id,
        )
        assert out["valid_against_count"] == 1
        status = await StalenessEngine(twin).get_status("engineering_entity", UUID(out["node_id"]))
        assert status.value == "current"

    async def test_explicit_revision_is_respected(self, twin, project_id):
        req = await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=UUID(project_id),
            )
        )
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"a": 1},
            valid_against=[{"ref": str(req.id), "entity_kind": "constraint", "revision": 1}],
            project_id=project_id,
        )
        assert out["valid_against_count"] == 1

    async def test_propagate_marks_this_evidence_stale_when_input_revises(self, twin, project_id):
        """The actual point of valid_against: this evidence goes STALE for
        real when what it was computed against changes -- reusing FORGE-59's
        propagate(), not a parallel mechanism."""
        req = await twin.create_constraint(
            Constraint(
                name="mass_budget",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=UUID(project_id),
            )
        )
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={"mass_budget_kg": 5.0},
            result={"required_joint_torque_nm": 16.7},
            valid_against=[{"ref": "mass_budget", "entity_kind": "constraint"}],
            project_id=project_id,
        )
        await twin.update_constraint(req.id, {"message": "revised"})  # -> revision 2
        engine = StalenessEngine(twin)
        markings = await engine.propagate(UUID(project_id), "constraint", req.id)
        assert any(m.entity_id == UUID(out["node_id"]) for m in markings)
        status = await engine.get_status("engineering_entity", UUID(out["node_id"]))
        assert status.value == "stale"

    async def test_invalid_entity_kind_rejected(self, twin, project_id):
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="entity_kind"):
            await record(
                evidence_type="calculation",
                producer={"tool": "x"},
                inputs={},
                result={"a": 1},
                valid_against=[{"ref": "x", "entity_kind": "work_product"}],
            )


class TestSupersedesRevalidation:
    """FORGE-65: rerunning a stale evidence's procedure records a NEW
    evidence entity that supersedes the old, stale one -- never a mutation
    of the old entity's own result."""

    async def test_supersedes_creates_edge_and_marks_old_superseded(self, twin, project_id):
        record = make_evidence_recorder(twin)
        stale = await record(
            evidence_type="calculation", producer={"tool": "x"}, inputs={}, result={"a": 1}
        )
        fresh = await record(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"a": 2},
            supersedes=stale["node_id"],
        )
        assert fresh["superseded"] == stale["node_id"]

        edges = await twin.get_edges(UUID(fresh["node_id"]), edge_type=EdgeType.SUPERSEDES)
        assert len(edges) == 1
        assert edges[0].target_id == UUID(stale["node_id"])

        engine = StalenessEngine(twin)
        old_status = await engine.get_status("engineering_entity", UUID(stale["node_id"]))
        assert old_status.value == "superseded"
        new_status = await engine.get_status("engineering_entity", UUID(fresh["node_id"]))
        assert new_status.value == "current"

    async def test_no_supersedes_leaves_superseded_none(self, twin, project_id):
        record = make_evidence_recorder(twin)
        out = await record(
            evidence_type="calculation", producer={"tool": "x"}, inputs={}, result={"a": 1}
        )
        assert out["superseded"] is None

    async def test_supersedes_ref_that_is_not_evidence_raises(self, twin, project_id):
        req = await twin.create_constraint(
            Constraint(
                name="not_evidence",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
            )
        )
        record = make_evidence_recorder(twin)
        with pytest.raises(ValueError, match="not an 'evidence'"):
            await record(
                evidence_type="calculation",
                producer={"tool": "x"},
                inputs={},
                result={"a": 1},
                supersedes=str(req.id),
            )


class TestAdapterHandler:
    async def test_record_evidence_tool_registered_and_calls_recorder(self, twin, project_id):
        server = TwinServer(
            twin=twin, allow_mutations=True, evidence_recorder=make_evidence_recorder(twin)
        )
        assert "twin.record_evidence" in server.tool_ids
        out = await server.record_evidence(
            {
                "evidence_type": "calculation",
                "producer": {"tool": "torque_analysis"},
                "inputs": {"mass": 4.0},
                "result": {"torque": 16.7},
            }
        )
        assert out["node_id"]

    def test_record_evidence_absent_without_recorder(self):
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_evidence" not in server.tool_ids

    async def test_handler_passes_through_supersedes(self, twin, project_id):
        server = TwinServer(
            twin=twin, allow_mutations=True, evidence_recorder=make_evidence_recorder(twin)
        )
        stale = await server.record_evidence(
            {
                "evidence_type": "calculation",
                "producer": {"tool": "x"},
                "inputs": {},
                "result": {"a": 1},
            }
        )
        fresh = await server.record_evidence(
            {
                "evidence_type": "calculation",
                "producer": {"tool": "x"},
                "inputs": {},
                "result": {"a": 2},
                "supersedes": stale["node_id"],
            }
        )
        assert fresh["superseded"] == stale["node_id"]

    async def test_handler_validates_required_fields(self, twin, project_id):
        server = TwinServer(twin=twin, evidence_recorder=make_evidence_recorder(twin))
        with pytest.raises(ValueError, match="evidence_type"):
            await server.record_evidence(
                {"producer": {"tool": "x"}, "inputs": {}, "result": {"a": 1}}
            )
        with pytest.raises(ValueError, match="producer"):
            await server.record_evidence(
                {"evidence_type": "test", "inputs": {}, "result": {"a": 1}}
            )
        with pytest.raises(ValueError, match="result"):
            await server.record_evidence(
                {"evidence_type": "test", "producer": {"tool": "x"}, "inputs": {}}
            )


def test_verification_case_entity_type_still_accepted() -> None:
    """Sanity check this file's neighbourhood: verification_case remains a
    valid, freely-recordable EngineeringEntity type independent of this
    recorder (FORGE-64's own ticket scope is Evidence, not
    VerificationCase -- G7 already documents that gap)."""
    entity = EngineeringEntity(entity_type="verification_case", statement="x")
    assert entity.entity_type == "verification_case"
