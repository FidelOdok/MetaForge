"""Unit tests for make_sensitivity_ranker / twin.rank_sensitivity (FORGE-317)."""

from __future__ import annotations

import pytest

from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.sensitivity import make_sensitivity_ranker
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


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


class TestRankSensitivity:
    async def test_unknown_metric_rejected(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        with pytest.raises(ValueError, match="metric"):
            await rank(metric="bogus", work_product_id=str(wp.id), wall_thickness_mm=5)

    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        rank = make_sensitivity_ranker(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await rank(
                metric="tip_deflection",
                work_product_id="11111111-1111-1111-1111-111111111111",
                wall_thickness_mm=5,
                load_n=50,
                deflection_limit_mm=0.5,
            )

    async def test_tip_deflection_requires_load_and_limit(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        with pytest.raises(ValueError, match="load_n"):
            await rank(metric="tip_deflection", work_product_id=str(wp.id), wall_thickness_mm=5)

    async def test_mass_requires_limit(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        with pytest.raises(ValueError, match="mass_limit_kg"):
            await rank(metric="mass", work_product_id=str(wp.id), wall_thickness_mm=5)

    async def test_tip_deflection_ranking_against_real_geometry(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        out = await rank(
            metric="tip_deflection",
            work_product_id=str(wp.id),
            wall_thickness_mm=5,
            load_n=50,
            deflection_limit_mm=0.5,
            material="aluminum_6061",
        )
        assert out["metric"] == "tip_deflection"
        params = {e["parameter"] for e in out["rankings"]}
        assert params == {"wall_thickness_mm", "length_mm"}

    async def test_mass_ranking_uses_default_candidate_materials(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        out = await rank(
            metric="mass",
            work_product_id=str(wp.id),
            wall_thickness_mm=5,
            mass_limit_kg=4.5,
            material="aluminum_6061",
        )
        params = {e["parameter"] for e in out["rankings"]}
        assert "wall_thickness_mm" in params
        assert "material=titanium" in params
        assert "material=aluminum_6061" not in params  # baseline excluded

    async def test_mass_ranking_respects_explicit_candidate_materials(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_cad(twin)
        rank = make_sensitivity_ranker(twin)
        out = await rank(
            metric="mass",
            work_product_id=str(wp.id),
            wall_thickness_mm=5,
            mass_limit_kg=4.5,
            material="aluminum_6061",
            candidate_materials=["steel"],
        )
        params = {e["parameter"] for e in out["rankings"]}
        assert params == {"wall_thickness_mm", "material=steel"}

    async def test_records_evidence_pinned_to_work_product(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        evidence_recorder = make_evidence_recorder(twin)
        rank = make_sensitivity_ranker(twin, evidence_recorder=evidence_recorder)
        out = await rank(
            metric="tip_deflection",
            work_product_id=str(wp.id),
            wall_thickness_mm=5,
            load_n=50,
            deflection_limit_mm=0.5,
        )
        assert "evidence_node_id" in out
        from uuid import UUID

        stored = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert stored is not None
        assert stored.metadata["evidence_type"] == "calculation"
        assert stored.metadata["depends_on"][0]["entity_id"] == str(wp.id)


class TestRankSensitivityAdapter:
    async def test_tool_registered_and_returns_shape(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin)
        ranker = make_sensitivity_ranker(twin)
        server = TwinServer(twin=twin, sensitivity_ranker=ranker)
        assert "twin.rank_sensitivity" in server.tool_ids

        out = await server.rank_sensitivity(
            {
                "metric": "tip_deflection",
                "work_product_id": str(wp.id),
                "wall_thickness_mm": 5,
                "load_n": 50,
                "deflection_limit_mm": 0.5,
            }
        )
        assert out["metric"] == "tip_deflection"

    async def test_not_registered_when_no_ranker_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.rank_sensitivity" not in server.tool_ids

    async def test_missing_wall_thickness_rejected(self, twin: InMemoryTwinAPI) -> None:
        ranker = make_sensitivity_ranker(twin)
        server = TwinServer(twin=twin, sensitivity_ranker=ranker)
        with pytest.raises(ValueError, match="wall_thickness_mm"):
            await server.rank_sensitivity({"metric": "tip_deflection", "work_product_id": "x"})
