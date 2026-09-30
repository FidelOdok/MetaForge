"""Unit tests for make_thermal_evidence_recorder / twin.evaluate_thermal_metric
(FORGE-297)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.thermal_evidence import make_thermal_evidence_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.thermal_result: dict[str, Any] = {
            "max_temperature_c": 81.27,
            "min_temperature_c": 25.0,
            "solver_time": 0.4,
        }
        self.cross_check_result: dict[str, Any] = {
            "hand_calc_peak_temp_c": 80.15,
            "fea_peak_temp_c": 81.27,
            "percent_difference": 2.0,
            "tolerance_pct": 20.0,
            "within_tolerance": True,
        }

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id == "calculix.run_thermal":
            return self.thermal_result
        if tool_id == "calculix.cross_check_thermal_steady_state":
            return self.cross_check_result
        raise AssertionError(f"unexpected tool call: {tool_id}")


async def _seed_wp(twin: InMemoryTwinAPI) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name="upper_arm",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
        )
    )


class TestEvaluateThermal:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        with pytest.raises(ValueError, match="no work_product"):
            await evaluate(
                work_product_id="11111111-1111-1111-1111-111111111111",
                mesh_file="/tmp/mesh.inp",
                material={"name": "aluminum_6061"},
                heat_source_node_set="NHeatSource",
                power_dissipation_w=61.4,
                sink_node_set="NSink",
                sink_temp_c=25.0,
            )

    async def test_records_evidence_pinned_to_work_product(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
        )

        assert out["peak_temperature_c"] == 81.27
        assert "evidence_node_id" in out
        assert [c[0] for c in bridge.calls] == ["calculix.run_thermal"]

        evidence = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert evidence is not None
        assert evidence.entity_type == "evidence"
        assert evidence.metadata["producer"]["tool"] == "calculix.run_thermal"
        assert evidence.metadata["result"]["metric"] == "peak_temperature_c"

    async def test_cross_check_runs_when_requested(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
            cross_check={"conduction_length_mm": 120.0, "cross_section_area_mm2": 480.0},
        )

        assert [c[0] for c in bridge.calls] == [
            "calculix.run_thermal",
            "calculix.cross_check_thermal_steady_state",
        ]
        cross_check_call = bridge.calls[1][1]
        # The FEA's own reported peak temperature is threaded through
        # automatically -- caller only supplies the geometric/hand-calc
        # inputs, not the FEA result it's checking against.
        assert cross_check_call["fea_peak_temp_c"] == 81.27
        assert cross_check_call["power_dissipation_w"] == 61.4
        assert cross_check_call["sink_temp_c"] == 25.0
        assert out["cross_check"]["within_tolerance"] is True

    async def test_no_cross_check_when_not_requested(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
        )

        assert "cross_check" not in out
        assert [c[0] for c in bridge.calls] == ["calculix.run_thermal"]

    async def test_within_rating_reported_when_rated_max_given(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
            rated_max_temp_c=100.0,
        )

        assert out["rated_max_temp_c"] == 100.0
        assert out["within_rating"] is True

    async def test_exceeding_rating_reported_as_false(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
            rated_max_temp_c=60.0,
        )

        assert out["within_rating"] is False

    async def test_no_rating_reported_when_rated_max_omitted(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
        )

        assert "within_rating" not in out
        assert "rated_max_temp_c" not in out

    async def test_replay_recorded_on_evidence(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluate = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            material={"name": "aluminum_6061"},
            heat_source_node_set="NHeatSource",
            power_dissipation_w=61.4,
            sink_node_set="NSink",
            sink_temp_c=25.0,
        )

        evidence = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert evidence is not None
        replay = evidence.metadata["replay"]
        assert replay["tool_id"] == "twin.evaluate_thermal_metric"
        assert replay["args"]["work_product_id"] == str(wp.id)
        assert replay["args"]["power_dissipation_w"] == 61.4


class TestEvaluateThermalMetricAdapter:
    """twin.evaluate_thermal_metric tool -- registration + handler."""

    async def test_tool_registered_and_returns_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluator = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, thermal_evaluator=evaluator)
        assert "twin.evaluate_thermal_metric" in server.tool_ids

        out = await server.evaluate_thermal_metric(
            {
                "work_product_id": str(wp.id),
                "mesh_file": "/tmp/mesh.inp",
                "material": {"name": "aluminum_6061"},
                "heat_source_node_set": "NHeatSource",
                "power_dissipation_w": 61.4,
                "sink_node_set": "NSink",
                "sink_temp_c": 25.0,
            }
        )
        assert out["peak_temperature_c"] == 81.27
        assert "evidence_node_id" in out

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        evaluator = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, thermal_evaluator=evaluator)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.evaluate_thermal_metric(
                {
                    "mesh_file": "/tmp/mesh.inp",
                    "material": {"name": "aluminum_6061"},
                    "heat_source_node_set": "NHeatSource",
                    "power_dissipation_w": 61.4,
                    "sink_node_set": "NSink",
                    "sink_temp_c": 25.0,
                }
            )

    async def test_missing_material_rejected(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluator = make_thermal_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, thermal_evaluator=evaluator)
        with pytest.raises(ValueError, match="material"):
            await server.evaluate_thermal_metric(
                {
                    "work_product_id": str(wp.id),
                    "mesh_file": "/tmp/mesh.inp",
                    "heat_source_node_set": "NHeatSource",
                    "power_dissipation_w": 61.4,
                    "sink_node_set": "NSink",
                    "sink_temp_c": 25.0,
                }
            )

    async def test_not_registered_when_no_evaluator_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.evaluate_thermal_metric" not in server.tool_ids
