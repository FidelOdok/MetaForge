"""Connector mating axes, plug envelopes and fasteners in the assembly check.

A desk lamp was released with 0 assembly findings while:

* its USB-C receptacle faced +Y in the assembly and the slot cut for it was
  in the +X wall, so a plug through the slot met the side of the receptacle;
* the slot was 10 x 7.8 mm with the receptacle set 8 mm behind the outer
  face, so a 12.35 x 6.5 mm plug overmold could not enter and the plug
  stopped short;
* no hinge pin or screw existed anywhere: the pin holes were empty and the
  cover's holes held nothing.

These tests hold the checks that catch each, on boxes and the simple solid
engine, the way ``twin.check_assembly`` runs them with the CAD adapter.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.cadquery.operations import STANDARD_SHAPES, standard_shape_spec
from twin_core.dfa.checks import AssemblyReport, AssemblySpecError, parse_spec, run_checks
from twin_core.dfa.connectors import (
    PLUG_ENVELOPES,
    edge_connectors,
    footprint_mating_axis,
    kicad_footprints,
    plug_envelope,
)
from twin_core.dfa.geometry import Body, Box, SimpleSolidEngine, location_matrix
from twin_core.library.standard_parts import (
    StandardPartError,
    build_standard_part,
    iso2338,
    parse_designation,
    standard_part_by_designation,
)

USB4105 = "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"


def _resolve(name: str) -> str:
    if name.startswith("?"):
        raise AssemblySpecError(f"{name!r} is not a part")
    return name


def _body(name: str, size: tuple[float, float, float], at: dict[str, float] | None = None) -> Body:
    return Body(
        name=name,
        ref=f"CAD-{name.upper()}@1",
        matrix=location_matrix(at or {}),
        box=Box((0.0, 0.0, 0.0), size),
    )


async def _run(bodies: list[Body], checks: dict[str, Any]) -> AssemblyReport:
    spec = parse_spec(checks, _resolve)
    return await run_checks(bodies, spec, SimpleSolidEngine(), assembly_ref="ASM-LAMP@1")


def _check(report: AssemblyReport, check: str) -> Any:
    return next(c for c in report.checks if c.check == check)


# ── the mating axis from a footprint ─────────────────────────────────────


def test_a_usb_c_footprint_faces_minus_y_of_the_board_body_and_turns_with_it() -> None:
    # KiCad draws the mouth toward +Y of the footprint (down the screen); the
    # board body's Y is KiCad's reversed.
    assert footprint_mating_axis(USB4105, 0) == pytest.approx((0.0, -1.0, 0.0))
    assert footprint_mating_axis(USB4105, 90) == pytest.approx((1.0, 0.0, 0.0))
    assert footprint_mating_axis(USB4105, 180) == pytest.approx((0.0, 1.0, 0.0))
    assert footprint_mating_axis(USB4105, -90) == pytest.approx((-1.0, 0.0, 0.0))
    # A bottom-side part is mirrored in X, as KiCad flips it.
    assert footprint_mating_axis(USB4105, 0, "B.Cu", "+x") == pytest.approx((-1.0, 0.0, 0.0))
    # Unknown footprints have no axis unless the mating side is given.
    assert footprint_mating_axis("Connector_JST:JST_PH_B2B", 0) is None
    assert footprint_mating_axis("Connector_JST:JST_PH_B2B", 0, mating_side="-y") == (
        pytest.approx((0.0, 1.0, 0.0))
    )
    assert footprint_mating_axis("USB_C_Receptacle_Vertical", 0) is None


BOARD_FILE = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (footprint "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
    (layer "F.Cu")
    (uuid "a1")
    (at 104.5 110.2)
    (property "Reference" "J1" (at 0 -5 0) (layer "F.SilkS"))
    (pad "A1" smd rect (at -3.2 -4.0) (size 0.6 1.2) (layers "F.Cu"))
  )
  (footprint "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"
    (layer "B.Cu")
    (at 120 112 90)
    (fp_text reference "J2" (at 0 0) (layer "B.SilkS"))
  )
)
"""


def test_footprints_are_read_out_of_the_board_file() -> None:
    fps = kicad_footprints(BOARD_FILE)
    assert set(fps) == {"J1", "J2"}
    assert fps["J1"].footprint == USB4105 and fps["J1"].rotation_deg == 0
    assert (fps["J1"].x, fps["J1"].y, fps["J1"].side) == (104.5, 110.2, "top")
    assert fps["J2"].rotation_deg == 90 and fps["J2"].side == "bottom"
    assert [f.reference for f in edge_connectors(fps)] == ["J1"]


def test_the_plug_envelope_defaults_to_usb_c_and_can_be_overridden() -> None:
    plug = plug_envelope()
    assert (plug.width_mm, plug.height_mm, plug.depth_mm) == (12.35, 6.5, 20.0)
    assert PLUG_ENVELOPES["usb_c"].shell_length_mm == 6.65
    big = plug_envelope("usb_c", [14, 7, 25])
    assert (big.width_mm, big.depth_mm, big.source) == (14, 25, "given")
    with pytest.raises(ValueError, match="no default envelope"):
        plug_envelope("hdmi")
    with pytest.raises(ValueError, match="positive"):
        plug_envelope(None, [0, 1, 1])


# ── the desk lamp: a receptacle facing +Y, its slot in the +X wall ───────


def _lamp() -> list[Body]:
    # The board turned 180 degrees about Z in a base whose +X wall has the slot.
    board = _body("board", (46, 32, 1.6), {"x": 59.5, "y": 16, "z": 21, "rz": 180})
    base = _body("base", (130, 90, 30), {"x": -65, "y": -45, "z": -10})
    return [board, base]


async def test_a_connector_facing_along_the_board_away_from_its_slot_is_high() -> None:
    connector = {
        "board": "board",
        "name": "J1",
        "footprint": USB4105,
        "rotation_deg": 0,
        "slot_part": "base",
        "slot_center": [131.0, 41.5, 37.0],
        "slot_normal": [1, 0, 0],
    }
    report = await _run(
        _lamp(), {"connectors": [connector], "allowed_overlaps": [_inside("board", "base")]}
    )
    check = _check(report, "connector")
    assert check.status == "failed" and check.required
    (finding,) = check.findings
    assert finding.severity == "high"
    assert finding.id == "connector:CAD-BASE@1|CAD-BOARD@1:J1:axis"
    assert finding.values["angle_deg"] == pytest.approx(90.0)
    assert finding.values["mating_axis"] == pytest.approx([0.0, 1.0, 0.0])
    assert finding.values["slot_normal"] == [1.0, 0.0, 0.0]
    assert "footprint" in finding.values["axis_source"]
    assert "cannot mate" in finding.message and "[0, 1, 0]" in finding.message
    assert report.outcome() == "issues_found"

    # The same with the axis passed explicitly in the board's frame.
    explicit = {k: v for k, v in connector.items() if k not in ("footprint", "rotation_deg")}
    report = await _run(
        _lamp(),
        {
            "connectors": [{**explicit, "mating_axis": [0, -1, 0]}],
            "allowed_overlaps": [_inside("board", "base")],
        },
    )
    (finding,) = _check(report, "connector").findings
    assert finding.severity == "high" and finding.values["axis_source"] == "given"

    # Turned so its mouth faces +X in the assembly, it faces the slot.
    report = await _run(
        _lamp(),
        {
            "connectors": [{**connector, "rotation_deg": -90}],
            "allowed_overlaps": [_inside("board", "base")],
        },
    )
    assert _check(report, "connector").status == "passed"


def _inside(a: str, b: str) -> dict[str, str]:
    return {"a": a, "b": b, "reason": "the board sits in the base's pocket (boxes here)"}


async def test_a_plug_that_cannot_enter_the_slot_stops_short_and_is_high() -> None:
    board = _body("board", (46, 32, 1.6))
    wall = _body("wall", (5, 40, 20), {"x": 49, "y": -4, "z": -8})
    # The receptacle's mouth at the board edge x = 46, 3 mm short of a 5 mm wall:
    # the plug's overmold must go 8 mm into a 10 x 7.8 mm slot.
    connector = {
        "board": "board",
        "name": "J1",
        "mating_axis": [1, 0, 0],
        "face": [46.0, 16.0, 3.2],
        "board_outline": [[0, 0], [46, 32]],
        "slot_part": "wall",
        "slot_center": [5.0, 20.0, 11.2],
        "slot_normal": [1, 0, 0],
        "slot_size_mm": [10, 7.8],
    }
    report = await _run([board, wall], {"connectors": [connector]})
    (finding,) = _check(report, "connector").findings
    assert finding.severity == "high" and finding.id.endswith("J1:slot")
    assert finding.values["entry_mm"] == pytest.approx(8.0)
    assert finding.values["overmold_margin_mm"][0] == pytest.approx(-1.175)
    assert finding.values["plug"]["overmold_mm"] == [12.35, 6.5, 20.0]
    assert "stops 8.00 mm short" in finding.message

    # A slot the overmold passes (13 x 7 mm) lets the plug seat.
    ok = await _run([board, wall], {"connectors": [{**connector, "slot_size_mm": [13, 7]}]})
    assert _check(ok, "connector").status == "passed", _check(ok, "connector").detail

    # A slot too small for the plug's shell never mates.
    shut = await _run([board, wall], {"connectors": [{**connector, "slot_size_mm": [8, 2]}]})
    (finding,) = _check(shut, "connector").findings
    assert finding.severity == "high" and "shell" in finding.message

    # A larger overmold, given per connector, no longer passes the 13 x 7 slot.
    tight = {**connector, "slot_size_mm": [13, 7], "plug_envelope_mm": [14, 7.5, 25]}
    report = await _run([board, wall], {"connectors": [tight]})
    assert _check(report, "connector").findings[0].severity == "high"


async def test_a_connector_with_no_axis_says_so() -> None:
    board = _body("board", (46, 32, 1.6))
    wall = _body("wall", (5, 40, 20), {"x": 49})
    report = await _run(
        [board, wall],
        {
            "connectors": [
                {
                    "board": "board",
                    "name": "J9",
                    "footprint": "Connector_Custom:Mystery",
                    "slot_part": "wall",
                    "slot_normal": [1, 0, 0],
                }
            ]
        },
    )
    check = _check(report, "connector")
    assert check.status == "not_assessed" and check.required
    assert "J9: no mating axis" in check.detail


# ── standard parts by designation ────────────────────────────────────────


def test_designations_are_read_as_people_write_them() -> None:
    assert parse_designation("ISO 4762 M4x25") == ("iso4762", "M4", 25.0)
    assert parse_designation("M2.5x6") == ("iso4762", "M2.5", 6.0)
    assert parse_designation("socket head cap screw M3 x 10") == ("iso4762", "M3", 10.0)
    assert parse_designation("ISO 7089 M4") == ("iso7089", "M4", None)
    assert parse_designation("hex nut M4 ISO 4032") == ("iso4032", "M4", None)
    assert parse_designation("ISO 2338 4 m6 x 30") == ("iso2338", "4", 30.0)
    assert parse_designation("dowel pin 3x12") == ("iso2338", "3", 12.0)
    assert parse_designation("bearing 608") == ("deep_groove_bearing", "608", None)
    for bad in ("", "M4", "ISO 4762 M4", "a lamp", "pin 4"):
        with pytest.raises(StandardPartError):
            parse_designation(bad)
    screw = standard_part_by_designation("M2.5x8")
    assert screw["specs"]["head_diameter_mm"]["value"] == pytest.approx(4.68)
    assert build_standard_part("", "ISO 7089 M4")["specs"]["thickness_mm"]["value"] == 0.9


def test_an_iso_2338_pin_is_its_designation() -> None:
    pin = iso2338("4", 30)
    assert pin["family"] == "pin" and pin["geometry"]["generator"] == "parallel_pin"
    assert pin["specs"]["nominal_diameter_mm"]["value"] == 4.0
    with pytest.raises(StandardPartError, match="no 4.2 mm pin"):
        iso2338("4.2", 30)
    with pytest.raises(StandardPartError, match="lengths run"):
        iso2338("4", 500)


def test_the_cad_adapter_knows_every_standard_generator() -> None:
    generators = {
        build_standard_part(f, s, length)["geometry"]["generator"]
        for f, s, length in (
            ("iso4762", "M3", 10),
            ("iso2338", "3", 10),
            ("iso7089", "M3", None),
            ("iso4032", "M3", None),
        )
    }
    assert generators == set(STANDARD_SHAPES)
    gen, params, z = standard_shape_spec(
        {**build_standard_part("iso4762", "M3", 10)["geometry"], "z_offset": -0.5}
    )
    assert gen == "socket_head_cap_screw" and params["length"] == 10 and z == -0.5
    with pytest.raises(ValueError, match="not one of"):
        standard_shape_spec({"generator": "rivet", "params": {}})
    with pytest.raises(ValueError, match="'dk' must be a positive number"):
        standard_shape_spec(
            {"generator": "socket_head_cap_screw", "params": {"d": 3, "length": 8, "k": 3}}
        )


# ── fasteners in their holes ─────────────────────────────────────────────


def _cover_and_base() -> list[Body]:
    base = _body("base", (60, 40, 20))
    cover = _body("cover", (60, 40, 2.4), {"z": 20})
    return [base, cover]


def _screw(**over: Any) -> dict[str, Any]:
    screw: dict[str, Any] = {
        "designation": "ISO 4762 M2.5x8",
        "name": "cover screw",
        "location": {"x": 5, "y": 5, "z": 22.4},
        "holes": [
            {"part": "cover", "diameter_mm": 2.7, "length_mm": 2.4, "center": [5, 5, 0]},
            {
                "part": "base",
                "diameter_mm": 2.1,
                "fit": "thread_forming",
                "depth_mm": 8,
                "center": [5, 5, 20],
            },
        ],
    }
    screw.update(over)
    return screw


async def test_a_screw_that_fits_its_holes_passes() -> None:
    report = await _run(_cover_and_base(), {"fasteners": [_screw()]})
    check = _check(report, "fastener")
    assert check.status == "passed" and check.required, check.detail


@pytest.mark.parametrize(
    ("change", "what", "severity"),
    [
        # Too short: 4 mm through a 2.4 mm cover leaves 1.6 mm of thread.
        ({"designation": "M2.5x4"}, "length", "high"),
        # Too long for an 8 mm blind pilot... with only 4 mm of depth.
        ({"holes_1": {"depth_mm": 4}}, "bottoms_out", "high"),
        # The cover hole smaller than the shank.
        ({"holes_0": {"diameter_mm": 2.2}}, "hole:cover", "high"),
        # Tight: under the ISO 273 fine clearance (2.7 for M2.5).
        ({"holes_0": {"diameter_mm": 2.6}}, "hole:cover", "medium"),
        # A pilot as large as the screw forms no thread.
        ({"holes_1": {"diameter_mm": 2.5}}, "hole:base", "high"),
        # A counterbore smaller than the 4.68 mm head.
        ({"holes_0": {"counterbore": {"diameter_mm": 4.0, "depth_mm": 2.5}}}, "head", "high"),
        # The head on a 4 mm boss overhangs it.
        ({"holes_0": {"face_diameter_mm": 4.0}}, "head", "medium"),
        # A hole as wide as the 4.68 mm head: it pulls through.
        ({"holes_0": {"diameter_mm": 4.7}}, "hole:cover", "high"),
        # Not in its hole: 1 mm off.
        ({"location": {"x": 6, "y": 5, "z": 22.4}}, "place:cover", "high"),
    ],
)
async def test_a_screw_that_does_not_fit_is_named(
    change: dict[str, Any], what: str, severity: str
) -> None:
    screw = _screw()
    for key, value in change.items():
        if key.startswith("holes_"):
            idx = int(key.split("_")[1])
            screw["holes"][idx] = {**screw["holes"][idx], **value}
        else:
            screw[key] = value
    report = await _run(_cover_and_base(), {"fasteners": [screw]})
    check = _check(report, "fastener")
    hits = [f for f in check.findings if f.id.endswith(f"cover screw:{what}")]
    assert hits, [f.id for f in check.findings]
    assert hits[0].severity == severity
    assert hits[0].values["designation"] == screw["designation"]


async def test_a_screw_with_nothing_to_hold_it_and_a_nut_stack() -> None:
    clear = _screw(
        holes=[
            {"part": "cover", "diameter_mm": 2.7, "length_mm": 2.4},
            {"part": "base", "diameter_mm": 2.7, "length_mm": 3.0},
        ]
    )
    report = await _run(_cover_and_base(), {"fasteners": [clear]})
    (f,) = [f for f in _check(report, "fastener").findings if f.id.endswith(":held")]
    assert f.severity == "high"
    # With a nut and washer: 2.4 + 3.0 + 0.5 washer + 2 mm nut = 7.9 < 8 + pitch.
    nutted = {**clear, "washers": ["ISO 7089 M2.5"], "nut": "ISO 4032 M2.5"}
    report = await _run(_cover_and_base(), {"fasteners": [nutted]})
    findings = _check(report, "fastener").findings
    assert [f.severity for f in findings if f.id.endswith(":length")] == ["medium"]


async def test_a_hinge_pin_must_span_its_members_and_fit_its_holes() -> None:
    base = _body("base", (16, 30, 30))
    arm = _body("arm", (8, 17, 150), {"x": 4, "z": 20})
    pin = {
        "designation": "ISO 2338 4x20",
        "name": "base pin",
        "location": {"x": 8, "y": 0, "z": 25, "rx": 90},
        "holes": [
            {"part": "base", "diameter_mm": 4.4, "length_mm": 4.0},
            {"part": "arm", "diameter_mm": 3.95, "length_mm": 8.0, "fit": "press"},
            {"part": "base", "diameter_mm": 4.4, "length_mm": 4.0},
        ],
    }
    report = await _run([base, arm], {"fasteners": [pin]})
    assert _check(report, "fastener").status == "passed", _check(report, "fastener").detail
    short = {**pin, "designation": "ISO 2338 4x12"}
    report = await _run([base, arm], {"fasteners": [short]})
    (f,) = _check(report, "fastener").findings
    assert f.severity == "high" and "16 mm of members" in f.message
    loose = {**pin, "holes": [{**pin["holes"][1], "diameter_mm": 4.1}]}
    report = await _run([base, arm], {"fasteners": [loose]})
    assert any("press fit does not hold" in f.message for f in _check(report, "fastener").findings)
    with pytest.raises(AssemblySpecError, match="a fastener is a screw or a pin"):
        parse_spec({"fasteners": [{**pin, "designation": "ISO 7089 M4"}]}, _resolve)
    with pytest.raises(AssemblySpecError, match="'designation' is required"):
        parse_spec({"fasteners": [{"location": {}}]}, _resolve)


# ── joints with no fastener ──────────────────────────────────────────────


async def test_a_hole_pattern_with_no_fastener_is_an_unfastened_joint() -> None:
    bodies = _cover_and_base()
    holes = [[5, 5], [55, 5], [5, 35], [55, 35]]
    pattern = {"a": "cover", "holes_a": holes, "b": "base", "holes_b": holes}
    report = await _run(bodies, {"hole_patterns": [pattern]})
    check = _check(report, "fastener")
    (f,) = check.findings
    assert f.severity == "medium" and f.id == "fastener:CAD-BASE@1|CAD-COVER@1:unfastened"
    assert "unfastened joint: 4 of 4 holes" in f.message

    # One screw in one hole leaves three.
    report = await _run(bodies, {"hole_patterns": [pattern], "fasteners": [_screw()]})
    (f,) = _check(report, "fastener").findings
    assert "3 of 4 holes" in f.message

    # A screw in every hole fastens it.
    screws = [
        _screw(
            name=f"cover screw {i}",
            location={"x": x, "y": y, "z": 22.4},
            holes=[
                {"part": "cover", "diameter_mm": 2.7, "length_mm": 2.4, "center": [x, y, 0]},
                {"part": "base", "diameter_mm": 2.1, "fit": "thread_forming"},
            ],
        )
        for i, (x, y) in enumerate(holes)
    ]
    report = await _run(bodies, {"hole_patterns": [pattern], "fasteners": screws})
    assert _check(report, "fastener").status == "passed", _check(report, "fastener").detail

    # A press fit or a snap needs none.
    for retention in ("press-fit", "snap"):
        report = await _run(bodies, {"hole_patterns": [{**pattern, "retention": retention}]})
        assert _check(report, "fastener").status == "passed"


async def test_a_hinge_with_no_pin_on_its_axis_is_an_unfastened_joint() -> None:
    base = _body("base", (100, 40, 30), {"x": -50, "y": -20})
    arm = _body("arm", (8, 8, 150), {"x": -4, "y": -4, "z": 31})
    joint = {
        "name": "base hinge",
        "type": "revolute",
        "parent": "base",
        "child": "arm",
        "axis_point": [50, 20, 35],
        "axis_dir": [0, 1, 0],
    }
    report = await _run([base, arm], {"joints": [joint]})
    (f,) = _check(report, "fastener").findings
    assert f.severity == "medium" and f.id == "fastener:CAD-ARM@1|CAD-BASE@1:base hinge:unfastened"

    # A pin along the axis (through y = -10 .. 10 at x 0, z 35) fastens it.
    pin = {
        "designation": "ISO 2338 4x20",
        "location": {"x": 0, "y": -10, "z": 35, "rx": 90},
        "holes": [{"part": "arm", "diameter_mm": 4.2, "length_mm": 8}],
    }
    report = await _run([base, arm], {"joints": [joint], "fasteners": [pin]})
    assert _check(report, "fastener").status == "passed", _check(report, "fastener").detail
    report = await _run([base, arm], {"joints": [{**joint, "retention": "snap"}]})
    assert _check(report, "fastener").status == "passed"
