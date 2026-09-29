"""Unit tests for the wall-thickness optimiser (FORGE-320): pure math
(twin_core.prediction.optimizer), the new material yield table, the
orchestration layer (api_gateway.twin.optimizer), and MCP tool
registration."""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.optimizer import make_wall_thickness_optimizer
from tool_registry.tools.cadquery.materials import MATERIAL_YIELD_MPA, resolve_yield_mpa
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct
from twin_core.prediction.optimizer import (
    cantilever_max_bending_stress_mpa,
    optimize_tube_height,
    optimize_wall_thickness,
)

_GEOM = dict(length_mm=180.0, width_mm=60.0, height_mm=40.0)
_ALUMINUM = dict(youngs_modulus_mpa=68900.0, density_kg_m3=2700.0, yield_mpa=276.0)


class TestCantileverMaxBendingStress:
    def test_known_values(self) -> None:
        # M = 10N * 100mm = 1000 N.mm; c = 20mm/2 = 10mm; stress = M*c/I
        stress = cantilever_max_bending_stress_mpa(
            length_mm=100.0, load_n=10.0, height_mm=20.0, moment_of_inertia_mm4=1000.0
        )
        assert stress == pytest.approx(10.0)

    def test_rejects_non_positive_inputs(self) -> None:
        with pytest.raises(ValueError, match="length_mm/height_mm"):
            cantilever_max_bending_stress_mpa(
                length_mm=0.0, load_n=10.0, height_mm=20.0, moment_of_inertia_mm4=1000.0
            )
        with pytest.raises(ValueError, match="moment_of_inertia_mm4"):
            cantilever_max_bending_stress_mpa(
                length_mm=100.0, load_n=10.0, height_mm=20.0, moment_of_inertia_mm4=0.0
            )


class TestOptimizeWallThickness:
    def test_finds_optimal_minimum_feasible_wall(self) -> None:
        # A heavy-enough load that wall_min alone doesn't already satisfy
        # both constraints -- otherwise status would be
        # already_feasible_at_min, not optimal (see the next few tests).
        result = optimize_wall_thickness(
            **_GEOM,
            **_ALUMINUM,
            load_n=800.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
        )
        assert result.status == "optimal"
        assert result.winner is not None
        assert result.winner.feasible is True
        assert result.winner.deflection_margin_mm >= 0
        assert result.winner.sf_margin >= 0
        assert 0.5 <= result.winner.wall_thickness_mm <= 19.5
        assert len(result.candidates) >= 2

    def test_infeasible_when_limit_unachievable(self) -> None:
        result = optimize_wall_thickness(
            **_GEOM,
            **_ALUMINUM,
            load_n=20.0,
            deflection_limit_mm=0.0001,  # unachievable even at max wall
            sf_limit=2.0,
        )
        assert result.status == "infeasible"
        assert result.winner is None
        assert "even the maximum allowed wall thickness" in result.detail

    def test_already_feasible_at_min_when_constraints_are_loose(self) -> None:
        result = optimize_wall_thickness(
            **_GEOM,
            **_ALUMINUM,
            load_n=20.0,
            deflection_limit_mm=1000.0,
            sf_limit=0.001,
            wall_min_mm=2.0,
        )
        assert result.status == "already_feasible_at_min"
        assert result.winner is not None
        assert result.winner.wall_thickness_mm == 2.0

    def test_winner_mass_increases_with_stricter_constraints(self) -> None:
        loose = optimize_wall_thickness(
            **_GEOM, **_ALUMINUM, load_n=1500.0, deflection_limit_mm=2.0, sf_limit=1.5
        )
        strict = optimize_wall_thickness(
            **_GEOM, **_ALUMINUM, load_n=1500.0, deflection_limit_mm=0.3, sf_limit=3.0
        )
        assert loose.winner is not None
        assert strict.winner is not None
        assert strict.winner.mass_kg > loose.winner.mass_kg

    def test_rejects_degenerate_wall_bounds(self) -> None:
        with pytest.raises(ValueError, match="wall_max_mm"):
            optimize_wall_thickness(
                length_mm=180.0,
                width_mm=1.0,
                height_mm=1.0,
                **_ALUMINUM,
                load_n=20.0,
                deflection_limit_mm=0.5,
            )


class TestOptimizeTubeHeight:
    """FORGE-288 (gap G-G2): a second, genuinely different parameter over
    the SAME real physics as optimize_wall_thickness -- proof the design
    loop's own candidate ingestion generalized beyond wall_thickness_mm,
    not a renamed copy of the same search."""

    def test_finds_optimal_minimum_feasible_height(self) -> None:
        result = optimize_tube_height(
            length_mm=180.0,
            width_mm=60.0,
            wall_thickness_mm=2.0,
            **_ALUMINUM,
            load_n=800.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            height_min_mm=5.0,
        )
        assert result.status == "optimal"
        assert result.winner is not None
        assert result.winner.feasible is True
        assert result.winner.deflection_margin_mm >= 0
        assert result.winner.sf_margin >= 0
        assert 5.0 <= result.winner.height_mm <= 240.0
        assert len(result.candidates) >= 2

    def test_infeasible_when_limit_unachievable_within_bounds(self) -> None:
        result = optimize_tube_height(
            length_mm=180.0,
            width_mm=60.0,
            wall_thickness_mm=2.0,
            **_ALUMINUM,
            load_n=5000.0,
            deflection_limit_mm=0.001,
            sf_limit=2.0,
            height_min_mm=5.0,
            height_max_mm=50.0,
        )
        assert result.status == "infeasible"
        assert result.winner is None
        assert "even the maximum allowed height" in result.detail

    def test_already_feasible_at_min_when_constraints_are_loose(self) -> None:
        result = optimize_tube_height(
            length_mm=180.0,
            width_mm=60.0,
            wall_thickness_mm=2.0,
            **_ALUMINUM,
            load_n=20.0,
            deflection_limit_mm=1000.0,
            sf_limit=0.001,
            height_min_mm=5.0,
        )
        assert result.status == "already_feasible_at_min"
        assert result.winner is not None
        assert result.winner.height_mm == 5.0

    def test_winner_mass_increases_with_stricter_constraints(self) -> None:
        loose = optimize_tube_height(
            length_mm=180.0,
            width_mm=60.0,
            wall_thickness_mm=2.0,
            **_ALUMINUM,
            load_n=800.0,
            deflection_limit_mm=2.0,
            sf_limit=1.5,
            height_min_mm=5.0,
        )
        strict = optimize_tube_height(
            length_mm=180.0,
            width_mm=60.0,
            wall_thickness_mm=2.0,
            **_ALUMINUM,
            load_n=800.0,
            deflection_limit_mm=0.3,
            sf_limit=3.0,
            height_min_mm=5.0,
        )
        assert loose.winner is not None
        assert strict.winner is not None
        assert strict.winner.mass_kg > loose.winner.mass_kg

    def test_rejects_height_min_that_leaves_no_cavity(self) -> None:
        with pytest.raises(ValueError, match="real cavity"):
            optimize_tube_height(
                length_mm=180.0,
                width_mm=60.0,
                wall_thickness_mm=2.0,
                **_ALUMINUM,
                load_n=20.0,
                deflection_limit_mm=0.5,
                height_min_mm=1.0,
            )

    def test_rejects_degenerate_height_bounds(self) -> None:
        with pytest.raises(ValueError, match="height_max_mm"):
            optimize_tube_height(
                length_mm=180.0,
                width_mm=60.0,
                wall_thickness_mm=2.0,
                **_ALUMINUM,
                load_n=20.0,
                deflection_limit_mm=0.5,
                height_min_mm=10.0,
                height_max_mm=5.0,
            )


class TestMaterialYieldTable:
    def test_known_material_resolves(self) -> None:
        assert resolve_yield_mpa("aluminum_6061") == MATERIAL_YIELD_MPA["aluminum_6061"]

    def test_name_normalization(self) -> None:
        assert resolve_yield_mpa("Aluminum 6061") == resolve_yield_mpa("aluminum_6061")

    def test_explicit_override_wins(self) -> None:
        assert resolve_yield_mpa("steel", yield_mpa=999.0) == 999.0

    def test_unknown_material_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown material"):
            resolve_yield_mpa("unobtainium")

    def test_no_material_and_no_override_raises(self) -> None:
        with pytest.raises(ValueError, match="provide either"):
            resolve_yield_mpa()

    def test_same_material_set_as_elastic_table(self) -> None:
        from tool_registry.tools.cadquery.materials import MATERIAL_ELASTIC_MPA

        assert set(MATERIAL_YIELD_MPA) == set(MATERIAL_ELASTIC_MPA)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _seed_cad(twin: InMemoryTwinAPI, name: str = "upper_arm") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            metadata={
                "geometry_features": {
                    "properties": {
                        "bounding_box": {
                            "min_x": -180,
                            "max_x": 180,
                            "min_y": -20,
                            "max_y": 20,
                            "min_z": -30,
                            "max_z": 30,
                        }
                    }
                }
            },
        )
    )


class TestMakeWallThicknessOptimizer:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        optimize = make_wall_thickness_optimizer(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await optimize(
                work_product_id="11111111-1111-1111-1111-111111111111",
                load_n=20.0,
                deflection_limit_mm=0.5,
            )

    async def test_optimal_result_against_real_geometry(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        optimize = make_wall_thickness_optimizer(twin)
        out = await optimize(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert out["status"] == "optimal"
        assert out["winner"] is not None
        assert out["material"] == "aluminum_6061"

    async def test_records_evidence_pinned_to_work_product(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        evidence_recorder = make_evidence_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, evidence_recorder=evidence_recorder)
        out = await optimize(
            work_product_id=str(wp.id), load_n=20.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert "evidence_node_id" in out
        stored = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert stored is not None
        assert stored.metadata["evidence_type"] == "calculation"
        assert stored.metadata["depends_on"][0]["entity_id"] == str(wp.id)

    async def test_records_decision_with_alternatives_when_feasible(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, decision_recorder=decision_recorder)
        out = await optimize(
            work_product_id=str(wp.id), load_n=20.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert "decision_node_id" in out
        decision_wp = await twin.get_work_product(UUID(out["decision_node_id"]))
        assert decision_wp is not None
        assert decision_wp.type == WorkProductType.DESIGN_DECISION
        assert isinstance(decision_wp.metadata["alternatives"], list)

    async def test_no_decision_recorded_when_infeasible(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, decision_recorder=decision_recorder)
        out = await optimize(
            work_product_id=str(wp.id),
            load_n=20.0,
            deflection_limit_mm=0.0001,
            sf_limit=2.0,
        )
        assert out["status"] == "infeasible"
        assert "decision_node_id" not in out

    async def test_record_decision_false_skips_decision(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, decision_recorder=decision_recorder)
        out = await optimize(
            work_product_id=str(wp.id),
            load_n=100.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            record_decision=False,
        )
        assert out["status"] == "optimal"
        assert "decision_node_id" not in out

    async def test_links_decision_to_requirement_ids(self, twin: InMemoryTwinAPI) -> None:
        from twin_core.models.constraint import Constraint
        from twin_core.models.enums import ConstraintSeverity

        wp = await _seed_cad(twin)
        req = await twin.create_constraint(
            Constraint(
                name="tip_deflection",
                expression="True",
                message="<= 0.5mm",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
            )
        )
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, decision_recorder=decision_recorder)
        out = await optimize(
            work_product_id=str(wp.id),
            load_n=20.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            requirement_ids=[str(req.id)],
        )
        decision_wp = await twin.get_work_product(UUID(out["decision_node_id"]))
        assert decision_wp is not None
        assert decision_wp.metadata["parent_refs"] == [str(req.id)]

    async def test_links_decision_to_its_own_evidence(self, twin: InMemoryTwinAPI) -> None:
        """FORGE-289 (gap G-G3): when both recorders are wired, the Decision
        must link to the Evidence THIS SAME RUN recorded, via a real edge --
        not just a rationale string mentioning "found via bisection"."""
        from twin_core.models.enums import EdgeType

        wp = await _seed_cad(twin)
        evidence_recorder = make_evidence_recorder(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(
            twin, evidence_recorder=evidence_recorder, decision_recorder=decision_recorder
        )
        out = await optimize(
            work_product_id=str(wp.id), load_n=20.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        decision_id = UUID(out["decision_node_id"])
        edges = await twin.get_edges(decision_id, edge_type=EdgeType.SUPPORTED_BY)
        assert len(edges) == 1
        assert str(edges[0].target_id) == out["evidence_node_id"]

    async def test_no_evidence_link_when_evidence_recorder_absent(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_wall_thickness_optimizer(twin, decision_recorder=decision_recorder)
        out = await optimize(
            work_product_id=str(wp.id), load_n=20.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        decision_wp = await twin.get_work_product(UUID(out["decision_node_id"]))
        assert decision_wp is not None
        assert decision_wp.metadata.get("evidence_refs", []) == []

    async def test_max_iterations_defaults_to_60_and_is_echoed_back(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        optimize = make_wall_thickness_optimizer(twin)
        out = await optimize(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert out["max_iterations"] == 60

    async def test_max_iterations_is_forwarded_to_the_bisection(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """FORGE-291: a too-small budget must surface as a real, honest
        effect on the search -- not a silently-ignored kwarg."""
        wp = await _seed_cad(twin)
        optimize = make_wall_thickness_optimizer(twin)
        # load_n=100.0 is the same "optimal" (bisecting) case
        # test_optimal_result_against_real_geometry already covers --
        # an infeasible search short-circuits after evaluating just its two
        # bounds and never enters the bisection loop at all, so it
        # wouldn't distinguish a real max_iterations effect from a no-op.
        out = await optimize(
            work_product_id=str(wp.id),
            load_n=100.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            max_iterations=1,
        )
        assert out["max_iterations"] == 1
        assert out["status"] == "optimal"
        default_out = await optimize(
            work_product_id=str(wp.id), load_n=100.0, deflection_limit_mm=0.5, sf_limit=2.0
        )
        assert len(out["candidates"]) < len(default_out["candidates"])


class TestMakeTubeHeightOptimizerDecisionEvidenceLink:
    """FORGE-289: same evidence-linking behaviour as
    TestMakeWallThicknessOptimizer.test_links_decision_to_its_own_evidence,
    proven separately for the tube-height optimizer -- the two share
    identical wiring code, but a test that only covers one leaves the other
    unverified."""

    async def test_links_decision_to_its_own_evidence(self, twin: InMemoryTwinAPI) -> None:
        from api_gateway.twin.optimizer import make_tube_height_optimizer
        from twin_core.models.enums import EdgeType

        wp = await _seed_cad(twin)
        evidence_recorder = make_evidence_recorder(twin)
        decision_recorder = make_decision_recorder(twin)
        optimize = make_tube_height_optimizer(
            twin, evidence_recorder=evidence_recorder, decision_recorder=decision_recorder
        )
        out = await optimize(
            work_product_id=str(wp.id),
            wall_thickness_mm=1.0,
            load_n=20.0,
            deflection_limit_mm=0.5,
            sf_limit=2.0,
            height_min_mm=3.0,
        )
        assert out["status"] == "optimal"
        decision_id = UUID(out["decision_node_id"])
        edges = await twin.get_edges(decision_id, edge_type=EdgeType.SUPPORTED_BY)
        assert len(edges) == 1
        assert str(edges[0].target_id) == out["evidence_node_id"]


class TestOptimizeParameterAdapter:
    async def test_tool_registered_and_returns_shape(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        optimizer = make_wall_thickness_optimizer(twin)
        server = TwinServer(twin=twin, parameter_optimizer=optimizer)
        assert "twin.optimize_parameter" in server.tool_ids

        out = await server.optimize_parameter(
            {
                "work_product_id": str(wp.id),
                "load_n": 100.0,
                "deflection_limit_mm": 0.5,
            }
        )
        assert out["status"] == "optimal"

    async def test_not_registered_when_no_optimizer_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.optimize_parameter" not in server.tool_ids

    async def test_missing_deflection_limit_rejected(self, twin: InMemoryTwinAPI) -> None:
        optimizer = make_wall_thickness_optimizer(twin)
        server = TwinServer(twin=twin, parameter_optimizer=optimizer)
        with pytest.raises(ValueError, match="deflection_limit_mm"):
            await server.optimize_parameter({"work_product_id": "x", "load_n": 20.0})
