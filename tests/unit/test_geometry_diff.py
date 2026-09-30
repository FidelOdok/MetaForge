"""Unit tests for make_geometry_diff / twin.geometry_diff (FORGE-301)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.twin.geometry_diff import make_geometry_diff
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
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
    """Maps a staged file path to a canned describe_step_file response."""

    def __init__(self, responses_by_path: dict[str, dict[str, Any]]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses_by_path

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id != "freecad.describe_step_file":
            raise AssertionError(f"unexpected tool call: {tool_id}")
        return self._responses[params["input_file"]]


def _component(volume: float, area: float, label: str = "Part") -> dict[str, Any]:
    return {
        "label": label,
        "solid_count": 1,
        "volume": volume,
        "area": area,
        "bounding_box": {"x_min": 0, "x_max": 10, "y_min": 0, "y_max": 10, "z_min": 0, "z_max": 10},
    }


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


class TestGeometryDiff:
    async def test_unknown_work_product_raises(self, twin: InMemoryTwinAPI) -> None:
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({}))
        with pytest.raises(ValueError, match="no work_product"):
            await diff(work_product_id="11111111-1111-1111-1111-111111111111")

    async def test_invalid_work_product_id_raises(self, twin: InMemoryTwinAPI) -> None:
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({}))
        with pytest.raises(ValueError, match="invalid work_product_id"):
            await diff(work_product_id="not-a-uuid")

    async def test_no_supersedes_edge_raises_lookup_error(self, twin: InMemoryTwinAPI) -> None:
        wp = await _seed_wp(twin)
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({}))
        with pytest.raises(LookupError, match="no prior version"):
            await diff(work_product_id=str(wp.id))

    async def test_non_step_current_format_raises(self, twin: InMemoryTwinAPI) -> None:
        previous = await _seed_wp(twin, format_="step")
        current = await _seed_wp(twin, format_="stl")
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({}))
        with pytest.raises(ValueError, match="current work product has format 'stl'"):
            await diff(work_product_id=str(current.id))

    async def test_non_step_previous_format_raises(self, twin: InMemoryTwinAPI) -> None:
        previous = await _seed_wp(twin, format_="stl")
        current = await _seed_wp(twin, format_="step")
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=_FakeBridge({}))
        with pytest.raises(ValueError, match="previous work product has format 'stl'"):
            await diff(work_product_id=str(current.id))

    async def test_real_supersedes_chain_reports_volume_delta(self, twin: InMemoryTwinAPI) -> None:
        previous = await _seed_wp(twin)
        current = await _seed_wp(twin)
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)

        current_path = f"/workspace/staged/{current.id}.step"
        previous_path = f"/workspace/staged/{previous.id}.step"
        bridge = _FakeBridge(
            {
                current_path: {"file": current_path, "components": [_component(1500.0, 900.0)]},
                previous_path: {"file": previous_path, "components": [_component(1000.0, 700.0)]},
            }
        )
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        result = await diff(work_product_id=str(current.id))

        assert result["current_work_product_id"] == str(current.id)
        assert result["previous_work_product_id"] == str(previous.id)
        assert result["current_volume_mm3"] == 1500.0
        assert result["previous_volume_mm3"] == 1000.0
        assert result["volume_delta_mm3"] == 500.0
        assert result["area_delta_mm2"] == 200.0
        assert result["current_bounding_box"]["x_max"] == 10

    async def test_picks_largest_component_when_multipart(self, twin: InMemoryTwinAPI) -> None:
        """A multipart STEP export can include named leaf parts AND a
        top-level compound whose volume equals their sum -- the largest
        (the compound, or the lone solid) is the representative "whole
        part" volume, per describe_step_file's own documented caveat."""
        previous = await _seed_wp(twin)
        current = await _seed_wp(twin)
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)

        current_path = f"/workspace/staged/{current.id}.step"
        previous_path = f"/workspace/staged/{previous.id}.step"
        bridge = _FakeBridge(
            {
                current_path: {
                    "file": current_path,
                    "components": [
                        _component(400.0, 300.0, label="LegA"),
                        _component(400.0, 300.0, label="LegB"),
                        _component(800.0, 600.0, label="Assembly"),
                    ],
                },
                previous_path: {"file": previous_path, "components": [_component(700.0, 500.0)]},
            }
        )
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        result = await diff(work_product_id=str(current.id))

        assert result["current_volume_mm3"] == 800.0
        assert result["volume_delta_mm3"] == 100.0

    async def test_empty_components_raises(self, twin: InMemoryTwinAPI) -> None:
        previous = await _seed_wp(twin)
        current = await _seed_wp(twin)
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)

        current_path = f"/workspace/staged/{current.id}.step"
        previous_path = f"/workspace/staged/{previous.id}.step"
        bridge = _FakeBridge(
            {
                current_path: {"file": current_path, "components": []},
                previous_path: {"file": previous_path, "components": [_component(700.0, 500.0)]},
            }
        )
        diff = make_geometry_diff(twin, blob_stager=_FakeBlobStager(), mcp_bridge=bridge)

        with pytest.raises(ValueError, match="no solid components"):
            await diff(work_product_id=str(current.id))

    async def test_stages_both_nodes(self, twin: InMemoryTwinAPI) -> None:
        previous = await _seed_wp(twin)
        current = await _seed_wp(twin)
        await twin.add_edge(current.id, previous.id, EdgeType.SUPERSEDES)

        current_path = f"/workspace/staged/{current.id}.step"
        previous_path = f"/workspace/staged/{previous.id}.step"
        stager = _FakeBlobStager()
        bridge = _FakeBridge(
            {
                current_path: {"file": current_path, "components": [_component(1000.0, 500.0)]},
                previous_path: {"file": previous_path, "components": [_component(1000.0, 500.0)]},
            }
        )
        diff = make_geometry_diff(twin, blob_stager=stager, mcp_bridge=bridge)

        await diff(work_product_id=str(current.id))

        assert set(stager.calls) == {str(current.id), str(previous.id)}
