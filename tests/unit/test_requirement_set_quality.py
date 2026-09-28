"""Unit tests for build_requirement_set_quality_report (FORGE-257)."""

from uuid import uuid4

import pytest

from api_gateway.requirement_intelligence.set_quality import build_requirement_set_quality_report
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, EdgeType


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed(twin, project_id, *, name, text, metadata=None) -> Constraint:
    c = Constraint(
        name=name,
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="mechanical",
        source="user",
        project_id=project_id,
        message=text,
        metadata=metadata or {},
    )
    return await twin.create_constraint(c)


class TestPerRequirementRecords:
    async def test_lints_each_real_requirement(self, twin, project_id):
        await _seed(twin, project_id, name="r1", text="The system should be fast.")
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        assert len(report.requirements) == 1
        record = report.requirements[0]
        assert record.name == "r1"
        assert record.clarity == "fail"  # "fast" is an ambiguous word

    async def test_candidate_requirements_are_excluded(self, twin, project_id):
        await _seed(
            twin,
            project_id,
            name="candidate",
            text="draft text",
            metadata={"candidate": True},
        )
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        assert report.requirements == []

    async def test_traceability_true_when_a_trace_edge_exists(self, twin, project_id):
        req = await _seed(twin, project_id, name="r1", text="The system shall weigh at most 2 kg.")
        other = await _seed(twin, project_id, name="parent", text="parent need")
        await twin.add_edge(req.id, other.id, EdgeType.DERIVES_FROM)
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        record = next(r for r in report.requirements if r.name == "r1")
        assert record.traceability == "pass"


class TestConflicts:
    async def test_conflicting_pair_is_reported_and_cross_referenced(self, twin, project_id):
        a = await _seed(twin, project_id, name="a", text="Shall weigh at most 2 kg.")
        b = await _seed(twin, project_id, name="b", text="Shall weigh at least 3 kg.")
        report = await build_requirement_set_quality_report(twin, project_id, "generic")

        assert len(report.conflicts) == 1
        pair = report.conflicts[0]
        assert {pair.aId, pair.bId} == {str(a.id), str(b.id)}

        rec_a = next(r for r in report.requirements if r.id == str(a.id))
        rec_b = next(r for r in report.requirements if r.id == str(b.id))
        assert str(b.id) in rec_a.conflicts
        assert str(a.id) in rec_b.conflicts

    async def test_conflict_persists_as_a_real_graph_edge(self, twin, project_id):
        a = await _seed(twin, project_id, name="a", text="Shall weigh at most 2 kg.")
        b = await _seed(twin, project_id, name="b", text="Shall weigh at least 3 kg.")
        await build_requirement_set_quality_report(twin, project_id, "generic")

        edges = await twin.get_edges(a.id, direction="outgoing", edge_type=EdgeType.CONFLICTS_WITH)
        assert any(e.target_id == b.id for e in edges)

    async def test_repeat_calls_do_not_duplicate_the_edge(self, twin, project_id):
        a = await _seed(twin, project_id, name="a", text="Shall weigh at most 2 kg.")
        b = await _seed(twin, project_id, name="b", text="Shall weigh at least 3 kg.")
        await build_requirement_set_quality_report(twin, project_id, "generic")
        await build_requirement_set_quality_report(twin, project_id, "generic")

        edges = await twin.get_edges(a.id, direction="outgoing", edge_type=EdgeType.CONFLICTS_WITH)
        matching = [e for e in edges if e.target_id == b.id]
        assert len(matching) == 1

    async def test_no_conflicts_when_bounds_are_compatible(self, twin, project_id):
        await _seed(twin, project_id, name="a", text="Shall weigh at most 5 kg.")
        await _seed(twin, project_id, name="b", text="Shall weigh at least 3 kg.")
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        assert report.conflicts == []


class TestCompleteness:
    async def test_completeness_reflects_the_whole_set(self, twin, project_id):
        await _seed(twin, project_id, name="a", text="Shall weigh at most 5 kg.")
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        assert "mechanical" in report.completeness.covered
        assert "safety" in report.completeness.missing

    async def test_product_type_selects_checklist(self, twin, project_id):
        report = await build_requirement_set_quality_report(twin, project_id, "robotic_arm")
        assert report.completeness.productType == "robotic_arm"

    async def test_empty_project_has_no_requirements_but_full_missing_list(self, twin, project_id):
        report = await build_requirement_set_quality_report(twin, project_id, "generic")
        assert report.requirements == []
        assert report.conflicts == []
        assert set(report.completeness.missing) == {
            "safety",
            "mechanical",
            "power",
            "environmental",
            "verification",
        }
