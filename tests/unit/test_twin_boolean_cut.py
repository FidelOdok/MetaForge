"""Tests for real CSG boolean-cut (MET-612): POST /v1/twin/nodes/boolean-cut.

Covers ``api_gateway.twin.boolean_ops.perform_boolean_op`` directly (fake
bridge + fake recorder, real ``InMemoryTwinAPI``) and the route's exception ->
HTTP-status mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_gateway.twin import boolean_ops, routes
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct


def _step_wp(*, name: str = "Bracket", fmt: str = "step", file_path: str = "") -> WorkProduct:
    now = datetime.now(UTC)
    return WorkProduct(
        id=uuid4(),
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=file_path,
        content_hash="abc123",
        format=fmt,
        metadata={},
        created_at=now,
        updated_at=now,
        created_by="test",
    )


class _FakeBridge:
    """Canned tool responses keyed by tool_id; records every call."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool_id: str, params: dict) -> dict:
        self.calls.append((tool_id, params))
        return self.responses[tool_id]


def _fake_recorder(result_node_id: str):
    calls: list[dict] = []

    async def record(**kwargs):
        calls.append(kwargs)
        return {"node_id": result_node_id, "model_url": f"/v1/twin/nodes/{result_node_id}/model"}

    record.calls = calls  # type: ignore[attr-defined]
    return record


async def _seed_pair(twin: InMemoryTwinAPI, tmp_path) -> tuple[str, str]:
    a = tmp_path / "target.step"
    a.write_bytes(b"ISO-10303-21; target")
    b = tmp_path / "cutter.step"
    b.write_bytes(b"ISO-10303-21; cutter")
    target = _step_wp(name="Base Plate", file_path=str(a))
    cutter = _step_wp(name="Hole Cutter", file_path=str(b))
    await twin.create_work_product(target)
    await twin.create_work_product(cutter)
    return str(target.id), str(cutter.id)


def _bridge_for(*, pre_volume: float, result_volume: float) -> _FakeBridge:
    return _FakeBridge(
        {
            "cadquery.get_properties": {"volume_mm3": pre_volume},
            "cadquery.boolean_operation": {
                "output_file": "result.step",
                "operation": "subtract",
                "result_volume": result_volume,
                "result_area": 42.0,
            },
        }
    )


class _WritingBridge(_FakeBridge):
    """Also writes the declared output_path so perform_boolean_op can read it back."""

    async def invoke(self, tool_id: str, params: dict) -> dict:
        result = await super().invoke(tool_id, params)
        if tool_id == "cadquery.boolean_operation":
            from pathlib import Path

            Path(params["output_path"]).write_bytes(b"ISO-10303-21; result")
        return result


class TestPerformBooleanOp:
    async def test_successful_cut_commits_and_links_provenance(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        target_id, cutter_id = await _seed_pair(twin, tmp_path)
        bridge = _WritingBridge(
            {
                "cadquery.get_properties": {"volume_mm3": 1000.0},
                "cadquery.boolean_operation": {
                    "output_file": "result.step",
                    "operation": "subtract",
                    "result_volume": 800.0,
                    "result_area": 42.0,
                },
            }
        )
        # The recorder returns the id of a node already registered in the
        # twin (created up front, standing in for what the real geometry
        # recorder would have just created) so provenance add_edge succeeds.
        result_wp = _step_wp(name="cut result", file_path="")
        await twin.create_work_product(result_wp)
        recorder = _fake_recorder(str(result_wp.id))

        out = await boolean_ops.perform_boolean_op(
            twin=twin,
            bridge=bridge,
            recorder=recorder,
            target_node_id=target_id,
            cutter_node_id=cutter_id,
            operation="subtract",
            workspace_dir=tmp_path,
        )

        assert out["node_id"] == str(result_wp.id)
        assert out["result_volume_mm3"] == 800.0
        assert out["result_area_mm2"] == 42.0
        assert recorder.calls[0]["extra_metadata"]["boolean_op"] == "subtract"
        assert recorder.calls[0]["extra_metadata"]["source_target_node_id"] == target_id
        assert recorder.calls[0]["extra_metadata"]["source_cutter_node_id"] == cutter_id

        edges = await twin.get_edges(result_wp.id, direction="incoming")
        sources = {str(e.source_id) for e in edges}
        assert target_id in sources
        assert cutter_id in sources
        for e in edges:
            assert e.edge_type == EdgeType.PARENT_OF

    async def test_scratch_dir_cleaned_up_after_success(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        target_id, cutter_id = await _seed_pair(twin, tmp_path)
        bridge = _WritingBridge(
            {
                "cadquery.get_properties": {"volume_mm3": 1000.0},
                "cadquery.boolean_operation": {
                    "result_volume": 800.0,
                    "result_area": 1.0,
                },
            }
        )
        recorder = _fake_recorder(str(uuid4()))

        await boolean_ops.perform_boolean_op(
            twin=twin,
            bridge=bridge,
            recorder=recorder,
            target_node_id=target_id,
            cutter_node_id=cutter_id,
            operation="subtract",
            workspace_dir=tmp_path,
        )

        assert list((tmp_path / "_boolean_cut").iterdir()) == []

    async def test_no_overlap_raises_and_commits_nothing(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        target_id, cutter_id = await _seed_pair(twin, tmp_path)
        bridge = _WritingBridge(_bridge_for(pre_volume=1000.0, result_volume=1000.0).responses)
        recorder = _fake_recorder(str(uuid4()))

        with pytest.raises(boolean_ops.NoOverlapError):
            await boolean_ops.perform_boolean_op(
                twin=twin,
                bridge=bridge,
                recorder=recorder,
                target_node_id=target_id,
                cutter_node_id=cutter_id,
                operation="subtract",
                workspace_dir=tmp_path,
            )
        assert recorder.calls == []
        # Scratch dir still cleaned up even on the rejected path.
        assert list((tmp_path / "_boolean_cut").iterdir()) == []

    async def test_target_not_found_raises(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        _, cutter_id = await _seed_pair(twin, tmp_path)
        with pytest.raises(boolean_ops.NodeNotFoundError):
            await boolean_ops.perform_boolean_op(
                twin=twin,
                bridge=_FakeBridge({}),
                recorder=_fake_recorder(str(uuid4())),
                target_node_id=str(uuid4()),
                cutter_node_id=cutter_id,
                operation="subtract",
                workspace_dir=tmp_path,
            )

    async def test_invalid_node_id_format_raises_not_found(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        _, cutter_id = await _seed_pair(twin, tmp_path)
        with pytest.raises(boolean_ops.NodeNotFoundError):
            await boolean_ops.perform_boolean_op(
                twin=twin,
                bridge=_FakeBridge({}),
                recorder=_fake_recorder(str(uuid4())),
                target_node_id="not-a-uuid",
                cutter_node_id=cutter_id,
                operation="subtract",
                workspace_dir=tmp_path,
            )

    async def test_non_step_target_raises_invalid_format(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        target = _step_wp(fmt="stl", file_path=str(tmp_path / "t.stl"))
        (tmp_path / "t.stl").write_bytes(b"solid")
        cutter = _step_wp(file_path=str(tmp_path / "c.step"))
        (tmp_path / "c.step").write_bytes(b"ISO-10303-21;")
        await twin.create_work_product(target)
        await twin.create_work_product(cutter)

        with pytest.raises(boolean_ops.InvalidFormatError):
            await boolean_ops.perform_boolean_op(
                twin=twin,
                bridge=_FakeBridge({}),
                recorder=_fake_recorder(str(uuid4())),
                target_node_id=str(target.id),
                cutter_node_id=str(cutter.id),
                operation="subtract",
                workspace_dir=tmp_path,
            )

    async def test_adapter_failure_propagates(self, tmp_path) -> None:
        twin = InMemoryTwinAPI.create()
        target_id, cutter_id = await _seed_pair(twin, tmp_path)

        class _DownBridge:
            async def invoke(self, tool_id, params):  # noqa: ANN001
                raise RuntimeError("adapter unreachable")

        with pytest.raises(RuntimeError):
            await boolean_ops.perform_boolean_op(
                twin=twin,
                bridge=_DownBridge(),
                recorder=_fake_recorder(str(uuid4())),
                target_node_id=target_id,
                cutter_node_id=cutter_id,
                operation="subtract",
                workspace_dir=tmp_path,
            )
        # Scratch dir still cleaned up when the adapter call blows up.
        assert list((tmp_path / "_boolean_cut").iterdir()) == []


class TestBooleanCutRoute:
    """Route-level exception -> HTTP status mapping."""

    async def _seed_route_twin(self, tmp_path) -> tuple[str, str]:
        twin = InMemoryTwinAPI.create()
        target_id, cutter_id = await _seed_pair(twin, tmp_path)
        routes._twin = twin
        return target_id, cutter_id

    async def test_missing_node_returns_404(self, tmp_path, monkeypatch) -> None:
        target_id, cutter_id = await self._seed_route_twin(tmp_path)
        body = routes.BooleanCutRequest(
            target_node_id=str(uuid4()), cutter_node_id=cutter_id, operation="subtract"
        )
        with pytest.raises(HTTPException) as exc:
            await routes.boolean_cut_nodes(body)
        assert exc.value.status_code == 404

    async def test_no_overlap_returns_409(self, tmp_path, monkeypatch) -> None:
        target_id, cutter_id = await self._seed_route_twin(tmp_path)

        async def fake_perform(**kwargs):
            raise boolean_ops.NoOverlapError("no overlap")

        monkeypatch.setattr(routes, "perform_boolean_op", fake_perform)
        body = routes.BooleanCutRequest(
            target_node_id=target_id, cutter_node_id=cutter_id, operation="subtract"
        )
        with pytest.raises(HTTPException) as exc:
            await routes.boolean_cut_nodes(body)
        assert exc.value.status_code == 409

    async def test_adapter_error_returns_503_with_code(self, tmp_path, monkeypatch) -> None:
        target_id, cutter_id = await self._seed_route_twin(tmp_path)

        async def fake_perform(**kwargs):
            raise RuntimeError("adapter down")

        monkeypatch.setattr(routes, "perform_boolean_op", fake_perform)
        body = routes.BooleanCutRequest(
            target_node_id=target_id, cutter_node_id=cutter_id, operation="subtract"
        )
        with pytest.raises(HTTPException) as exc:
            await routes.boolean_cut_nodes(body)
        assert exc.value.status_code == 503
        assert exc.value.detail["code"] == -32001

    async def test_successful_cut_returns_response_model(self, tmp_path, monkeypatch) -> None:
        target_id, cutter_id = await self._seed_route_twin(tmp_path)
        result_wp = _step_wp(name="cut result")
        await routes._twin.create_work_product(result_wp)

        async def fake_perform(**kwargs):
            return {
                "node_id": str(result_wp.id),
                "result_volume_mm3": 800.0,
                "result_area_mm2": 42.0,
            }

        monkeypatch.setattr(routes, "perform_boolean_op", fake_perform)
        body = routes.BooleanCutRequest(
            target_node_id=target_id, cutter_node_id=cutter_id, operation="subtract"
        )
        resp = await routes.boolean_cut_nodes(body)
        assert resp.node.id == str(result_wp.id)
        assert resp.result_volume_mm3 == 800.0
        assert resp.operation == "subtract"
