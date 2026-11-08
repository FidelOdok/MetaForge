"""Board placement hints, edge mounts and board edits in the KiCad adapter.

The worker runs under KiCad's own Python, so it is loaded here against a
stand-in ``pcbnew``: a footprint double that turns, flips and moves its pads,
courtyard, fab outline and "PCB Edge" marker the way KiCad does (positive
orientation counter-clockwise as drawn, y down). The same placements were
checked against KiCad 9's own pcbnew in the kicad-adapter image, with the
GCT USB4105 USB-C receptacle on each of the four edges.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.kicad.circuit import Circuit

WORKER = Path(__file__).resolve().parents[2] / "tool_registry/tools/kicad/pcb_worker.py"
NM = 1_000_000

# -- a stand-in pcbnew ---------------------------------------------------------------


class V:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = int(round(x)), int(round(y))


class Box:
    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    def GetX(self) -> float:  # noqa: N802 - pcbnew's names
        return self.x1

    def GetY(self) -> float:  # noqa: N802
        return self.y1

    def GetRight(self) -> float:  # noqa: N802
        return self.x2

    def GetBottom(self) -> float:  # noqa: N802
        return self.y2

    def GetWidth(self) -> float:  # noqa: N802
        return self.x2 - self.x1

    def GetHeight(self) -> float:  # noqa: N802
        return self.y2 - self.y1

    def Merge(self, o: Box) -> None:  # noqa: N802
        self.x1, self.y1 = min(self.x1, o.x1), min(self.y1, o.y1)
        self.x2, self.y2 = max(self.x2, o.x2), max(self.y2, o.y2)


F_CRTYD, B_CRTYD, F_FAB, B_FAB, DWGS, CMTS, EDGE, F_CU, B_CU = range(9)


class Poly:
    def __init__(self, box: Box | None) -> None:
        self.box = box

    def OutlineCount(self) -> int:  # noqa: N802
        return 1 if self.box else 0

    def BBox(self) -> Box:  # noqa: N802
        assert self.box
        return self.box


class Footprint:
    """A footprint in mm, local frame; turned, flipped and moved like KiCad's."""

    def __init__(
        self,
        ref: str,
        pads: list[tuple[float, float]],
        body: tuple[float, float, float, float],
        marker: tuple[float, float] | None = None,
        lib_id: str = "Lib:Part",
        drill: float | None = None,
    ) -> None:
        self.ref, self.pads_local, self.body_local = ref, pads, body
        self.marker_local, self.lib_id, self.drill = marker, lib_id, drill
        self.pos, self.deg, self.flipped = (0.0, 0.0), 0.0, False

    # transform
    def _to_board(self, x: float, y: float) -> tuple[float, float]:
        if self.flipped:
            x = -x
        r = math.radians(self.deg)
        rx, ry = x * math.cos(r) + y * math.sin(r), -x * math.sin(r) + y * math.cos(r)
        return (rx + self.pos[0]) * NM, (ry + self.pos[1]) * NM

    def _box(self, b: tuple[float, float, float, float]) -> Box:
        pts = [self._to_board(x, y) for x in (b[0], b[2]) for y in (b[1], b[3])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Box(min(xs), min(ys), max(xs), max(ys))

    # pcbnew API
    def GetPosition(self) -> V:  # noqa: N802
        return V(self.pos[0] * NM, self.pos[1] * NM)

    def SetPosition(self, v: V) -> None:  # noqa: N802
        self.pos = (v.x / NM, v.y / NM)

    def SetOrientationDegrees(self, deg: float) -> None:  # noqa: N802
        self.deg = deg

    def GetOrientationDegrees(self) -> float:  # noqa: N802
        return self.deg

    def IsFlipped(self) -> bool:  # noqa: N802
        return self.flipped

    def Flip(self, centre: V, direction: Any) -> None:  # noqa: N802
        self.flipped = not self.flipped

    def GetReference(self) -> str:  # noqa: N802
        return self.ref

    def GetFPIDAsString(self) -> str:  # noqa: N802
        return self.lib_id

    def BuildCourtyardCaches(self) -> None:  # noqa: N802
        pass

    def GetCourtyard(self, layer: int) -> Poly:  # noqa: N802
        own = B_CRTYD if self.flipped else F_CRTYD
        x1, y1, x2, y2 = self.body_local
        return Poly(self._box((x1 - 0.25, y1 - 0.25, x2 + 0.25, y2 + 0.25)) if layer == own else None)

    def GetBoundingBox(self, *_: Any) -> Box:  # noqa: N802
        return self._box(self.body_local)

    def Pads(self) -> list[Any]:  # noqa: N802
        fp = self

        class Pad:
            def __init__(self, x: float, y: float) -> None:
                self.at = fp._to_board(x, y)

            def GetPosition(self) -> V:  # noqa: N802
                return V(*self.at)

            def HasHole(self) -> bool:  # noqa: N802
                return fp.drill is not None

            def GetDrillSize(self) -> V:  # noqa: N802
                return V((fp.drill or 0) * NM, (fp.drill or 0) * NM)

            def GetNetname(self) -> str:  # noqa: N802
                return ""

        return [Pad(x, y) for x, y in self.pads_local]

    def GraphicalItems(self) -> list[Any]:  # noqa: N802
        fp = self
        items: list[Any] = []

        class Fab:
            def GetLayer(self) -> int:  # noqa: N802
                return B_FAB if fp.flipped else F_FAB

            def GetBoundingBox(self) -> Box:  # noqa: N802
                return fp._box(fp.body_local)

        items.append(Fab())
        if self.marker_local:
            mx, my = self.marker_local

            class Text:
                def GetLayer(self) -> int:  # noqa: N802
                    return DWGS

                def GetText(self) -> str:  # noqa: N802
                    return "PCB Edge"

                def GetPosition(self) -> V:  # noqa: N802
                    return V(*fp._to_board(mx, my))

            class Line:
                def GetLayer(self) -> int:  # noqa: N802
                    return DWGS

                def GetShape(self) -> int:  # noqa: N802
                    return 0

                def GetStart(self) -> V:  # noqa: N802
                    return V(*fp._to_board(-3, my))

                def GetEnd(self) -> V:  # noqa: N802
                    return V(*fp._to_board(3, my))

            items += [Text(), Line()]
        return items


class Board:
    def __init__(self, footprints: list[Footprint], w: float = 40, h: float = 30) -> None:
        self.fps = {f.ref: f for f in footprints}
        self.w, self.h = w, h
        self.tracks: list[Any] = []
        self.zones: list[Any] = []

    def GetBoardEdgesBoundingBox(self) -> Box:  # noqa: N802
        return Box(100 * NM, 100 * NM, (100 + self.w) * NM, (100 + self.h) * NM)

    def GetDrawings(self) -> list[Any]:  # noqa: N802
        return []

    def FindFootprintByReference(self, ref: str) -> Footprint | None:  # noqa: N802
        return self.fps.get(ref)

    def GetFootprints(self) -> list[Footprint]:  # noqa: N802
        return list(self.fps.values())

    def GetTracks(self) -> list[Any]:  # noqa: N802
        return list(self.tracks)

    def Zones(self) -> list[Any]:  # noqa: N802
        return list(self.zones)

    def Add(self, fp: Footprint) -> None:  # noqa: N802
        self.fps[fp.ref] = fp

    def Remove(self, item: Any) -> None:  # noqa: N802
        if isinstance(item, Footprint):
            self.fps.pop(item.ref)
        elif item in self.tracks:
            self.tracks.remove(item)
        else:
            self.zones.remove(item)


@pytest.fixture(scope="module")
def worker() -> types.ModuleType:
    stub = types.ModuleType("pcbnew")
    for name, value in {
        "F_CrtYd": F_CRTYD,
        "B_CrtYd": B_CRTYD,
        "F_Fab": F_FAB,
        "B_Fab": B_FAB,
        "Dwgs_User": DWGS,
        "Cmts_User": CMTS,
        "Edge_Cuts": EDGE,
        "F_Cu": F_CU,
        "B_Cu": B_CU,
        "SHAPE_T_SEGMENT": 0,
        "FLIP_DIRECTION_LEFT_RIGHT": 1,
    }.items():
        setattr(stub, name, value)
    stub.FromMM = lambda v: int(round(v * NM))  # type: ignore[attr-defined]
    stub.ToMM = lambda v: v / NM  # type: ignore[attr-defined]
    stub.VECTOR2I = V  # type: ignore[attr-defined]
    stub.SaveBoard = lambda path, board: None  # type: ignore[attr-defined]
    saved = sys.modules.get("pcbnew")
    sys.modules["pcbnew"] = stub
    try:
        spec = importlib.util.spec_from_file_location("pcb_worker_placement", WORKER)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("pcbnew", None)
        else:
            sys.modules["pcbnew"] = saved
    return mod


def usb_c() -> Footprint:
    """A USB-C receptacle like KiCad's GCT USB4105: mouth at +y, marked "PCB Edge"."""
    pads = [(x * 0.5, -3.5) for x in range(-5, 6)] + [(-4.3, 0.5), (4.3, 0.5)]
    return Footprint("J1", pads, (-4.52, -4.45, 4.52, 3.0), marker=(0.0, 3.1))


def absolute(fp: Footprint) -> tuple[float, float, float, float]:
    b = fp._box(fp.body_local)
    return (b.x1 / NM, b.y1 / NM, b.x2 / NM, b.y2 / NM)


X0, Y0, W, H = 100.0, 100.0, 40.0, 30.0

# -- the pure geometry ----------------------------------------------------------------


def test_a_quarter_turn_takes_plus_x_to_minus_y(worker: types.ModuleType) -> None:
    x, y = worker.rotate_point(1.0, 0.0, 90.0)
    assert (round(x, 9), round(y, 9)) == (0.0, -1.0)
    assert worker.side_after_rotation("+y", 90) == "+x"
    assert worker.side_after_rotation("+y", 180) == "-y"


@pytest.mark.parametrize("edge", ["+x", "-x", "+y", "-y"])
@pytest.mark.parametrize("local", ["+x", "-x", "+y", "-y"])
def test_rotation_for_edge_turns_any_side_to_any_edge(
    worker: types.ModuleType, local: str, edge: str
) -> None:
    assert worker.side_after_rotation(local, worker.rotation_for_edge(local, edge)) == edge


def test_the_mouth_is_found_from_the_marker_then_from_the_pads(worker: types.ModuleType) -> None:
    box = (-4.0, -4.0, 4.0, 3.0)
    assert worker.mating_side(box, (0.0, -3.0), (0.0, 3.1)) == ("+y", "pcb_edge_marker")
    # Pads at the back (-y): the body reaches furthest the other way.
    assert worker.mating_side(box, (0.0, -3.0)) == ("+y", "pad_geometry")
    # Pads in the middle of the body: nobody can tell.
    assert worker.mating_side((-2, -2, 2, 2), (0.0, 0.0)) == (None, "unknown")


@pytest.mark.parametrize(
    ("edge", "overhang"), [("+x", 0.0), ("-x", 1.5), ("+y", -0.5), ("-y", 2.0)]
)
def test_edge_mount_anchor_puts_the_body_side_at_the_overhang(
    worker: types.ModuleType, edge: str, overhang: float
) -> None:
    body = (-3.0, -2.0, 4.0, 5.0)
    cx, cy = worker.edge_mount_anchor(edge, body, X0, Y0, W, H, overhang)
    assert worker.mouth_overhang(edge, body, cx, cy, X0, Y0, W, H) == pytest.approx(overhang)


def test_edge_mount_along_keeps_the_given_coordinate(worker: types.ModuleType) -> None:
    cx, cy = worker.edge_mount_anchor("+x", (-1, -1, 1, 1), X0, Y0, W, H, 0.0, along=112.0)
    assert (cx, cy) == (139.0, 112.0)


# -- placement on a footprint -------------------------------------------------------


@pytest.mark.parametrize("edge", ["+x", "-x", "+y", "-y"])
@pytest.mark.parametrize("overhang", [0.0, 1.5])
def test_edge_mount_turns_the_mouth_to_the_edge_and_puts_it_there(
    worker: types.ModuleType, edge: str, overhang: float
) -> None:
    fp = usb_c()
    hint = {"edge_mount": {"edge": edge, "overhang_mm": overhang}}
    anchor, note = worker.apply_placement(fp, hint, X0, Y0, W, H)
    assert anchor is not None and note["fixed"] is True
    fp.SetPosition(V(anchor[0] * NM, anchor[1] * NM))
    # Facing out: measured on the turned footprint, not assumed.
    assert worker._facing(fp)[0] == edge
    assert note["edge_mount"]["faces"] == edge
    assert note["edge_mount"]["mating_side_source"] == "pcb_edge_marker"
    x1, y1, x2, y2 = absolute(fp)
    mouth = {"+x": x2 - (X0 + W), "-x": X0 - x1, "+y": y2 - (Y0 + H), "-y": Y0 - y1}[edge]
    assert mouth == pytest.approx(overhang, abs=1e-6)
    assert note["edge_mount"]["overhang_mm"] == pytest.approx(overhang, abs=1e-3)


def test_an_unmarked_receptacle_is_turned_by_its_pads(worker: types.ModuleType) -> None:
    fp = usb_c()
    fp.marker_local = None
    anchor, note = worker.apply_placement(fp, {"edge_mount": {"edge": "-x"}}, X0, Y0, W, H)
    assert note["edge_mount"]["mating_side_source"] == "pad_geometry"
    fp.SetPosition(V(anchor[0] * NM, anchor[1] * NM))
    assert worker._facing(fp)[0] == "-x"
    assert absolute(fp)[0] == pytest.approx(X0)


def test_a_part_whose_mouth_cannot_be_told_is_refused_by_name(worker: types.ModuleType) -> None:
    fp = Footprint("J2", [(0, 0)], (-1, -1, 1, 1))
    with pytest.raises(ValueError, match="J2: cannot tell which side"):
        worker.apply_placement(fp, {"edge_mount": {"edge": "+x"}}, X0, Y0, W, H)
    anchor, _ = worker.apply_placement(
        fp, {"edge_mount": {"edge": "+x"}, "rotation_deg": 90}, X0, Y0, W, H
    )
    assert fp.deg == 90 and anchor is not None


def test_position_rotation_and_side_are_honoured(worker: types.ModuleType) -> None:
    fp = Footprint("R1", [(-0.8, 0), (0.8, 0)], (-1.5, -0.7, 1.5, 0.7))
    hint = {"position": {"x_mm": 10.0, "y_mm": 8.0}, "rotation_deg": 90, "side": "bottom"}
    anchor, note = worker.apply_placement(fp, hint, X0, Y0, W, H)
    assert anchor == (110.0, 108.0)
    assert fp.deg == 90 and fp.flipped is True and note["fixed"] is True


def test_rotation_alone_leaves_the_part_to_the_auto_placer(worker: types.ModuleType) -> None:
    fp = Footprint("R2", [(-0.8, 0), (0.8, 0)], (-1.5, -0.7, 1.5, 0.7))
    anchor, note = worker.apply_placement(fp, {"rotation_deg": 45}, X0, Y0, W, H)
    assert anchor is None and fp.deg == 45 and note["fixed"] is False


def test_the_placer_keeps_fixed_parts_and_places_the_rest(worker: types.ModuleType) -> None:
    j1 = Footprint("J1", [(0, -1), (0, 1)], (-1, -2, 1, 2))
    r1 = Footprint("R1", [(-0.8, 0), (0.8, 0)], (-1.5, -0.7, 1.5, 0.7))
    r2 = Footprint("R2", [(-0.8, 0), (0.8, 0)], (-1.5, -0.7, 1.5, 0.7))
    fps = {"J1": j1, "R1": r1, "R2": r2}
    net_of = {"J1.1": "A", "R1.1": "A", "R2.1": "A"}
    out = worker._place(fps, net_of, X0, Y0, W, H, [], set(), {"J1": (130.0, 115.0)})
    by_ref = {p["ref"]: p for p in out["placements"]}
    assert (by_ref["J1"]["board_x_mm"], by_ref["J1"]["board_y_mm"]) == (30.0, 15.0)
    assert by_ref["J1"]["fixed"] is True and by_ref["R1"]["fixed"] is False
    assert out["unplaced"] == []
    # A fixed part on top of another fixed part is placed and reported.
    out = worker._place(
        {"J1": j1, "R1": r1}, net_of, X0, Y0, W, H, [], set(), {"J1": (120, 115), "R1": (120, 115)}
    )
    assert any("overlaps" in w for w in out["warnings"])


def test_corner_holes_and_hole_keepouts(worker: types.ModuleType) -> None:
    holes = worker.corner_holes(3.2, X0, Y0, W, H)
    assert holes[0] == (103.6, 103.6, 3.2) and len(holes) == 4
    assert worker.hole_keepout(105, 125, 3.2) == pytest.approx((101.9, 121.9, 108.1, 128.1))


# -- board edits --------------------------------------------------------------------


def test_edit_operations_are_checked_before_the_board_is_touched(
    worker: types.ModuleType,
) -> None:
    errors = worker.check_operations(
        [
            {"op": "move", "ref": "R1"},
            {"op": "rotate", "ref": "R1"},
            {"op": "place", "ref": "R1"},
            {"op": "set_mounting_hole"},
            {"op": "explode"},
        ]
    )
    assert len(errors) == 5
    assert worker.edit({"pcb_path": "x", "operations": [{"op": "explode"}]})["ok"] is False


def test_the_edit_moves_rotates_places_and_strips(
    worker: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    j1 = usb_c()
    j1.SetPosition(V(110 * NM, 110 * NM))
    r2 = Footprint("R2", [(-0.8, 0), (0.8, 0)], (-1.5, -0.7, 1.5, 0.7))
    h2 = Footprint("H2", [(0, 0)], (-1.6, -1.6, 1.6, 1.6), drill=3.2)

    class Track:
        def GetClass(self) -> str:  # noqa: N802
            return "PCB_TRACK"

    class Pour:
        def GetIsRuleArea(self) -> bool:  # noqa: N802
            return False

        def GetLayer(self) -> int:  # noqa: N802
            return F_CU

        def GetNetname(self) -> str:  # noqa: N802
            return "GND"

    board = Board([j1, r2, h2])
    board.tracks = [Track(), Track()]
    board.zones = [Pour()]
    monkeypatch.setattr(worker.pcbnew, "LoadBoard", lambda path: board, raising=False)
    result = worker.edit(
        {
            "pcb_path": "board.kicad_pcb",
            "operations": [
                {"op": "move", "ref": "R2", "position": {"x_mm": 20, "y_mm": 20}},
                {"op": "rotate", "ref": "R2", "rotation_deg": 45},
                {"op": "place", "ref": "J1", "edge_mount": {"edge": "+x", "overhang_mm": 1.0}},
                {"op": "remove_mounting_hole", "ref": "H2"},
            ],
            "strip_routing": True,
        }
    )
    assert result["ok"] is True, result
    assert (r2.pos, r2.deg) == ((120.0, 120.0), 45)
    assert worker._facing(j1)[0] == "+x"
    assert absolute(j1)[2] - (X0 + W) == pytest.approx(1.0)
    assert "H2" not in board.fps
    assert result["routing_stripped"] == {"tracks": 2, "vias": 0, "pours": 1, "pour_nets": ["GND"]}
    assert board.tracks == [] and board.zones == []
    assert result["beyond_board_edge"] == ["J1"]
    assert {p["ref"] for p in result["placements"]} == {"J1", "R2"}


def test_the_edit_refuses_an_unknown_reference(
    worker: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker.pcbnew, "LoadBoard", lambda path: Board([]), raising=False)
    result = worker.edit(
        {"pcb_path": "b", "operations": [{"op": "rotate", "ref": "U9", "rotation_deg": 90}]}
    )
    assert result == {"ok": False, "errors": ["rotate: no footprint U9 on the board"]}


# -- the circuit model -----------------------------------------------------------------

BASE: dict[str, Any] = {
    "name": "lamp",
    "components": [
        {
            "ref": "J1",
            "symbol": "Connector:USB_C_Receptacle_USB2.0_16P",
            "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
            "value": "USB-C",
            "placement": {"edge_mount": {"edge": "-y", "overhang_mm": 0.5}},
        }
    ],
    "board": {"width_mm": 40, "height_mm": 30},
}


def test_the_circuit_takes_placements_and_mounting_holes() -> None:
    c = Circuit.model_validate(
        {
            **BASE,
            "board": {
                **BASE["board"],
                "mounting_holes": [{"position": {"x_mm": 5, "y_mm": 25}, "drill_mm": 2.7}],
            },
        }
    )
    placement = c.components[0].placement
    assert placement is not None and placement.edge_mount is not None
    assert placement.edge_mount.edge == "-y" and placement.side == "top"
    assert c.board is not None and c.board.mounting_holes[0].drill_mm == 2.7


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"placement": {"position": {"x_mm": 50, "y_mm": 1}}}, "outside the 40"),
        ({"placement": {"edge_mount": {"edge": "up"}}}, "edge"),
        ({"placement": {"side": "middle"}}, "side"),
    ],
)
def test_a_bad_placement_is_refused(change: dict[str, Any], message: str) -> None:
    comp = {**BASE["components"][0], **change}
    with pytest.raises(ValueError, match=message):
        Circuit.model_validate({**BASE, "components": [comp]})


def test_a_hole_off_the_board_is_refused() -> None:
    with pytest.raises(ValueError, match="off the board"):
        Circuit.model_validate(
            {**BASE, "board": {**BASE["board"], "mounting_holes": [{"position": {"x_mm": -1, "y_mm": 1}}]}}
        )


# -- the tools -------------------------------------------------------------------------


class _Config:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.footprint_dir = "/fp"
        self.router_cmd = None
        self.max_route_time = 60
        self.max_operation_time = 30


class _Server:
    def __init__(self, work_dir: Path) -> None:
        self.config = _Config(work_dir)

    def _existing(self, path: str, kind: str) -> str:
        assert Path(path).is_file(), path
        return path

    async def _execute_drc(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"errors": 0, "warnings": 0, "violations": [], "unconnected_items": 0}


@pytest.mark.asyncio
async def test_edit_pcb_copies_edits_and_reroutes_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tool_registry.tools.kicad import generation

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    board = src_dir / "lamp.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    (src_dir / "lamp.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    requests: list[dict[str, Any]] = []

    async def fake_worker(server: Any, request: dict[str, Any], timeout: Any = None) -> Any:
        requests.append(request)
        if request["action"] == "edit":
            return {
                "ok": True,
                "applied": [{"op": "move", "ref": "R2"}],
                "placements": [],
                "overlaps": [],
                "beyond_board_edge": [],
                "routing_stripped": {"tracks": 4, "vias": 1, "pours": 2, "pour_nets": ["GND"]},
            }
        return {"ok": True, "tracks": 5, "vias": 1, "track_length_mm": 12.0, "router_time_s": 1}

    monkeypatch.setattr(generation, "_worker", fake_worker)
    out = await generation.edit_pcb_tool(
        _Server(tmp_path),  # type: ignore[arg-type]
        {
            "pcb_file": str(board),
            "operations": [{"op": "move", "ref": "R2", "position": {"x_mm": 1, "y_mm": 2}}],
        },
    )
    edited = Path(out["pcb_file"])
    assert edited != board and edited.parent.parent == tmp_path / "generated"
    assert (edited.parent / "lamp.kicad_sch").exists(), "the project comes along, for parity"
    assert board.read_text(encoding="utf-8") == "(kicad_pcb)"
    assert [r["action"] for r in requests] == ["edit", "route"]
    assert requests[0]["strip_routing"] is True
    assert requests[1]["ground_pour"] == "GND", "the pour the board had"
    assert requests[1]["pcb_path"] == str(edited), "routed in place, not copied again"
    assert out["verdict"] == "pass" and out["routed"]["schematic_parity_checked"] is True
    assert "twin.record_design_files" in out["next"]


@pytest.mark.asyncio
async def test_edit_pcb_without_reroute_says_what_overlaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tool_registry.tools.kicad import generation

    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    async def fake_worker(server: Any, request: dict[str, Any], timeout: Any = None) -> Any:
        assert request["action"] == "edit" and request["strip_routing"] is False
        return {
            "ok": True,
            "applied": [],
            "placements": [],
            "overlaps": ["J1 and R1"],
            "beyond_board_edge": [],
            "routing_stripped": None,
        }

    monkeypatch.setattr(generation, "_worker", fake_worker)
    out = await generation.edit_pcb_tool(
        _Server(tmp_path),  # type: ignore[arg-type]
        {"pcb_file": str(board), "operations": [{"op": "strip_routing"}], "reroute": False},
    )
    assert out["verdict"] == "fail" and out["routed"] is None


@pytest.mark.asyncio
async def test_edit_pcb_reports_a_refused_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tool_registry.tools.kicad import generation

    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    async def fake_worker(server: Any, request: dict[str, Any], timeout: Any = None) -> Any:
        return {"ok": False, "errors": ["rotate: no footprint U9 on the board"]}

    monkeypatch.setattr(generation, "_worker", fake_worker)
    with pytest.raises(ValueError, match="no footprint U9"):
        await generation.edit_pcb_tool(
            _Server(tmp_path),  # type: ignore[arg-type]
            {"pcb_file": str(board), "operations": [{"op": "rotate", "ref": "U9"}]},
        )
