"""Unit tests for the overhang angle math (compute_face_tilt_from_vertical_deg
/ compute_overhang_faces) and make_overhang_evidence_recorder /
twin.evaluate_overhang_metric (FORGE-273)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.dfm_evidence import (
    OVERHANG_TILT_THRESHOLD_DEG,
    compute_face_tilt_from_vertical_deg,
    compute_overhang_faces,
    make_overhang_evidence_recorder,
)
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


class TestComputeFaceTiltFromVerticalDeg:
    def test_vertical_wall_normal_is_zero_tilt(self) -> None:
        # A face whose normal is horizontal (perpendicular to the Z build
        # axis) is a vertical wall -- 0 degrees of tilt, never an overhang.
        assert compute_face_tilt_from_vertical_deg((0.0, 1.0, 0.0)) == pytest.approx(0.0)

    def test_horizontal_face_normal_is_ninety_degree_tilt(self) -> None:
        # A face whose normal is parallel to the build axis (either sense)
        # is perfectly horizontal -- 90 degrees, the worst case.
        assert compute_face_tilt_from_vertical_deg((0.0, 0.0, -1.0)) == pytest.approx(90.0)
        assert compute_face_tilt_from_vertical_deg((0.0, 0.0, 1.0)) == pytest.approx(90.0)

    def test_undirected_same_tilt_either_normal_sense(self) -> None:
        # The mesh normal's sense isn't guaranteed outward (see module
        # docstring) -- a 45-degree-tilted face reads the same tilt
        # whichever way its normal happens to point.
        up = compute_face_tilt_from_vertical_deg((0.7071, 0.0, 0.7071))
        down = compute_face_tilt_from_vertical_deg((-0.7071, 0.0, -0.7071))
        assert up == pytest.approx(down)
        assert up == pytest.approx(45.0, abs=0.01)

    def test_zero_magnitude_normal_returns_zero(self) -> None:
        assert compute_face_tilt_from_vertical_deg((0.0, 0.0, 0.0)) == 0.0

    def test_custom_build_axis(self) -> None:
        # A face vertical relative to an X-axis build direction.
        assert compute_face_tilt_from_vertical_deg(
            (0.0, 1.0, 0.0), build_axis=(1.0, 0.0, 0.0)
        ) == pytest.approx(0.0)


class TestComputeOverhangFaces:
    def test_flags_faces_past_threshold(self) -> None:
        faces = [
            {"name": "NWall", "area_mm2": 100.0, "normal": [0.0, 1.0, 0.0]},
            {"name": "NCeiling", "area_mm2": 50.0, "normal": [0.0, 0.0, -1.0]},
        ]
        out = compute_overhang_faces(faces)
        by_name = {f["name"]: f for f in out}
        assert by_name["NWall"]["flagged"] is False
        assert by_name["NWall"]["tilt_from_vertical_deg"] == pytest.approx(0.0)
        assert by_name["NCeiling"]["flagged"] is True
        assert by_name["NCeiling"]["tilt_from_vertical_deg"] == pytest.approx(90.0)

    def test_exactly_at_threshold_is_not_flagged(self) -> None:
        # 45 degrees is the max still-printable tilt -- the boundary is
        # inclusive-safe (not flagged), only tilt strictly greater than
        # the threshold is flagged.
        import math

        # A normal at exactly 45 degrees from vertical: (sin45, 0, cos45).
        n = (math.sin(math.radians(45.0)), 0.0, math.cos(math.radians(45.0)))
        faces = [{"name": "NBoundary", "area_mm2": 10.0, "normal": list(n)}]
        out = compute_overhang_faces(faces, threshold_deg=OVERHANG_TILT_THRESHOLD_DEG)
        assert out[0]["tilt_from_vertical_deg"] == pytest.approx(45.0, abs=0.01)
        assert out[0]["flagged"] is False

    def test_just_past_threshold_is_flagged(self) -> None:
        import math

        # Angle from the build axis of 44 degrees => tilt from vertical of
        # 90 - 44 = 46 degrees, just past the 45-degree threshold.
        n = (math.sin(math.radians(44.0)), 0.0, math.cos(math.radians(44.0)))
        faces = [{"name": "NPast", "area_mm2": 10.0, "normal": list(n)}]
        out = compute_overhang_faces(faces)
        assert out[0]["tilt_from_vertical_deg"] == pytest.approx(46.0, abs=0.01)
        assert out[0]["flagged"] is True

    def test_skips_faces_with_no_resolvable_normal(self) -> None:
        faces = [
            {"name": "NGood", "area_mm2": 10.0, "normal": [0.0, 1.0, 0.0]},
            {"name": "NDegenerate", "area_mm2": 0.0, "normal": [0.0, 0.0, 0.0]},
            {"name": "NMissing", "area_mm2": 5.0},
        ]
        out = compute_overhang_faces(faces)
        assert {f["name"] for f in out} == {"NGood"}

    def test_custom_threshold(self) -> None:
        # normal = (sin60, 0, cos60) => angle from build axis 60 deg =>
        # tilt from vertical = 90 - 60 = 30 deg.
        faces = [{"name": "NTilt30", "area_mm2": 10.0, "normal": [0.866025, 0.0, 0.5]}]
        assert compute_overhang_faces(faces, threshold_deg=45.0)[0]["flagged"] is False
        assert compute_overhang_faces(faces, threshold_deg=20.0)[0]["flagged"] is True


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.faces_result: dict[str, Any] = {
            "faces": [
                {"name": "NWall", "area_mm2": 200.0, "normal": [0.0, 1.0, 0.0]},
                {"name": "NOverhang", "area_mm2": 30.0, "normal": [0.0, 0.0, -1.0]},
            ]
        }

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id == "freecad.list_named_faces":
            return self.faces_result
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


class TestEvaluateOverhang:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        evaluate = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        with pytest.raises(ValueError, match="no work_product"):
            await evaluate(
                work_product_id="11111111-1111-1111-1111-111111111111",
                mesh_file="/tmp/mesh.inp",
            )

    async def test_records_evidence_pinned_to_work_product(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        evaluate = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(work_product_id=str(wp.id), mesh_file="/tmp/mesh.inp")

        assert out["total_faces"] == 2
        assert out["flagged_count"] == 1
        assert out["dfm_pass"] is False
        assert "evidence_node_id" in out
        assert [c[0] for c in bridge.calls] == ["freecad.list_named_faces"]

        evidence = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert evidence is not None
        assert evidence.entity_type == "evidence"
        assert evidence.metadata["producer"]["tool"] == "freecad.list_named_faces"
        assert evidence.metadata["result"]["metric"] == "overhang_check"

    async def test_dfm_pass_true_when_no_faces_flagged(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        bridge.faces_result = {
            "faces": [{"name": "NWall", "area_mm2": 200.0, "normal": [0.0, 1.0, 0.0]}]
        }
        evaluate = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(work_product_id=str(wp.id), mesh_file="/tmp/mesh.inp")

        assert out["flagged_count"] == 0
        assert out["dfm_pass"] is True

    async def test_custom_threshold_and_build_axis_passed_through(
        self, twin: InMemoryTwinAPI
    ) -> None:
        wp = await _seed_wp(twin)
        bridge = _FakeBridge()
        evaluate = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=bridge
        )

        out = await evaluate(
            work_product_id=str(wp.id),
            mesh_file="/tmp/mesh.inp",
            build_axis=[1.0, 0.0, 0.0],
            threshold_deg=10.0,
        )

        assert out["threshold_deg"] == 10.0
        assert out["build_axis"] == [1.0, 0.0, 0.0]

    async def test_replay_recorded_on_evidence(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluate = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )

        out = await evaluate(work_product_id=str(wp.id), mesh_file="/tmp/mesh.inp")

        evidence = await twin.get_engineering_entity(UUID(out["evidence_node_id"]))
        assert evidence is not None
        replay = evidence.metadata["replay"]
        assert replay["tool_id"] == "twin.evaluate_overhang_metric"
        assert replay["args"]["work_product_id"] == str(wp.id)


class TestEvaluateOverhangMetricAdapter:
    """twin.evaluate_overhang_metric tool -- registration + handler."""

    async def test_tool_registered_and_returns_result(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluator = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, overhang_evaluator=evaluator)
        assert "twin.evaluate_overhang_metric" in server.tool_ids

        out = await server.evaluate_overhang_metric(
            {"work_product_id": str(wp.id), "mesh_file": "/tmp/mesh.inp"}
        )
        assert out["total_faces"] == 2
        assert "evidence_node_id" in out

    async def test_missing_work_product_id_rejected(self, twin: InMemoryTwinAPI) -> None:
        evaluator = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, overhang_evaluator=evaluator)
        with pytest.raises(ValueError, match="work_product_id"):
            await server.evaluate_overhang_metric({"mesh_file": "/tmp/mesh.inp"})

    async def test_missing_mesh_file_rejected(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluator = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, overhang_evaluator=evaluator)
        with pytest.raises(ValueError, match="mesh_file"):
            await server.evaluate_overhang_metric({"work_product_id": str(wp.id)})

    async def test_invalid_build_axis_rejected(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        evaluator = make_overhang_evidence_recorder(
            twin, evidence_recorder=make_evidence_recorder(twin), mcp_bridge=_FakeBridge()
        )
        server = TwinServer(twin=twin, overhang_evaluator=evaluator)
        with pytest.raises(ValueError, match="build_axis"):
            await server.evaluate_overhang_metric(
                {
                    "work_product_id": str(wp.id),
                    "mesh_file": "/tmp/mesh.inp",
                    "build_axis": [1.0, 0.0],
                }
            )

    async def test_not_registered_when_no_evaluator_supplied(self, twin: InMemoryTwinAPI) -> None:
        server = TwinServer(twin=twin)
        assert "twin.evaluate_overhang_metric" not in server.tool_ids
