"""Unit tests for make_metric_evaluator / twin.evaluate_metric (FORGE-315)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.metric_evaluator import make_metric_evaluator
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _FakeBridge:
    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result or {"max_von_mises": {"part": 12.3}, "solver_time": 1.1}
        self._error = error

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if self._error is not None:
            raise self._error
        return self._result


async def _seed_cad(
    twin: InMemoryTwinAPI, *, length_mm: float, width_mm: float, height_mm: float
) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name="upper_arm",
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
                            "min_x": -length_mm / 2,
                            "max_x": length_mm / 2,
                            "min_y": -width_mm / 2,
                            "max_y": width_mm / 2,
                            "min_z": -height_mm / 2,
                            "max_z": height_mm / 2,
                        }
                    }
                }
            },
        )
    )


class TestEvaluateTipDeflection:
    async def test_no_geometry_features_raises(self, twin: InMemoryTwinAPI) -> None:
        wp = await twin.create_work_product(
            WorkProduct(
                name="bare",
                type=WorkProductType.CAD_MODEL,
                domain="mechanical",
                file_path="",
                content_hash="x",
                format="step",
                created_by="test",
            )
        )
        evaluate = make_metric_evaluator(twin)
        with pytest.raises(ValueError, match="bounding_box"):
            await evaluate(work_product_id=str(wp.id), load_n=10, youngs_modulus_mpa=70000)

    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        evaluate = make_metric_evaluator(twin)
        with pytest.raises(ValueError, match="no work_product"):
            await evaluate(
                work_product_id="11111111-1111-1111-1111-111111111111",
                load_n=10,
                youngs_modulus_mpa=70000,
            )

    async def test_within_band_does_not_call_bridge(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        bridge = _FakeBridge()
        evidence_recorder = make_evidence_recorder(twin)
        evaluate = make_metric_evaluator(
            twin, evidence_recorder=evidence_recorder, mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            load_n=0.01,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            project_id=None,
        )

        assert out["escalated"] is False
        assert "tier2" not in out
        assert bridge.calls == []
        assert "evidence_node_id" in out

    async def test_escalation_without_tier2_args_does_not_call_bridge(
        self, twin: InMemoryTwinAPI
    ) -> None:
        # _bounding_box_extents_mm assigns the LARGER cross-section extent
        # to width (60) and the smaller to height (40, conservative -- see
        # its own docstring), giving I=60*40^3/12=320000mm^4; load_n=600
        # against limit_mm=0.5/band_fraction=0.2 lands margin (~0.083mm)
        # inside the band (0.1mm) -- a deliberately-chosen escalating case.
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        bridge = _FakeBridge()
        evaluate = make_metric_evaluator(twin, mcp_bridge=bridge)

        out = await evaluate(
            work_product_id=str(wp.id), load_n=600, youngs_modulus_mpa=70000, limit_mm=0.5
        )

        assert out["escalated"] is True
        assert out["tier2"]["attempted"] is False
        assert bridge.calls == []

    async def test_escalation_with_tier2_args_calls_bridge(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        bridge = _FakeBridge(result={"max_von_mises": {"a": 1.0}, "solver_time": 2.0})
        evidence_recorder = make_evidence_recorder(twin)
        evaluate = make_metric_evaluator(
            twin, evidence_recorder=evidence_recorder, mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            load_n=600,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            tier2={
                "mesh_file": "/tmp/mesh.inp",
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": [0, 0, -50],
                "material": {"name": "aluminum_6061"},
            },
        )

        assert out["escalated"] is True
        assert out["tier2"]["attempted"] is True
        assert out["tier2"]["result"]["max_von_mises"] == {"a": 1.0}
        assert "evidence_node_id" in out["tier2"]
        assert len(bridge.calls) == 1
        tool_id, params = bridge.calls[0]
        assert tool_id == "calculix.run_fea"
        assert params["fixed_node_set"] == "Surface1"
        assert params["analysis_type"] == "static_stress"

    async def test_tier2_bridge_failure_preserves_tier0_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        bridge = _FakeBridge(error=RuntimeError("adapter unreachable"))
        evaluate = make_metric_evaluator(twin, mcp_bridge=bridge)

        out = await evaluate(
            work_product_id=str(wp.id),
            load_n=600,
            youngs_modulus_mpa=70000,
            limit_mm=0.5,
            tier2={
                "mesh_file": "/tmp/mesh.inp",
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": [0, 0, -50],
                "material": {"name": "aluminum_6061"},
            },
        )

        assert out["escalated"] is True
        assert out["tier2"]["attempted"] is True
        assert "adapter unreachable" in out["tier2"]["error"]
        # tier-0 fields are still present -- a tier-2 failure never loses them.
        assert out["value_mm"] > 0

    async def test_evidence_pins_work_product_dependency(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        evidence_recorder = make_evidence_recorder(twin)
        evaluate = make_metric_evaluator(twin, evidence_recorder=evidence_recorder)

        out = await evaluate(work_product_id=str(wp.id), load_n=1, youngs_modulus_mpa=70000)

        stored = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert stored.metadata["depends_on"][0]["entity_kind"] == "work_product"
        assert stored.metadata["depends_on"][0]["entity_id"] == str(wp.id)


class TestEvaluateMetricAdapter:
    """twin.evaluate_metric tool -- registration + handler."""

    async def test_tool_registered_and_returns_tier0(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_cad(twin, length_mm=360, width_mm=40, height_mm=60)
        evaluator = make_metric_evaluator(twin)
        server = TwinServer(twin=twin, metric_evaluator=evaluator)
        assert "twin.evaluate_metric" in server.tool_ids

        out = await server.evaluate_metric(
            {
                "metric": "tip_deflection",
                "work_product_id": str(wp.id),
                "load_n": 5,
                "youngs_modulus_mpa": 70000,
            }
        )
        assert out["metric"] == "tip_deflection"
        assert out["tier"] == 0
        assert out["value_mm"] > 0

    async def test_unknown_metric_rejected(self, twin: InMemoryTwinAPI) -> None:
        evaluator = make_metric_evaluator(twin)
        server = TwinServer(twin=twin, metric_evaluator=evaluator)
        with pytest.raises(ValueError, match="metric"):
            await server.evaluate_metric(
                {"metric": "bogus", "work_product_id": "x", "load_n": 1, "youngs_modulus_mpa": 1}
            )

    async def test_missing_required_fields_rejected(self, twin: InMemoryTwinAPI) -> None:
        evaluator = make_metric_evaluator(twin)
        server = TwinServer(twin=twin, metric_evaluator=evaluator)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.evaluate_metric(
                {"metric": "tip_deflection", "load_n": 1, "youngs_modulus_mpa": 1}
            )

    async def test_not_registered_when_no_evaluator_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.evaluate_metric" not in server.tool_ids
