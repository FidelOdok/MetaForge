"""DFM rule profiles and the heuristic evaluator (C3D slice 4).

Pure checks, no twin: every profile rule carries a kind and a source and no
supplier capability is seeded (DFM-009, DFM-014); each FDM rule fires on
synthetic geometry it should and not on geometry it should not; an unknown
input reads as "not assessed because ...", never a pass (DFM-010); findings
are anchored to the component and the feature or face (DFM-008); the status
is never analysis_passed from heuristics (DFM-013); and FDM and injection
moulding give different findings for the same part (3D-RC-013).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from twin_core.dfm import (
    PROFILES,
    ComponentGeometry,
    DfmFindingSet,
    DfmRule,
    FaceSample,
    MachineCapability,
    MachineContext,
    Measure,
    ProcessContext,
    RuleKind,
    evaluate,
    finding_set_status,
    material_family,
    product_status,
    profile_for_process,
    variant_guidance,
)
from twin_core.dfm.profiles.model import DfmProfile
from twin_core.manufacturing import DfmStatus

FORBIDDEN = ("verified", "production-ready", "production ready", "passed")


def _m(quantity: str, value: Any, confidence: str = "estimated", **kw: Any) -> Measure:
    unit = "deg" if quantity == "draft_angle" else ("" if isinstance(value, bool) else "mm")
    return Measure(
        quantity=quantity,
        value=value,
        unit=unit,
        confidence=confidence,  # type: ignore[arg-type]
        source=kw.pop("source", "test"),
        **kw,
    )


def _geom(
    *measures: Measure, faces: list[FaceSample] | None = None, **kw: Any
) -> ComponentGeometry:
    return ComponentGeometry(
        component_id=kw.pop("component_id", "CMP-enclosure"),
        name="Enclosure",
        level=kw.pop("level", "parametric"),
        measures=list(measures),
        faces=faces,
        **kw,
    )


def _ctx(process: str | None = "FDM", **kw: Any) -> ProcessContext:
    return ProcessContext(
        process=process,
        process_authority="user_stated" if process else "unknown",
        context_ref="MFC-PROJECT@1",
        **kw,
    )


def _rules(fs: DfmFindingSet) -> list[str]:
    return [f.rule_id for f in fs.findings]


def _machine(**caps: Any) -> MachineContext:
    return MachineContext(
        id="m1",
        name="Prusa MK4S",
        capabilities={
            k: MachineCapability(value=v, source=f"Prusa MK4S {k} (manufacturer_spec: url)")
            for k, v in caps.items()
        },
    )


# ── profiles ────────────────────────────────────────────────────────────


def test_every_profile_rule_has_a_kind_and_a_source() -> None:
    assert set(PROFILES) == {"fdm", "resin", "cnc", "injection_moulding", "sheet_metal"}
    for profile in PROFILES.values():
        assert profile.version.count(".") == 2
        for rule in profile.rules:
            assert rule.kind in RuleKind
            assert len(rule.source) >= 10, rule.id
            assert rule.kind is not RuleKind.SUPPLIER_LIMIT, rule.id
            if rule.kind is RuleKind.MACHINE_LIMIT:
                # The number comes from the machine library, never the profile.
                assert rule.limit is None and rule.machine_capability, rule.id
            else:
                # Guidance is marked as such in its source.
                assert "guidance" in rule.source.lower() or "heuristic" in rule.source.lower()


def test_the_fdm_profile_has_the_rules_the_spec_names() -> None:
    fdm = PROFILES["fdm"]
    assert {r.id for r in fdm.rules} == {
        "fdm.min_wall",
        "fdm.min_feature",
        "fdm.overhang",
        "fdm.bridge",
        "fdm.build_volume",
        "fdm.min_hole",
        "fdm.support_access",
    }
    assert fdm.rule("fdm.build_volume").kind is RuleKind.MACHINE_LIMIT


def test_a_profile_cannot_seed_a_supplier_capability() -> None:
    with pytest.raises(ValidationError, match="never seeds a supplier limit"):
        DfmRule(
            id="fdm.supplier_wall",
            title="Supplier wall",
            check="min",
            quantity="wall_thickness",
            limit=0.5,
            kind=RuleKind.SUPPLIER_LIMIT,
            source="A supplier said so on the phone",
            message="The wall is {measured} {unit}.",
            fix="Thicken the wall to {limit}.",
        )
    with pytest.raises(ValidationError, match="comes from the machine library"):
        DfmRule(
            id="fdm.fixed_volume",
            title="Volume",
            check="envelope",
            quantity="envelope",
            limit=200.0,
            kind=RuleKind.MACHINE_LIMIT,
            machine_capability="build_volume_mm",
            source="A machine limit typed into a profile",
            message="Too big at {measured} {unit}.",
            fix="Make it smaller than {limit}.",
        )


def test_a_rule_without_a_source_is_refused() -> None:
    with pytest.raises(ValidationError):
        DfmRule(
            id="fdm.x",
            title="No source",
            check="min",
            quantity="wall_thickness",
            limit=1.0,
            kind=RuleKind.HEURISTIC,
            source="",
            message="The wall is {measured} {unit}.",
            fix="Thicken it to {limit}.",
        )
    with pytest.raises(ValidationError, match="belong to another profile"):
        DfmProfile(
            id="fdm",
            version="1.0.0",
            process="FDM",
            title="FDM",
            aliases=["fdm"],
            rules=[PROFILES["cnc"].rules[0]],
        )


@pytest.mark.parametrize(
    ("words", "profile"),
    [
        ("FDM", "fdm"),
        ("3D printing", "fdm"),
        ("SLA resin print", "resin"),
        ("CNC milling", "cnc"),
        ("Injection moulding", "injection_moulding"),
        ("injection molded ABS", "injection_moulding"),
        ("Sheet metal", "sheet_metal"),
        ("weaving", None),
        (None, None),
    ],
)
def test_process_words_pick_a_profile(words: str | None, profile: str | None) -> None:
    found = profile_for_process(words)
    assert (found.id if found else None) == profile


# ── each FDM rule on synthetic geometry ─────────────────────────────────


def test_min_wall_fires_below_and_not_above() -> None:
    thin = evaluate(_geom(_m("wall_thickness", 0.5, feature="side_wall")), _ctx())
    assert "fdm.min_wall" in _rules(thin)
    ok = evaluate(_geom(_m("wall_thickness", 1.6)), _ctx())
    assert "fdm.min_wall" not in _rules(ok) and "fdm.min_wall" in ok.rules_run


def test_min_wall_reads_an_estimated_block_section() -> None:
    fs = evaluate(_geom(_m("thinnest_section", 0.4, source="L1"), level="L1"), _ctx())
    [finding] = [f for f in fs.findings if f.rule_id == "fdm.min_wall"]
    assert finding.confidence == "estimated" and finding.severity == "advisory"


def test_min_feature_and_min_hole() -> None:
    fs = evaluate(_geom(_m("feature_size", 1.0), _m("hole_diameter", 1.5)), _ctx())
    assert {"fdm.min_feature", "fdm.min_hole"} <= set(_rules(fs))
    fine = evaluate(_geom(_m("feature_size", 3.0), _m("hole_diameter", 3.0)), _ctx())
    assert not {"fdm.min_feature", "fdm.min_hole"} & set(_rules(fine))


def test_overhang_flags_steep_faces_and_anchors_them() -> None:
    faces = [
        FaceSample(
            face_id="f1",
            normal=(0.0, 0.0, -1.0),
            area_mm2=400.0,
            face_signature="CMP-enclosure::bottom",
        ),
        FaceSample(face_id="f2", normal=(1.0, 0.0, 0.0), area_mm2=100.0),
        FaceSample(face_id="f3", normal=(0.0, 0.5, -0.5), area_mm2=50.0),
    ]
    fs = evaluate(_geom(faces=faces), _ctx())
    [finding] = [f for f in fs.findings if f.rule_id == "fdm.overhang"]
    # The largest flagged face anchors it; every flagged face is listed.
    assert finding.anchor.face_signature == "CMP-enclosure::bottom"
    assert finding.anchor.faces == ["CMP-enclosure::bottom"]
    assert finding.measured == 90.0 and finding.limit == 45.0 and finding.unit == "deg"
    vertical = evaluate(_geom(faces=[faces[1]]), _ctx())
    assert "fdm.overhang" in vertical.rules_run and not vertical.findings


def test_overhang_with_directed_brep_faces_spares_the_top() -> None:
    top = FaceSample(face_id="top", normal=(0.0, 0.0, 1.0), area_mm2=100.0)
    fs = evaluate(_geom(faces=[top], faces_directed=True), _ctx())
    assert "fdm.overhang" in fs.rules_run and not fs.findings
    # The same face from a mesh (no outward sense) is flagged, conservatively.
    mesh = evaluate(_geom(faces=[top]), _ctx())
    assert _rules(mesh) == ["fdm.overhang"]


def test_bridge_length() -> None:
    assert "fdm.bridge" in _rules(evaluate(_geom(_m("bridge_length", 25.0)), _ctx()))
    assert "fdm.bridge" not in _rules(evaluate(_geom(_m("bridge_length", 8.0)), _ctx()))


def test_build_volume_needs_the_selected_machine() -> None:
    big = _geom(_m("envelope", [300.0, 100.0, 50.0], "exact"))
    unknown = evaluate(big, _ctx())
    assert "fdm.build_volume" not in unknown.rules_run
    assert any(
        n.rule_id == "fdm.build_volume" and n.reason == "the machine is unknown"
        for n in unknown.not_assessed
    )
    fs = evaluate(big, _ctx(machine=_machine(build_volume_mm=[250, 210, 220])))
    [finding] = [f for f in fs.findings if f.rule_id == "fdm.build_volume"]
    # A machine limit on exact geometry blocks, with the machine's own source.
    assert finding.severity == "blocking" and finding.rule_kind is RuleKind.MACHINE_LIMIT
    assert "Prusa MK4S build_volume_mm" in finding.limit_source
    # A part that fits in some orientation fits.
    fits = evaluate(
        _geom(_m("envelope", [100.0, 240.0, 200.0], "exact")),
        _ctx(machine=_machine(build_volume_mm=[250, 210, 220])),
    )
    assert "fdm.build_volume" in fits.rules_run and not fits.findings


def test_a_machine_capability_replaces_the_guidance() -> None:
    fs = evaluate(
        _geom(_m("wall_thickness", 1.0, "design_parameter")),
        _ctx(machine=_machine(min_wall_mm=1.2)),
    )
    [finding] = fs.findings
    assert finding.limit == 1.2 and finding.rule_kind is RuleKind.MACHINE_LIMIT
    assert finding.severity == "blocking"


def test_support_access_needs_overhangs_inside_a_cavity() -> None:
    down = FaceSample(face_id="f1", normal=(0.0, 0.0, -1.0), area_mm2=10.0)
    hollow = evaluate(_geom(_m("has_internal_cavity", True), faces=[down]), _ctx())
    assert set(_rules(hollow)) == {"fdm.overhang", "fdm.support_access"}
    assert hollow.note == "Printable only with supports that may be hard to remove (preliminary)"
    solid = evaluate(_geom(_m("has_internal_cavity", False), faces=[down]), _ctx())
    assert _rules(solid) == ["fdm.overhang"]
    assert solid.note == "Likely printable with supports (preliminary)"


# ── unknowns and status ─────────────────────────────────────────────────


def test_unknown_inputs_are_not_assessed_with_the_reason() -> None:
    fs = evaluate(_geom(), _ctx())
    assert fs.status is DfmStatus.NOT_ASSESSED and fs.rules_run == [] and not fs.findings
    texts = {n.rule_id: n.text for n in fs.not_assessed}
    assert texts["fdm.min_wall"] == (
        "Not assessed for minimum wall thickness because the wall thickness is unknown"
    )
    assert texts["fdm.build_volume"].endswith("because the part's overall size is unknown")


def test_an_unknown_process_assesses_nothing() -> None:
    fs = evaluate(_geom(_m("wall_thickness", 0.2)), _ctx(process=None))
    assert fs.status is DfmStatus.NOT_ASSESSED and fs.profile_id is None
    assert fs.not_assessed[0].reason == "the process is unknown"
    assert fs.note == "Manufacturing not assessed (the process is unknown)"


def test_a_material_rule_without_a_material_is_not_assessed() -> None:
    fs = evaluate(_geom(_m("wall_thickness", 0.5)), _ctx(process="CNC milling"))
    reasons = {n.rule_id: n.reason for n in fs.not_assessed}
    assert reasons["cnc.min_wall_metal"] == "the material is unknown"
    metal = evaluate(
        _geom(_m("wall_thickness", 0.5)), _ctx("CNC milling", material="Aluminium 6061")
    )
    assert "cnc.min_wall_metal" in _rules(metal) and "cnc.min_wall_plastic" not in metal.rules_run


def test_status_semantics_never_pass_from_heuristics() -> None:
    clean = evaluate(_geom(_m("wall_thickness", 2.0)), _ctx())
    assert clean.status is DfmStatus.PRELIMINARY_GUIDANCE
    assert clean.note == "No issues in 1 preliminary FDM check"
    issues = evaluate(_geom(_m("wall_thickness", 0.2)), _ctx())
    assert issues.status is DfmStatus.ISSUES_FOUND
    pending = evaluate(_geom(_m("wall_thickness", 2.0)), _ctx(), exact_checks_scheduled=True)
    assert pending.status is DfmStatus.ANALYSIS_PENDING
    assert finding_set_status([], [], exact_checks_scheduled=False) is DfmStatus.NOT_ASSESSED
    with pytest.raises(ValidationError, match="never pass"):
        DfmFindingSet(component_id="CMP-x", status=DfmStatus.ANALYSIS_PASSED)
    for fs in (clean, issues, pending):
        assert fs.heuristic_only is True
        assert not any(word in fs.note.lower() for word in FORBIDDEN)


def test_likely_printable_only_when_the_overhang_rule_ran() -> None:
    side = FaceSample(face_id="s", normal=(1.0, 0.0, 0.0), area_mm2=10.0)
    ran = evaluate(_geom(_m("wall_thickness", 2.0), faces=[side]), _ctx())
    assert ran.note == "Likely printable without supports (preliminary)"
    no_faces = evaluate(_geom(_m("wall_thickness", 2.0)), _ctx())
    assert "printable" not in no_faces.note.lower()


def test_product_status_is_the_worst_component() -> None:
    assert product_status([DfmStatus.PRELIMINARY_GUIDANCE, DfmStatus.ISSUES_FOUND]) is (
        DfmStatus.ISSUES_FOUND
    )
    assert product_status([]) is DfmStatus.NOT_ASSESSED


# ── anchors, evidence, decisions ────────────────────────────────────────


def test_findings_are_anchored_with_measured_limit_units_and_evidence() -> None:
    fs = evaluate(
        _geom(
            _m(
                "wall_thickness",
                0.5,
                "human_confirmed",
                feature="feat-extrude-1",
                evidence_ref="PSK-LID@2",
            )
        ),
        _ctx(),
    )
    [f] = fs.findings
    assert f.anchor.component_id == "CMP-enclosure" and f.anchor.feature == "feat-extrude-1"
    assert (f.measured, f.limit, f.unit, f.comparison) == (0.5, 0.8, "mm", ">=")
    assert f.confidence == "human_confirmed" and f.evidence_refs == ["PSK-LID@2"]
    assert f.profile_id == "fdm" and f.profile_version == "1.0.0"
    # Guidance never blocks, however firm the measure.
    assert f.severity == "advisory" and f.rule_kind is RuleKind.GENERAL_GUIDANCE
    assert "0.8 mm" in f.suggested_fix and f.fix_changes == ["wall_thickness"]


def test_finding_ids_are_stable_and_carry_decisions() -> None:
    geom = _geom(_m("wall_thickness", 0.5, feature="w"))
    first = evaluate(geom, _ctx())
    again = evaluate(geom, _ctx(), decisions={first.findings[0].id: {"decision": "keep_design"}})
    assert again.findings[0].id == first.findings[0].id
    assert again.findings[0].decision == {"decision": "keep_design"}
    assert again.open_findings() == []


# ── process trade-off ───────────────────────────────────────────────────


def test_fdm_and_injection_moulding_give_different_findings() -> None:
    geom = _geom(
        _m("wall_thickness", 0.9, "design_parameter", feature="wall"),
        _m("wall_thickness", 6.0, "design_parameter", feature="boss"),
        _m("draft_angle", 0.0, "design_parameter", feature="side"),
        faces=[FaceSample(face_id="f1", normal=(0.0, 0.0, -1.0), area_mm2=10.0)],
    )
    fdm = evaluate(geom, _ctx("FDM"))
    moulding = evaluate(geom, _ctx("Injection moulding"))
    assert set(_rules(fdm)) == {"fdm.overhang"}
    assert set(_rules(moulding)) == {
        "injection_moulding.min_wall",
        "injection_moulding.max_wall",
        "injection_moulding.wall_uniformity",
        "injection_moulding.draft",
    }
    assert moulding.profile_id == "injection_moulding" and fdm.profile_id == "fdm"
    assert {f.id for f in fdm.findings}.isdisjoint({f.id for f in moulding.findings})


def test_relative_rules_use_the_sheet_thickness() -> None:
    fs = evaluate(
        _geom(_m("sheet_thickness", 2.0), _m("bend_radius", 1.0), _m("flange_length", 10.0)),
        _ctx("sheet metal"),
    )
    assert set(_rules(fs)) == {"sheet_metal.bend_radius"}
    no_t = evaluate(_geom(_m("bend_radius", 1.0)), _ctx("sheet metal"))
    reasons = {n.rule_id: n.reason for n in no_t.not_assessed}
    assert reasons["sheet_metal.bend_radius"] == "the sheet thickness is unknown"


def test_resin_drainage_for_hollow_parts() -> None:
    hollow = evaluate(_geom(_m("has_internal_cavity", True)), _ctx("resin"))
    assert "resin.drainage" in _rules(hollow)
    drained = evaluate(
        _geom(_m("has_internal_cavity", True), _m("drain_hole_diameter", 4.0)), _ctx("resin")
    )
    assert "resin.drainage" in drained.rules_run and "resin.drainage" not in _rules(drained)


# ── variants ────────────────────────────────────────────────────────────


def test_variant_guidance_names_the_rules_that_shaped_it() -> None:
    guidance = variant_guidance(PROFILES["cnc"], material="aluminium")
    assert guidance.profile_id == "cnc" and guidance.profile_version == "1.0.0"
    ids = {r["rule_id"] for r in guidance.rules}
    assert "cnc.min_wall_metal" in ids and "cnc.min_wall_plastic" not in ids
    assert "not a checked constraint set" in guidance.text
    assert all(r["source"] for r in guidance.rules)


def test_material_families() -> None:
    assert material_family("PETG") == "plastic"
    assert material_family("Aluminium 6061-T6") == "metal"
    assert material_family("unobtainium") is None
