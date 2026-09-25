"""Unit tests for domain_agents.shared.commit_geometry (FORGE-100).

Network-free: the fake ``InMemoryMcpBridge`` records every call it receives,
so these assert on the exact arguments ``twin.commit_geometry`` gets sent.
"""

from __future__ import annotations

import pytest

from domain_agents.shared.commit_geometry import (
    commit_geometry,
    measured_metadata_from_cad_result,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge

# ---------------------------------------------------------------------------
# measured_metadata_from_cad_result — pure extraction, no I/O
# ---------------------------------------------------------------------------


class TestMeasuredMetadataFromCadResult:
    def test_extracts_canonical_keys_present_in_the_result(self) -> None:
        result = {
            "cad_file": "output/part.step",
            "volume_mm3": 12500.0,
            "surface_area_mm2": 8400.0,
            "mass_kg": 0.03375,
            "bounding_box": {"min_x": 0.0, "max_x": 50.0},
            "material": "aluminum_6061",  # not a canonical measured key
            "parameters_used": {"width": 50.0},  # not a canonical measured key
        }
        metadata = measured_metadata_from_cad_result(result)
        assert metadata == {
            "volume_mm3": 12500.0,
            "surface_area_mm2": 8400.0,
            "mass_kg": 0.03375,
            "bbox_mm": {"min_x": 0.0, "max_x": 50.0},
        }

    def test_never_fabricates_a_missing_key(self) -> None:
        """FORGE-96's tool result when commit=False was requested, or a tool
        that genuinely didn't compute mass (no material given) -- absent
        keys stay absent, never defaulted to 0."""
        result = {"cad_file": "output/part.step", "volume_mm3": 1000.0}
        metadata = measured_metadata_from_cad_result(result)
        assert metadata == {"volume_mm3": 1000.0}
        assert "mass_kg" not in metadata
        assert "bbox_mm" not in metadata

    def test_empty_result_yields_empty_metadata(self) -> None:
        assert measured_metadata_from_cad_result({}) == {}

    def test_non_dict_bounding_box_is_ignored_not_crashed_on(self) -> None:
        # Defensive: a malformed tool result must not raise from this helper.
        result = {"volume_mm3": 1000.0, "bounding_box": "not-a-dict"}
        metadata = measured_metadata_from_cad_result(result)
        assert metadata == {"volume_mm3": 1000.0}


# ---------------------------------------------------------------------------
# commit_geometry — extra_metadata threading
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge_with_geometry() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.commit_geometry", capability="twin_write")
    bridge.register_tool_response("twin.commit_geometry", {"node_id": "node-1"})
    return bridge


class TestCommitGeometryExtraMetadata:
    async def test_extra_metadata_is_passed_through_to_the_mcp_call(
        self, tmp_path, bridge_with_geometry: InMemoryMcpBridge
    ) -> None:
        step_file = tmp_path / "part.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")

        committed, node_id, _model_url, error = await commit_geometry(
            bridge_with_geometry,
            cad_file=str(step_file),
            name="Shoulder Yoke",
            project_id="proj-1",
            extra_metadata={"volume_mm3": 12500.0, "mass_kg": 0.03375},
        )

        assert committed is True
        assert node_id == "node-1"
        assert error is None
        call = next(c for c in bridge_with_geometry.calls if c[0] == "twin.commit_geometry")
        assert call[1]["extra_metadata"] == {"volume_mm3": 12500.0, "mass_kg": 0.03375}

    async def test_no_extra_metadata_key_when_none_given(
        self, tmp_path, bridge_with_geometry: InMemoryMcpBridge
    ) -> None:
        """Backward compatible: an old caller that doesn't pass
        extra_metadata must not send an empty/None one either."""
        step_file = tmp_path / "part.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")

        await commit_geometry(
            bridge_with_geometry, cad_file=str(step_file), name="Part", project_id=None
        )

        call = next(c for c in bridge_with_geometry.calls if c[0] == "twin.commit_geometry")
        assert "extra_metadata" not in call[1]

    async def test_empty_extra_metadata_dict_is_also_omitted(
        self, tmp_path, bridge_with_geometry: InMemoryMcpBridge
    ) -> None:
        step_file = tmp_path / "part.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")

        await commit_geometry(
            bridge_with_geometry,
            cad_file=str(step_file),
            name="Part",
            project_id=None,
            extra_metadata={},
        )

        call = next(c for c in bridge_with_geometry.calls if c[0] == "twin.commit_geometry")
        assert "extra_metadata" not in call[1]
