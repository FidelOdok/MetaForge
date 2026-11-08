"""Design for assembly checks on simple solids (twin_core.dfa).

Each check is exercised with boxes placed in an assembly and the simple solid
engine, the same way twin.check_assembly drives them with the CAD adapter:

* two boxes overlapping, and a pair kept apart by a stated gap;
* a misaligned hole pattern (the desk lamp's 36.3 x 21.3 mm board holes
  against a 45 x 30 mm cover);
* a connector set back from the board edge, and one that misses its slot;
* a washer stack that does not fit its clevis gap (8.0 mm tongue and two
  1.6 mm washers in 8.4 mm), and standard washer thicknesses;
* an oversized seated disc (a 66 mm diffuser in a 63 mm seat) with no
  retention;
* a revolute joint whose arm hits the base inside its range.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from twin_core.dfa.checks import (
    AssemblyReport,
    AssemblySpecError,
    parse_spec,
    run_checks,
)
from twin_core.dfa.geometry import (
    Body,
    Box,
    SimpleSolidEngine,
    apply,
    axis_rotation,
    box_distance,
    box_overlap_volume,
    compose,
    flat,
    location_matrix,
    matrix_from,
)


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


async def _run(
    bodies: list[Body], checks: dict[str, Any] | None = None, **kw: Any
) -> AssemblyReport:
    spec = parse_spec(checks or {}, _resolve)
    return await run_checks(bodies, spec, SimpleSolidEngine(), assembly_ref="ASM-TEST@1", **kw)


def _check(report: AssemblyReport, check: str) -> Any:
    return next(c for c in report.checks if c.check == check)


# ── placements and the simple engine ─────────────────────────────────────


def test_a_location_rotates_about_the_origin_then_moves() -> None:
    m = location_matrix({"x": 10, "rz": 90})
    p = apply(m, (1.0, 0.0, 0.0))
    assert p == pytest.approx((10.0, 1.0, 0.0), abs=1e-9)
    assert flat(matrix_from(flat(m))) == pytest.approx(flat(m))
    with pytest.raises(ValueError, match="location key"):
        location_matrix({"yaw": 3})


def test_a_joint_rotation_turns_about_its_axis() -> None:
    rot = axis_rotation((0.0, 0.0, 10.0), (0.0, 1.0, 0.0), 90)
    assert apply(rot, (0.0, 0.0, 20.0)) == pytest.approx((10.0, 0.0, 10.0), abs=1e-9)
    assert apply(compose(rot, location_matrix({})), (0.0, 0.0, 10.0)) == pytest.approx(
        (0.0, 0.0, 10.0)
    )


def test_box_overlap_is_exact_when_parallel_and_sampled_when_turned() -> None:
    a = Box((0, 0, 0), (10, 10, 10))
    b = Box((0, 0, 0), (10, 10, 10))
    assert box_overlap_volume(a, location_matrix({}), b, location_matrix({"x": 8})) == (
        pytest.approx(200.0)
    )
    assert box_overlap_volume(a, location_matrix({}), b, location_matrix({"x": 11})) == 0.0
    turned = box_overlap_volume(
        a, location_matrix({}), b, location_matrix({"x": 5, "y": 5, "rz": 45})
    )
    assert 0 < turned < 1000
    assert box_distance(a, location_matrix({}), b, location_matrix({"x": 13})) == pytest.approx(3)


# ── interference and clearance ───────────────────────────────────────────


async def test_two_overlapping_boxes_are_a_high_interference() -> None:
    report = await _run([_body("base", (20, 20, 10)), _body("lid", (20, 20, 5), {"z": 8})])
    check = _check(report, "interference")
    assert check.status == "failed" and check.required
    (finding,) = check.findings
    assert finding.severity == "high"
    assert finding.parts == ["CAD-BASE@1", "CAD-LID@1"]
    assert finding.values["volume_mm3"] == pytest.approx(800.0)
    assert finding.id == "interference:CAD-BASE@1|CAD-LID@1"
    assert report.outcome() == "issues_found"
    # Accepted, it no longer blocks.
    assert report.outcome({finding.id}) == "passed"


async def test_touching_boxes_pass_and_an_intended_overlap_is_low() -> None:
    bodies = [_body("base", (20, 20, 10)), _body("lid", (20, 20, 5), {"z": 10})]
    assert _check(await _run(bodies), "interference").status == "passed"
    pressed = [_body("boss", (5, 5, 10)), _body("insert", (3, 3, 5), {"x": 1, "y": 1, "z": 6})]
    report = await _run(
        pressed, {"allowed_overlaps": [{"a": "boss", "b": "insert", "reason": "heat-set insert"}]}
    )
    (finding,) = _check(report, "interference").findings
    assert finding.severity == "low" and "heat-set insert" in finding.message
    assert report.outcome() == "passed"


async def test_a_gap_below_the_stated_minimum_is_medium() -> None:
    bodies = [_body("board", (40, 30, 2)), _body("wall", (2, 30, 20), {"x": 40.3})]
    report = await _run(bodies, {"clearances": [{"a": "board", "b": "wall", "min_gap_mm": 0.5}]})
    check = _check(report, "clearance")
    assert check.status == "failed"
    assert check.findings[0].severity == "medium"
    assert check.findings[0].values["gap_mm"] == pytest.approx(0.3)
    ok = await _run(bodies, {"clearances": [{"a": "board", "b": "wall", "min_gap_mm": 0.2}]})
    assert _check(ok, "clearance").status == "passed"


# ── mating features ──────────────────────────────────────────────────────


async def test_a_misaligned_hole_pattern_is_high() -> None:
    board = _body("board", (43, 28, 1.6), {"x": 5, "y": 5, "z": 4})
    cover = _body("cover", (52, 37, 2.4))
    pcb_holes = [[3.35, 3.35], [39.65, 3.35], [3.35, 24.65], [39.65, 24.65]]
    cover_holes = [[3.5, 3.5], [48.5, 3.5], [3.5, 33.5], [48.5, 33.5]]
    report = await _run(
        [board, cover],
        {
            "hole_patterns": [
                {"a": "board", "holes_a": pcb_holes, "b": "cover", "holes_b": cover_holes}
            ]
        },
    )
    check = _check(report, "hole_pattern")
    assert check.status == "failed"
    (finding,) = check.findings
    assert finding.severity == "high" and len(finding.values["misses"]) == 4
    assert finding.values["span_a_mm"] == [36.3, 21.3]
    assert finding.values["span_b_mm"] == [45.0, 30.0]
    assert "36.3 x 21.3" in finding.message and "45 x 30" in finding.message

    # The same pattern, placed so the holes sit over each other, lines up.
    lined = [[h[0] - 5 + 0.15, h[1] - 5 + 0.15] for h in cover_holes]
    ok = await _run(
        [board, cover],
        {"hole_patterns": [{"a": "board", "holes_a": lined, "b": "cover", "holes_b": cover_holes}]},
    )
    assert _check(ok, "hole_pattern").status == "passed"


async def test_a_board_that_does_not_fit_its_opening_is_high() -> None:
    bodies = [_body("board", (43, 28, 1.6), {"z": 10}), _body("cover", (52, 37, 2.4))]
    report = await _run(
        bodies,
        {
            "outlines": [
                {"part": "board", "size_mm": [43, 28], "host": "cover", "opening_mm": [36.8, 21.8]}
            ]
        },
    )
    check = _check(report, "outline_fit")
    assert check.status == "failed" and check.findings[0].severity == "high"
    ok = await _run(
        bodies,
        {
            "outlines": [
                {
                    "part": "board",
                    "size_mm": [43, 28],
                    "host": "cover",
                    "opening_mm": [54.4, 39.4],
                    "clearance_mm": 1.0,
                }
            ]
        },
    )
    assert _check(ok, "outline_fit").status == "passed"


async def test_a_connector_set_back_from_the_edge_is_medium() -> None:
    board = _body("board", (43, 28, 1.6))
    base = _body("base", (2, 40, 20), {"x": 60, "y": -6, "z": -5})
    connector = {
        "board": "board",
        "name": "J1",
        "face": [35.0, 14.0, 3.0],
        "direction": [1, 0, 0],
        "board_outline": [[0, 0], [43, 28]],
    }
    report = await _run([board, base], {"connectors": [connector]})
    check = _check(report, "connector")
    assert check.status == "failed"
    (finding,) = check.findings
    assert finding.severity == "medium" and finding.values["setback_mm"] == pytest.approx(8.0)
    assert finding.id.endswith("J1:edge")

    # At the edge, and facing a slot 0.5 mm away on its centre line: passes.
    at_edge = {
        **connector,
        "face": [42.8, 14.0, 3.0],
        "slot_part": "base",
        "slot_center": [0.0, 20.0, 8.0],
        "slot_normal": [-1, 0, 0],
    }
    base_close = _body("base", (2, 40, 20), {"x": 43.3, "y": -6, "z": -5})
    ok = await _run([board, base_close], {"connectors": [at_edge]})
    assert _check(ok, "connector").status == "passed", _check(ok, "connector").detail

    # The slot 15 mm away does not reach.
    far = await _run([board, base], {"connectors": [at_edge]})
    (miss,) = _check(far, "connector").findings
    assert miss.id.endswith("J1:slot") and miss.values["gap_mm"] > 1.0


async def test_a_washer_stack_that_does_not_fit_its_gap_is_high() -> None:
    bodies = [_body("base", (16, 20, 30)), _body("arm", (8, 17, 150), {"x": 4})]
    stack = {
        "name": "base hinge",
        "gap_mm": 8.4,
        "parts": ["base", "arm"],
        "items": [
            {"name": "tongue", "thickness_mm": 8.0},
            {"part": "washer", "thickness_mm": 1.6, "count": 2},
        ],
    }
    report = await _run(bodies, {"stacks": [stack]})
    check = _check(report, "stack")
    assert check.status == "failed"
    (finding,) = check.findings
    assert finding.severity == "high" and finding.values["stack_mm"] == pytest.approx(11.2)
    assert "the gap is 8.4 mm" in finding.message

    # Standard parts by family and size, and a screw too short for its grip.
    def lookup(family: str, size: str, length: float | None) -> dict[str, Any]:
        if family == "iso7089":
            return {"title": f"Plain washer {size}", "specs": {"thickness_mm": {"value": 0.8}}}
        return {"title": f"Screw {size}x{length:g}", "specs": {"length_mm": {"value": length}}}

    spec = parse_spec(
        {
            "stacks": [
                {
                    "name": "cover screw",
                    "gap_mm": 10,
                    "items": [
                        {"standard": "iso7089", "size": "M2.5", "count": 2},
                        {"thickness_mm": 2.4},
                    ],
                    "fastener": {
                        "standard": "iso4762",
                        "size": "M2.5",
                        "length_mm": 5,
                        "min_engagement_mm": 3,
                    },
                }
            ]
        },
        _resolve,
        standard_part=lookup,
    )
    report = await run_checks(bodies, spec, SimpleSolidEngine(), assembly_ref="ASM-TEST@1")
    (short,) = _check(report, "stack").findings
    assert short.id.endswith("cover screw:short") and short.values["needed_mm"] == pytest.approx(
        7.0
    )


def test_the_real_standard_washer_thickness_is_used() -> None:
    spec = parse_spec(
        {"stacks": [{"gap_mm": 5, "items": [{"standard": "iso7089", "size": "M4", "count": 2}]}]},
        _resolve,
    )
    (item,) = spec.stacks[0].items
    assert item.count == 2 and item.thickness_mm == pytest.approx(0.9)  # ISO 7089 M4
    with pytest.raises(AssemblySpecError, match="stacks\\[0\\].items\\[0\\]"):
        parse_spec(
            {"stacks": [{"gap_mm": 5, "items": [{"standard": "iso7089", "size": "Z9"}]}]}, _resolve
        )


async def test_an_oversized_seated_disc_is_high_and_no_retention_medium() -> None:
    bodies = [_body("head", (66, 66, 30)), _body("diffuser", (66, 66, 1.6), {"z": 40})]
    seat = {"part": "diffuser", "seat_part": "head", "part_diameter_mm": 66, "seat_diameter_mm": 63}
    report = await _run(bodies, {"seats": [seat]})
    check = _check(report, "seat")
    assert check.status == "failed"
    severities = {f.id.rsplit(":", 1)[1]: f.severity for f in check.findings}
    assert severities == {"fit": "high", "retention": "medium"}
    ok = await _run(
        bodies,
        {
            "seats": [
                {**seat, "part_diameter_mm": 62.6, "min_clearance_mm": 0.2, "retention": "clips"}
            ]
        },
    )
    assert _check(ok, "seat").status == "passed"


async def test_a_revolute_joint_that_collides_in_its_range_is_medium() -> None:
    # A tall arm hinged 2 mm above the base's top face: upright it clears,
    # tilted it sweeps into the base.
    base = _body("base", (100, 40, 30), {"x": -50, "y": -20})
    arm = _body("arm", (8, 8, 150), {"x": -4, "y": -4, "z": 31})
    joint = {
        "name": "base hinge",
        "type": "revolute",
        "parent": "base",
        "child": "arm",
        "axis_point": [50, 20, 32],
        "axis_dir": [0, 1, 0],
        "min_deg": -45,
        "max_deg": 45,
        "steps": 7,
    }
    report = await _run([base, arm], {"joints": [joint]})
    assert _check(report, "interference").status == "passed"
    check = _check(report, "motion")
    assert check.status == "failed" and check.required
    (finding,) = check.findings
    assert finding.severity == "medium" and finding.id == "motion:CAD-ARM@1|CAD-BASE@1:base hinge"
    assert -45 in finding.values["angles_deg"] and 45 in finding.values["angles_deg"]
    assert all(abs(a) > 1 for a in finding.values["angles_deg"])

    # A small range clears.
    ok = await _run([base, arm], {"joints": [{**joint, "min_deg": -1, "max_deg": 1}]})
    assert _check(ok, "motion").status == "passed"
    # No limits: not swept, and not required.
    open_ = await _run(
        [base, arm], {"joints": [{k: v for k, v in joint.items() if "deg" not in k}]}
    )
    motion = _check(open_, "motion")
    assert motion.status == "not_assessed" and not motion.required
    assert "no limits stated" in motion.detail


async def test_an_older_body_and_a_required_check_left_out_block() -> None:
    bodies = [_body("base", (10, 10, 10)), _body("lid", (10, 10, 2), {"z": 10})]
    report = await _run(bodies, stale={"lid": "CAD-LID@2"}, required={"hole_pattern"})
    (finding,) = _check(report, "revisions").findings
    assert finding.severity == "high" and "CAD-LID@2" in finding.message
    assert [c.check for c in report.missing_required()] == ["hole_pattern"]
    report2 = await _run(bodies, required={"hole_pattern"})
    assert report2.outcome() == "incomplete"
    assert AssemblyReport.from_dict(report2.as_dict()).outcome() == "incomplete"


def test_a_bad_spec_names_the_field() -> None:
    with pytest.raises(AssemblySpecError, match="clearances\\[0\\]: 'min_gap_mm' is required"):
        parse_spec({"clearances": [{"a": "x", "b": "y"}]}, _resolve)
    with pytest.raises(AssemblySpecError, match="not a part"):
        parse_spec({"clearances": [{"a": "?x", "b": "y", "min_gap_mm": 1}]}, _resolve)
    with pytest.raises(AssemblySpecError, match="'steps'"):
        parse_spec({"joints": [{"child": "a", "steps": 1}]}, _resolve)
    with pytest.raises(AssemblySpecError, match="'reason'"):
        parse_spec({"allowed_overlaps": [{"a": "x", "b": "y"}]}, _resolve)
    assert math.isclose(parse_spec({}, _resolve).tolerance_mm3, 1e-3)
