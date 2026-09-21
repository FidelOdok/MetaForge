"""twin.record_claim -- requirement satisfaction claims (FORGE-65, epic
FORGE-35, Phase 6: Evidence Integration).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.claim_recorder import make_claim_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.claims import ClaimStatus, evaluate_claim
from twin_core.consistency.staleness import StalenessEngine
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return str(uuid4())


async def _seed_requirement(twin, project_id, name="leg_safety_factor") -> Constraint:
    return await twin.create_constraint(
        Constraint(
            name=name,
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
            project_id=UUID(project_id),
        )
    )


async def _seed_artefact(twin, project_id, name="leg_v2") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=f"{name}.step",
            content_hash="h",
            format="step",
            created_by="user",
            project_id=UUID(project_id),
        )
    )


class TestRecordClaim:
    async def test_creates_satisfies_edge_by_default(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record = make_claim_recorder(twin)

        out = await record(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            project_id=project_id,
        )
        assert out["requirement_id"] == str(req.id)
        assert out["artefact_id"] == str(artefact.id)
        assert out["claim_type"] == "satisfies"
        assert out["status"] == "unsupported"  # no evidence cited yet

        edges = await twin.get_edges(artefact.id, edge_type=EdgeType.SATISFIES)
        assert len(edges) == 1
        assert edges[0].target_id == req.id
        assert edges[0].metadata["kind"] == "requirement_satisfaction_claim"

    async def test_artefact_ref_resolves_by_work_product_name(self, twin, project_id):
        """The point of FORGE-65's _ref_resolver extension: an artefact is a
        WorkProduct, not a Constraint/EngineeringEntity, and must resolve by
        name too."""
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id, name="unique_artefact_name")
        record = make_claim_recorder(twin)
        out = await record(
            requirement_ref=req.name, artefact_ref="unique_artefact_name", project_id=project_id
        )
        assert out["artefact_id"] == str(artefact.id)

    async def test_unresolvable_requirement_raises(self, twin, project_id):
        artefact = await _seed_artefact(twin, project_id)
        record = make_claim_recorder(twin)
        with pytest.raises(ValueError, match="did not resolve"):
            await record(requirement_ref="nonexistent", artefact_ref=artefact.name)

    async def test_invalid_claim_type_raises(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record = make_claim_recorder(twin)
        with pytest.raises(ValueError, match="claim_type"):
            await record(
                requirement_ref=req.name, artefact_ref=artefact.name, claim_type="not_a_real_edge"
            )

    async def test_evidence_ref_that_is_not_evidence_type_raises(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        other_req = await _seed_requirement(twin, project_id, name="unrelated_req")
        record = make_claim_recorder(twin)
        with pytest.raises(ValueError, match="not an 'evidence'"):
            await record(
                requirement_ref=req.name,
                artefact_ref=artefact.name,
                evidence_refs=[str(other_req.id)],
            )


class TestClaimStatus:
    async def test_claim_with_current_evidence_is_supported(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea"},
            inputs={"a": 1},
            result={"max_von_mises": 100.0},
            project_id=project_id,
        )
        record_claim = make_claim_recorder(twin)
        out = await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[ev["node_id"]],
            project_id=project_id,
        )
        assert out["status"] == "supported"

    async def test_claim_with_stale_evidence_is_unsupported(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea"},
            inputs={"a": 1},
            result={"max_von_mises": 100.0},
            valid_against=[{"ref": req.name, "entity_kind": "constraint"}],
            project_id=project_id,
        )
        await twin.update_constraint(req.id, {"message": "revised"})
        await StalenessEngine(twin).propagate(UUID(project_id), "constraint", req.id)

        record_claim = make_claim_recorder(twin)
        out = await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[ev["node_id"]],
            project_id=project_id,
        )
        assert out["status"] == "unsupported"

    async def test_deleted_evidence_does_not_support_the_claim(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="test",
            producer={"tool": "bench"},
            inputs={},
            result={"pass": True},
            project_id=project_id,
        )
        record_claim = make_claim_recorder(twin)
        await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[ev["node_id"]],
            project_id=project_id,
        )
        await twin.graph.delete_node(UUID(ev["node_id"]))

        result = await evaluate_claim(twin, artefact.id, req.id)
        assert result.status == ClaimStatus.UNSUPPORTED

    async def test_evaluate_claim_raises_when_no_claim_recorded(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        with pytest.raises(ValueError, match="no requirement-satisfaction claim"):
            await evaluate_claim(twin, artefact.id, req.id)

    async def test_status_flips_to_supported_after_revalidation_supersedes(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        stale_ev = await record_evidence(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea"},
            inputs={"a": 1},
            result={"max_von_mises": 100.0},
            valid_against=[{"ref": req.name, "entity_kind": "constraint"}],
            project_id=project_id,
        )
        await twin.update_constraint(req.id, {"message": "revised"})
        await StalenessEngine(twin).propagate(UUID(project_id), "constraint", req.id)

        fresh_ev = await record_evidence(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea"},
            inputs={"a": 1},
            result={"max_von_mises": 105.0},
            supersedes=stale_ev["node_id"],
            project_id=project_id,
        )
        assert fresh_ev["superseded"] == stale_ev["node_id"]

        record_claim = make_claim_recorder(twin)
        out = await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[stale_ev["node_id"], fresh_ev["node_id"]],
            project_id=project_id,
        )
        assert out["status"] == "supported"

        engine = StalenessEngine(twin)
        old_status = await engine.get_status("engineering_entity", UUID(stale_ev["node_id"]))
        assert old_status.value == "superseded"

        edges = await twin.get_edges(UUID(fresh_ev["node_id"]), edge_type=EdgeType.SUPERSEDES)
        assert len(edges) == 1
        assert edges[0].target_id == UUID(stale_ev["node_id"])


class TestAdapterHandler:
    async def test_record_claim_tool_registered_and_calls_recorder(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        server = TwinServer(
            twin=twin, allow_mutations=True, claim_recorder=make_claim_recorder(twin)
        )
        assert "twin.record_claim" in server.tool_ids
        out = await server.record_claim(
            {
                "requirement_ref": req.name,
                "artefact_ref": artefact.name,
                "project_id": project_id,
            }
        )
        assert out["requirement_id"] == str(req.id)

    def test_record_claim_absent_without_recorder(self):
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_claim" not in server.tool_ids

    async def test_handler_validates_required_fields(self, twin, project_id):
        server = TwinServer(twin=twin, claim_recorder=make_claim_recorder(twin))
        with pytest.raises(ValueError, match="requirement_ref"):
            await server.record_claim({"artefact_ref": "x"})
        with pytest.raises(ValueError, match="artefact_ref"):
            await server.record_claim({"requirement_ref": "x"})


def test_claim_status_enum_values() -> None:
    assert ClaimStatus.SUPPORTED.value == "supported"
    assert ClaimStatus.UNSUPPORTED.value == "unsupported"
