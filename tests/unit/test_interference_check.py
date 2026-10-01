"""Unit tests for make_interference_check / twin.check_interference (FORGE-272)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.twin.interference_check import make_interference_check
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _FakeBlobStager:
    """Records which node ids were staged; returns a deterministic path per id."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, node_id: str) -> dict[str, Any]:
        self.calls.append(node_id)
        return {
            "node_id": node_id,
            "file_path": f"/workspace/staged/{node_id}.step",
            "filename": f"{node_id}.step",
            "size_bytes": 123,
            "content_hash": "deadbeef",
            "format": "step",
        }


class _FakeBridge:
    """Maps a (file_a, file_b) pair to a canned boolean_operation response."""

    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id != "cadquery.boolean_operation":
            raise AssertionError(f"unexpected tool call: {tool_id}")
        assert params["operation"] == "intersect"
        key = (params["input_file_a"], params["input_file_b"])
        return self._responses[key]


async def _seed_wp(
    twin: InMemoryTwinAPI, *, format_: str = "step", name: str = "part"
) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format=format_,
            created_by="test",
        )
    )


class TestInterferenceCheck:
    async def test_unknown_work_product_a_raises(self, twin: InMemoryTwinAPI) -> None:
        wp_b = await _seed_wp(twin, name="b")
        check = make_interference_check(
            twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({})
        )
        with pytest.raises(ValueError, match="no work_product"):
            await check(
                work_product_id_a="11111111-1111-1111-1111-111111111111",
                work_product_id_b=str(wp_b.id),
            )

    async def test_unknown_work_product_b_raises(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, name="a")
        check = make_interference_check(
            twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({})
        )
        with pytest.raises(ValueError, match="no work_product"):
            await check(
                work_product_id_a=str(wp_a.id),
                work_product_id_b="22222222-2222-2222-2222-222222222222",
            )

    async def test_invalid_work_product_id_raises(self, twin: InMemoryTwinAPI) -> None:
        check = make_interference_check(
            twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({})
        )
        with pytest.raises(ValueError, match="invalid work_product_id"):
            await check(work_product_id_a="not-a-uuid", work_product_id_b="also-not-a-uuid")

    async def test_non_step_format_a_raises(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, format_="stl", name="a")
        wp_b = await _seed_wp(twin, format_="step", name="b")
        check = make_interference_check(
            twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({})
        )
        with pytest.raises(ValueError, match="part A has format 'stl'"):
            await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

    async def test_non_step_format_b_raises(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, format_="step", name="a")
        wp_b = await _seed_wp(twin, format_="stl", name="b")
        check = make_interference_check(
            twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({})
        )
        with pytest.raises(ValueError, match="part B has format 'stl'"):
            await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

    async def test_nonzero_volume_reports_interference(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, name="a")
        wp_b = await _seed_wp(twin, name="b")
        path_a = f"/workspace/staged/{wp_a.id}.step"
        path_b = f"/workspace/staged/{wp_b.id}.step"
        bridge = _FakeBridge({(path_a, path_b): {"result_volume": 125.5, "result_area": 94.2}})
        check = make_interference_check(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        result = await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

        assert result["work_product_id_a"] == str(wp_a.id)
        assert result["work_product_id_b"] == str(wp_b.id)
        assert result["interferes"] is True
        assert result["interference_volume_mm3"] == 125.5
        assert result["interference_area_mm2"] == 94.2

    async def test_zero_volume_reports_no_interference(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, name="a")
        wp_b = await _seed_wp(twin, name="b")
        path_a = f"/workspace/staged/{wp_a.id}.step"
        path_b = f"/workspace/staged/{wp_b.id}.step"
        bridge = _FakeBridge({(path_a, path_b): {"result_volume": 0.0, "result_area": 0.0}})
        check = make_interference_check(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        result = await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

        assert result["interferes"] is False
        assert result["interference_volume_mm3"] == 0.0

    async def test_stages_both_parts(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, name="a")
        wp_b = await _seed_wp(twin, name="b")
        path_a = f"/workspace/staged/{wp_a.id}.step"
        path_b = f"/workspace/staged/{wp_b.id}.step"
        stager = _FakeBlobStager()
        bridge = _FakeBridge({(path_a, path_b): {"result_volume": 10.0, "result_area": 5.0}})
        check = make_interference_check(twin, blob_stager=stager, mcp_bridge=bridge)

        await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

        assert set(stager.calls) == {str(wp_a.id), str(wp_b.id)}

    async def test_calls_boolean_operation_with_intersect(self, twin: InMemoryTwinAPI) -> None:
        wp_a = await _seed_wp(twin, name="a")
        wp_b = await _seed_wp(twin, name="b")
        path_a = f"/workspace/staged/{wp_a.id}.step"
        path_b = f"/workspace/staged/{wp_b.id}.step"
        bridge = _FakeBridge({(path_a, path_b): {"result_volume": 1.0, "result_area": 1.0}})
        check = make_interference_check(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        await check(work_product_id_a=str(wp_a.id), work_product_id_b=str(wp_b.id))

        assert len(bridge.calls) == 1
        tool_id, params = bridge.calls[0]
        assert tool_id == "cadquery.boolean_operation"
        assert params["operation"] == "intersect"
        assert params["input_file_a"] == path_a
        assert params["input_file_b"] == path_b
