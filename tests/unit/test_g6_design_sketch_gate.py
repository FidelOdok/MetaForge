"""Unit tests for the G6 Preliminary Design / Design Sketch Gate evaluator
(FORGE-62)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency import GateCheckStatus, GateStatus, evaluate_g6_design_sketch
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


def _sketch(project_id, approved: bool = False, name: str = "leg sketch") -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.DESIGN_SKETCH,
        domain="mechanical",
        file_path="",
        content_hash="h",
        format="html",
        created_by="user",
        project_id=project_id,
        metadata={"approved": approved, "approved_at": None},
    )


def _architecture(
    project_id, component_count=2, interface_count=1, dangling=None, name="arch"
) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.SYSTEM_ARCHITECTURE,
        domain="systems",
        file_path="",
        content_hash="h",
        format="md",
        created_by="user",
        project_id=project_id,
        metadata={
            "component_count": component_count,
            "interface_count": interface_count,
            "dangling_interfaces": dangling or [],
        },
    )


class TestGeometryLayoutCheck:
    async def test_no_sketch_is_not_evaluated(self, twin, project_id):
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "geometry_layout")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_approved_sketch_passes(self, twin, project_id):
        await twin.create_work_product(_sketch(project_id, approved=True))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "geometry_layout")
        assert check.status == GateCheckStatus.PASS

    async def test_unapproved_sketch_fails(self, twin, project_id):
        await twin.create_work_product(_sketch(project_id, approved=False))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "geometry_layout")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_one_unapproved_among_several_fails(self, twin, project_id):
        await twin.create_work_product(_sketch(project_id, approved=True, name="s1"))
        await twin.create_work_product(_sketch(project_id, approved=False, name="s2"))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "geometry_layout")
        assert check.status == GateCheckStatus.FAIL


class TestComponentsAndInterfacesChecks:
    async def test_no_architecture_recorded_is_not_evaluated(self, twin, project_id):
        result = await evaluate_g6_design_sketch(twin, project_id)
        components = next(c for c in result.checks if c.id == "components")
        interfaces = next(c for c in result.checks if c.id == "major_interfaces")
        assert components.status == GateCheckStatus.NOT_EVALUATED
        assert interfaces.status == GateCheckStatus.NOT_EVALUATED

    async def test_components_present_passes(self, twin, project_id):
        await twin.create_work_product(_architecture(project_id, component_count=3))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "components")
        assert check.status == GateCheckStatus.PASS

    async def test_zero_components_fails(self, twin, project_id):
        await twin.create_work_product(_architecture(project_id, component_count=0))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "components")
        assert check.status == GateCheckStatus.FAIL

    async def test_no_dangling_interfaces_passes(self, twin, project_id):
        await twin.create_work_product(_architecture(project_id, dangling=[]))
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "major_interfaces")
        assert check.status == GateCheckStatus.PASS

    async def test_dangling_interfaces_fails(self, twin, project_id):
        await twin.create_work_product(
            _architecture(project_id, dangling=[{"from": "a", "to": "unknown_part"}])
        )
        result = await evaluate_g6_design_sketch(twin, project_id)
        check = next(c for c in result.checks if c.id == "major_interfaces")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED


class TestNotEvaluatedChecks:
    async def test_mass_power_coverage_manufacturability_are_not_evaluated(self, twin, project_id):
        result = await evaluate_g6_design_sketch(twin, project_id)
        ids = {c.id for c in result.checks}
        for expected in (
            "mass_estimate",
            "power_estimate",
            "requirement_coverage",
            "manufacturability_concerns",
        ):
            assert expected in ids
            check = next(c for c in result.checks if c.id == expected)
            assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_gate_id_is_g6(self, twin, project_id):
        result = await evaluate_g6_design_sketch(twin, project_id)
        assert result.gate_id == "G6"

    async def test_nothing_recorded_is_ready_for_review(self, twin, project_id):
        result = await evaluate_g6_design_sketch(twin, project_id)
        assert result.status == GateStatus.READY_FOR_REVIEW


class TestRiskChecksReused:
    async def test_risk_checks_present(self, twin, project_id):
        from twin_core.models.engineering_entity import EngineeringEntity

        await twin.create_engineering_entity(
            EngineeringEntity(entity_type="risk", title="thermal runaway", project_id=project_id)
        )
        result = await evaluate_g6_design_sketch(twin, project_id)
        risk_checks = [c for c in result.checks if c.id.startswith("risk:")]
        assert len(risk_checks) == 1
        assert risk_checks[0].status == GateCheckStatus.NOT_EVALUATED


class TestEverythingGreenPasses:
    async def test_all_resolved_checks_pass_yields_ready_for_review_not_passed(
        self, twin, project_id
    ):
        """Mass/power/coverage/manufacturability are permanently NOT_EVALUATED
        today, so this gate can never reach PASSED yet -- honest ceiling,
        same posture as G3/G4."""
        await twin.create_work_product(_sketch(project_id, approved=True))
        await twin.create_work_product(_architecture(project_id, component_count=2, dangling=[]))
        result = await evaluate_g6_design_sketch(twin, project_id)
        assert result.status == GateStatus.READY_FOR_REVIEW
        geometry = next(c for c in result.checks if c.id == "geometry_layout")
        components = next(c for c in result.checks if c.id == "components")
        interfaces = next(c for c in result.checks if c.id == "major_interfaces")
        assert geometry.status == GateCheckStatus.PASS
        assert components.status == GateCheckStatus.PASS
        assert interfaces.status == GateCheckStatus.PASS
