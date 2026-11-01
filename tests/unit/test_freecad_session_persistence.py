"""FORGE-512: session edits persist across calls and execute_code returns data.

Pure-Python parts run everywhere. The FreeCAD parts need the real bindings and
skip when they are not importable (the case in CI); run them inside the
freecad-adapter image (see the note in test_freecad_mesh_frame.py).
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.freecad.operations import HAS_FREECAD, capped_result, json_safe

needs_freecad = pytest.mark.skipif(not HAS_FREECAD, reason="FreeCAD bindings not importable")


def test_json_safe_plain_data_passthrough() -> None:
    assert json_safe({"a": [1, 2.5, "x", None, True]}) == {"a": [1, 2.5, "x", None, True]}


def test_json_safe_non_finite_and_unknown_become_text() -> None:
    assert json_safe(float("nan")) == "nan"
    assert json_safe(object()).startswith("<object")


def test_capped_result_truncates_oversize() -> None:
    data, truncated = capped_result(list(range(10000)), max_chars=100)
    assert truncated is True
    assert isinstance(data, str) and len(data) == 100
    small, truncated = capped_result({"k": 1})
    assert small == {"k": 1} and truncated is False


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _server() -> Any:
    from tool_registry.tools.freecad.adapter import FreecadServer

    return FreecadServer()


async def _box(server: Any, sid: str, name: str = "A") -> str:
    out = await server.create_primitive(
        {
            "session_id": sid,
            "kind": "box",
            "parameters": {"length": 10, "width": 20, "height": 30},
            "name": name,
        }
    )
    return str(out["obj_id"])


async def _imported_part(server: Any, sid: str, tmp_path: Path) -> str:
    box = await _box(server, sid)
    ex = await server.export_model({"session_id": sid, "obj_id": box})
    path = tmp_path / "part.step"
    path.write_bytes(base64.b64decode(ex["step_base64"]))
    out = await server.import_step({"session_id": sid, "file_path": str(path)})
    return str(out["obj_ids"][0])


@needs_freecad
def test_transform_then_measure_in_later_call(tmp_path: Path) -> None:
    async def go() -> None:
        s = _server()
        sid = (await s.open_session({"name": "t"}))["session_id"]
        pid = await _imported_part(s, sid, tmp_path)
        out = await s.transform_object(
            {
                "session_id": sid,
                "obj_id": pid,
                "position": [100, 0, 0],
                "rotation": {"axis": [0, 0, 1], "angle_deg": 90},
            }
        )
        assert out["placement"]["position"] == [100.0, 0.0, 0.0]
        assert out["placement"]["rotation"]["angle_deg"] == pytest.approx(90.0, abs=1e-3)
        assert out["bounding_box"]["min_x"] == pytest.approx(80.0, abs=0.01)
        later = await s.measure({"session_id": sid, "obj_id": pid})
        assert later["bounding_box"] == {
            k: out["bounding_box"][k]
            for k in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
        }

    _run(go())


@needs_freecad
def test_transform_container_is_visible_when_measuring_child(tmp_path: Path) -> None:
    async def go() -> None:
        s = _server()
        sid = (await s.open_session({"name": "t"}))["session_id"]
        a = await _box(s, sid, "A")
        asm = (await s.create_assembly({"session_id": sid, "name": "Asm"}))["obj_id"]
        await s.add_part_to_assembly({"session_id": sid, "assembly_id": asm, "part_id": a})
        await s.transform_object({"session_id": sid, "obj_id": asm, "position": [50, 0, 0]})
        child = s._sessions.get_object(sid, a)
        assert child.getParentGeoFeatureGroup() is not None
        got = await s.measure({"session_id": sid, "obj_id": a})
        assert got["bounding_box"]["min_x"] == pytest.approx(50.0, abs=0.01)

    _run(go())


@needs_freecad
def test_execute_code_edit_persists_to_later_call() -> None:
    async def go() -> None:
        s = _server()
        sid = (await s.open_session({"name": "t"}))["session_id"]
        box = await _box(s, sid)
        name = s._sessions.get_object(sid, box).Name
        # Placement edit.
        await s.execute_code(
            {
                "session_id": sid,
                "code": (
                    f"doc.getObject('{name}').Placement = Placement(Vector(5, 5, 5), Rotation())"
                ),
            }
        )
        bb = (await s.measure({"session_id": sid, "obj_id": box}))["bounding_box"]
        assert (bb["min_x"], bb["min_y"], bb["min_z"]) == (5.0, 5.0, 5.0)
        # Shape replacement on a parametric Part::Box must survive recompute too.
        await s.execute_code(
            {
                "session_id": sid,
                "code": f"doc.getObject('{name}').Shape = Part.makeBox(1, 2, 3)",
            }
        )
        later = await s.measure({"session_id": sid, "obj_id": box})
        assert later["volume_mm3"] == pytest.approx(6.0, abs=0.01)
        # And a later unrelated recompute-triggering call keeps it.
        await s.transform_object({"session_id": sid, "obj_id": box, "position": [7, 0, 0]})
        again = await s.measure({"session_id": sid, "obj_id": box})
        assert again["volume_mm3"] == pytest.approx(6.0, abs=0.01)
        assert again["bounding_box"]["min_x"] == pytest.approx(7.0, abs=0.01)

    _run(go())


@needs_freecad
def test_execute_code_returns_result_variable() -> None:
    async def go() -> None:
        s = _server()
        sid = (await s.open_session({"name": "t"}))["session_id"]
        out = await s.execute_code(
            {
                "session_id": sid,
                "code": (
                    "result = {'n': 3, 'v': Vector(1, 2, 3), 'bb': Part.makeBox(1, 2, 3).BoundBox}"
                ),
            }
        )
        assert out["executed"] is True
        assert out["result"]["n"] == 3
        assert out["result"]["v"] == [1.0, 2.0, 3.0]
        assert out["result"]["bb"]["max"] == [1.0, 2.0, 3.0]
        big = await s.execute_code({"session_id": sid, "code": "result = list(range(50000))"})
        assert big["result_truncated"] is True
        none = await s.execute_code({"session_id": sid, "code": "x = 1"})
        assert "result" not in none

    _run(go())
