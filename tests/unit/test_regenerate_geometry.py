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

    async def test_unknown_cad_tool_raises_value_error(self) -> None:
        bridge = _FakeBridge({})

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(ValueError, match="Unknown cad_tool"):
            await perform_regenerate_geometry(
                bridge=bridge,
                recorder=recorder,
                script_source="pad(10)\n",
                name="Bracket",
                cad_tool="solidworks",
            )


class _FakeFreecadBridge:
    """Simulates the open_session -> execute_code -> export_model -> close_session
    lifecycle (MET-630 follow-up: regenerate_geometry via FreeCAD, not just CadQuery).
    """

    def __init__(
        self,
        *,
        obj_id: str | None = "obj-1",
        step_base64: str | None = None,
        export_extra: dict[str, Any] | None = None,
        fail_export: bool = False,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._obj_id = obj_id
        self._step_base64 = step_base64
        self._export_extra = export_extra or {}
        self._fail_export = fail_export

    async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool == "freecad.open_session":
            return {"status": "ok", "data": {"session_id": "sess-1"}}
        if tool == "freecad.execute_code":
            data: dict[str, Any] = {"executed": True}
            if self._obj_id is not None:
                data["obj_id"] = self._obj_id
            return {"status": "ok", "data": data}
        if tool == "freecad.export_model":
            if self._fail_export:
                return {"status": "error", "error": "export failed"}
            data = {**self._export_extra}
            if self._step_base64 is not None:
                data["step_base64"] = self._step_base64
            return {"status": "ok", "data": data}
        if tool == "freecad.close_session":
            return {"status": "ok", "data": {"closed": True}}
        raise AssertionError(f"unexpected tool call: {tool}")


class TestPerformRegenerateGeometryFreecad:
    async def test_success_runs_full_session_lifecycle(self) -> None:
        import base64

        step_bytes = b"ISO-10303-21;\nfreecad body\n"
        bridge = _FakeFreecadBridge(
            step_base64=base64.b64encode(step_bytes).decode("ascii"),
            export_extra={"volume_mm3": 555.5},
        )
        recorded: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            recorded.update(kwargs)
            return {"node_id": "n-3"}

        result = await perform_regenerate_geometry(
            bridge=bridge,
            recorder=recorder,
            script_source="result = doc.addObject('Part::Box')\n",
            name="Bracket",
            cad_tool="freecad",
        )

        assert result["node_id"] == "n-3"
        assert [c[0] for c in bridge.calls] == [
            "freecad.open_session",
            "freecad.execute_code",
            "freecad.export_model",
            "freecad.close_session",
        ]
        assert bridge.calls[1][1] == {
            "session_id": "sess-1",
            "code": "result = doc.addObject('Part::Box')\n",
        }
        assert bridge.calls[2][1] == {"session_id": "sess-1", "obj_id": "obj-1"}
        assert bridge.calls[3][1] == {"session_id": "sess-1"}
        assert base64.b64decode(recorded["step_base64"]) == step_bytes
        assert recorded["properties"]["volume_mm3"] == 555.5

    async def test_no_result_object_raises_and_still_closes_session(self) -> None:
        bridge = _FakeFreecadBridge(obj_id=None)

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="no result object"):
            await perform_regenerate_geometry(
                bridge=bridge,
                recorder=recorder,
                script_source="doc.addObject('Part::Box')\n",  # no 'result ='
                name="Bracket",
                cad_tool="freecad",
            )

        assert bridge.calls[-1][0] == "freecad.close_session"

    async def test_export_failure_raises_and_still_closes_session(self) -> None:
        bridge = _FakeFreecadBridge(fail_export=True)

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="export failed"):
            await perform_regenerate_geometry(
                bridge=bridge,
                recorder=recorder,
                script_source="result = doc.addObject('Part::Box')\n",
                name="Bracket",
                cad_tool="freecad",
            )

        assert bridge.calls[-1][0] == "freecad.close_session"

    async def test_missing_session_id_raises(self) -> None:
        class _NoSessionBridge:
            async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
                return {"status": "ok", "data": {}}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {}

        with pytest.raises(RegenerateGeometryError, match="session_id"):
            await perform_regenerate_geometry(
                bridge=_NoSessionBridge(),
                recorder=recorder,
                script_source="result = doc.addObject('Part::Box')\n",
                name="Bracket",
                cad_tool="freecad",
            )
