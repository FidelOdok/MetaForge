"""Unit tests for perform_regenerate_geometry (MET-630)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.twin.regenerate_geometry import (
    RegenerateGeometryError,
    perform_regenerate_geometry,
)

_STEP = b"ISO-10303-21;\nHEADER;\nfake step body\nENDSEC;\n"


class _FakeBridge:
    def __init__(self, response: dict[str, Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._response = response

    async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        return {"status": "ok", "data": self._response}


class TestPerformRegenerateGeometry:
    async def test_success_commits_new_step_with_script_and_properties(self, tmp_path) -> None:
        result_file = tmp_path / "script_result.step"
        result_file.write_bytes(_STEP)
        bridge = _FakeBridge(
            {
                "cad_file": str(result_file),
                "volume_mm3": 1234.5,
                "surface_area_mm2": 678.9,
                "bounding_box": {"x": 10, "y": 20, "z": 30},
            }
        )
        recorded: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            recorded.update(kwargs)
            return {"node_id": "n-1", "model_url": "/v1/twin/nodes/n-1/model"}

        result = await perform_regenerate_geometry(
            bridge=bridge,
            recorder=recorder,
            script_source="pad(15)\n",
            name="Bracket",
            project_id="proj-1",
            parameters={"pad_length_mm": 15},
        )

        assert result["node_id"] == "n-1"
        assert bridge.calls == [("cadquery.execute_script", {"script": "pad(15)\n"})]
        assert recorded["step_base64"]
        assert recorded["name"] == "Bracket"
        assert recorded["project_id"] == "proj-1"
        assert recorded["script_source"] == "pad(15)\n"
        assert recorded["parameters"] == {"pad_length_mm": 15}
        assert recorded["properties"]["volume_mm3"] == 1234.5

        import base64

        assert base64.b64decode(recorded["step_base64"]) == _STEP

    async def test_relative_cad_file_resolves_against_workspace_dir(self, tmp_path) -> None:
        (tmp_path / "script_result.step").write_bytes(_STEP)
        bridge = _FakeBridge({"cad_file": "script_result.step"})

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {"node_id": "n-2"}

        result = await perform_regenerate_geometry(
            bridge=bridge,
            recorder=recorder,
            script_source="pad(10)\n",
            name="Bracket",
            workspace_dir=tmp_path,
        )
        assert result["node_id"] == "n-2"

    async def test_no_output_file_raises(self) -> None:
        bridge = _FakeBridge({})

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="no output file"):
            await perform_regenerate_geometry(
                bridge=bridge, recorder=recorder, script_source="pad(10)\n", name="Bracket"
            )

    async def test_missing_result_file_on_disk_raises(self, tmp_path) -> None:
        bridge = _FakeBridge({"cad_file": str(tmp_path / "nope.step")})

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="not found"):
            await perform_regenerate_geometry(
                bridge=bridge, recorder=recorder, script_source="pad(10)\n", name="Bracket"
            )

    async def test_bridge_error_envelope_raises(self) -> None:
        class _ErrBridge:
            async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
                return {"status": "error", "error": "sandbox rejected script"}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="sandbox rejected"):
            await perform_regenerate_geometry(
                bridge=_ErrBridge(), recorder=recorder, script_source="bad", name="Bracket"
            )
