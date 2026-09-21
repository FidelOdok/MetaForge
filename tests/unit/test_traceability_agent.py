"""Unit tests for TraceabilityAgent (FORGE-56)."""

from uuid import uuid4

import pytest

from api_gateway.requirement_intelligence.traceability import TraceabilityAgent
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def agent(twin):
    return TraceabilityAgent(twin)


async def _seed_need(twin, project_id, statement="need it") -> EngineeringEntity:
    e = EngineeringEntity(
        entity_type="stakeholder_need", statement=statement, project_id=project_id
    )
    return await twin.create_engineering_entity(e)


async def _seed_requirement(
    twin, project_id, *, name="req", rationale="x", source="user", verification_method="test"
) -> Constraint:
    metadata = {}
    if rationale:
        metadata["rationale"] = rationale
    if verification_method:
        metadata["verification_method"] = verification_method
    c = Constraint(
        name=name,
        expression="m<=3",
        severity=ConstraintSeverity.ERROR,
        domain="mech",
        source=source,
        project_id=project_id,
        metadata=metadata,
    )
    return await twin.create_constraint(c)


class TestRequirementWithoutParent:
    async def test_flags_requirement_with_no_outgoing_trace_edge(self, twin, project_id, agent):
        req = await _seed_requirement(twin, project_id)
        result = await agent.check(str(project_id))
        assert any("requirement_without_parent" in c and req.name in c for c in result.conclusions)

    async def test_does_not_flag_requirement_with_a_trace_edge(self, twin, project_id, agent):
        need = await _seed_need(twin, project_id)
        req = await _seed_requirement(twin, project_id)
        await twin.add_edge(req.id, need.id, EdgeType.DERIVES_FROM)
        result = await agent.check(str(project_id))
        assert not any("requirement_without_parent" in c for c in result.conclusions)

    async def test_candidate_constraints_are_not_checked(self, twin, project_id, agent):
        c = Constraint(
            name="candidate_req",
            expression="True",
            severity=ConstraintSeverity.INFO,
            domain="candidate",
            source="requirement_author_agent",
            project_id=project_id,
            metadata={"candidate": True},
        )
        await twin.create_constraint(c)
        result = await agent.check(str(project_id))
        assert not any("candidate_req" in c for c in result.conclusions)


class TestRequirementWithoutRationaleOwnerVerification:
    async def test_flags_missing_rationale(self, twin, project_id, agent):
        await _seed_requirement(twin, project_id, rationale=None)
        result = await agent.check(str(project_id))
        assert any("requirement_without_rationale" in c for c in result.conclusions)

    async def test_flags_missing_owner(self, twin, project_id, agent):
        await _seed_requirement(twin, project_id, source="")
        result = await agent.check(str(project_id))
        assert any("requirement_without_owner" in c for c in result.conclusions)

    async def test_flags_missing_verification(self, twin, project_id, agent):
        await _seed_requirement(twin, project_id, verification_method=None)
        result = await agent.check(str(project_id))
        assert any("requirement_without_verification" in c for c in result.conclusions)

    async def test_complete_requirement_flags_nothing_but_parent(self, twin, project_id, agent):
        need = await _seed_need(twin, project_id)
        req = await _seed_requirement(twin, project_id)
        await twin.add_edge(req.id, need.id, EdgeType.DERIVES_FROM)
        result = await agent.check(str(project_id))
        assert not any(req.name in c for c in result.conclusions if "requirement" in c)


class TestNeedWithoutRequirement:
    async def test_flags_unlinked_need(self, twin, project_id, agent):
        await _seed_need(twin, project_id, statement="unlinked need")
        result = await agent.check(str(project_id))
        assert any("need_without_requirement" in c for c in result.conclusions)

    async def test_does_not_flag_linked_need(self, twin, project_id, agent):
        need = await _seed_need(twin, project_id)
        req = await _seed_requirement(twin, project_id)
        await twin.add_edge(req.id, need.id, EdgeType.DERIVES_FROM)
        result = await agent.check(str(project_id))
        assert not any("need_without_requirement" in c for c in result.conclusions)


class TestVerificationWithoutRequirement:
    async def test_flags_verification_with_no_edges(self, twin, project_id, agent):
        v = EngineeringEntity(
            entity_type="verification_case", statement="test procedure", project_id=project_id
        )
        await twin.create_engineering_entity(v)
        result = await agent.check(str(project_id))
        assert any("verification_without_requirement" in c for c in result.conclusions)

    async def test_does_not_flag_verification_with_an_edge(self, twin, project_id, agent):
        req = await _seed_requirement(twin, project_id)
        v = EngineeringEntity(
            entity_type="verification_case", statement="test procedure", project_id=project_id
        )
        created_v = await twin.create_engineering_entity(v)
        await twin.add_edge(created_v.id, req.id, EdgeType.VERIFIED_BY)
        result = await agent.check(str(project_id))
        assert not any("verification_without_requirement" in c for c in result.conclusions)


class TestArtefactWithoutRequirement:
    async def test_flags_work_product_with_no_constrained_by_edge(self, twin, project_id, agent):
        wp = WorkProduct(
            name="unbound_model",
            type=WorkProductType.CAD_MODEL,
            domain="mech",
            file_path="x.step",
            content_hash="h",
            format="step",
            created_by="user",
            project_id=project_id,
        )
        await twin.create_work_product(wp)
        result = await agent.check(str(project_id))
        assert any("artefact_without_requirement_justification" in c for c in result.conclusions)

    async def test_does_not_flag_bound_work_product(self, twin, project_id, agent):
        wp = WorkProduct(
            name="bound_model",
            type=WorkProductType.CAD_MODEL,
            domain="mech",
            file_path="x.step",
            content_hash="h",
            format="step",
            created_by="user",
            project_id=project_id,
        )
        created_wp = await twin.create_work_product(wp)
        req = await _seed_requirement(twin, project_id)
        await twin.add_edge(req.id, created_wp.id, EdgeType.CONSTRAINED_BY)
        result = await agent.check(str(project_id))
        assert not any(
            "artefact_without_requirement_justification" in c for c in result.conclusions
        )


class TestEvidenceWithoutProvenance:
    async def test_flags_evidence_with_no_source_refs(self, twin, project_id, agent):
        ev = EngineeringEntity(
            entity_type="evidence", statement="fea run result", project_id=project_id
        )
        await twin.create_engineering_entity(ev)
        result = await agent.check(str(project_id))
        assert any("evidence_without_provenance" in c for c in result.conclusions)

    async def test_does_not_flag_evidence_with_source_refs(self, twin, project_id, agent):
        ev = EngineeringEntity(
            entity_type="evidence",
            statement="fea run result",
            project_id=project_id,
            source_refs=["work_product:abc123"],
        )
        await twin.create_engineering_entity(ev)
        result = await agent.check(str(project_id))
        assert not any("evidence_without_provenance" in c for c in result.conclusions)


class TestCoverageMetrics:
    async def test_no_issues_message_when_graph_is_clean(self, twin, project_id, agent):
        result = await agent.check(str(project_id))
        assert result.conclusions == ["no traceability gaps found"]

    async def test_coverage_is_none_for_empty_sets(self, twin, project_id, agent):
        result = await agent.check(str(project_id))
        evidence_str = result.evidence[0]
        assert "'needs_to_requirements': None" in evidence_str

    async def test_needs_to_requirements_coverage_is_100_when_fully_linked(
        self, twin, project_id, agent
    ):
        need = await _seed_need(twin, project_id)
        req = await _seed_requirement(twin, project_id)
        await twin.add_edge(req.id, need.id, EdgeType.DERIVES_FROM)
        result = await agent.check(str(project_id))
        assert "'needs_to_requirements': 100.0" in result.evidence[0]

    async def test_needs_to_requirements_coverage_is_0_when_unlinked(self, twin, project_id, agent):
        await _seed_need(twin, project_id)
        result = await agent.check(str(project_id))
        assert "'needs_to_requirements': 0.0" in result.evidence[0]

    async def test_requirements_to_architecture_coverage(self, twin, project_id, agent):
        req = await _seed_requirement(twin, project_id)
        arch = WorkProduct(
            name="system_arch",
            type=WorkProductType.SYSTEM_ARCHITECTURE,
            domain="systems",
            file_path="arch.md",
            content_hash="h",
            format="md",
            created_by="user",
            project_id=project_id,
        )
        created_arch = await twin.create_work_product(arch)
        await twin.add_edge(req.id, created_arch.id, EdgeType.CONSTRAINED_BY)
        result = await agent.check(str(project_id))
        assert "'requirements_to_architecture': 100.0" in result.evidence[0]

    async def test_critical_requirements_to_evidence_coverage(self, twin, project_id, agent):
        req = await _seed_requirement(twin, project_id)  # severity ERROR by default
        ev = EngineeringEntity(entity_type="evidence", statement="proof", project_id=project_id)
        created_ev = await twin.create_engineering_entity(ev)
        await twin.add_edge(req.id, created_ev.id, EdgeType.VERIFIED_BY)
        result = await agent.check(str(project_id))
        assert "'critical_requirements_to_evidence': 100.0" in result.evidence[0]


class TestNeverProposesAPatch:
    async def test_proposed_patch_is_always_none(self, twin, project_id, agent):
        await _seed_requirement(twin, project_id)
        result = await agent.check(str(project_id))
        assert result.proposed_patch is None
