"""FDM (fused deposition modelling) starter profile.

Numbers are general guidance widely published in 3D printing design guides
(two extrusion widths for a wall, 45 degrees self-supporting, about 10 mm
unsupported bridges, holes of 2 mm and up). They are heuristics, not limits
of any printer. The build volume is read from the selected machine in the
machine library, never assumed; without a selected machine it is not
assessed.

Version 1.1.0 names the exact checks run on a validated B-Rep (wall
thickness, feature and hole size, overhang faces, build volume) and the
physical verifications a printed part needs (fit, layer strength, first
article). The rule numbers are unchanged from 1.0.0.
"""

from __future__ import annotations

from twin_core.dfm.profiles.model import DfmProfile, DfmRule, RuleKind, VerificationRule

_GUIDE = (
    "General FDM design guidance, widely published in 3D printing design guides; "
    "a heuristic for a 0.4 mm nozzle, not a limit of any machine"
)

PROFILE = DfmProfile(
    id="fdm",
    version="1.1.0",
    process="FDM",
    title="FDM 3D printing (starter heuristics)",
    aliases=[
        "fdm",
        "fff",
        "fused deposition",
        "fused filament",
        "3d print",
        "3d printed",
        "3d printing",
    ],
    notes=(
        "Starter heuristics for interactive guidance. The machine limits (build volume, "
        "and any wall, feature or overhang limit the machine records) come from the "
        "machine library when a machine is selected. Bridges and support access stay "
        "guidance: no exact check measures them yet."
    ),
    exact_checks=[
        "fdm.min_wall",
        "fdm.min_feature",
        "fdm.overhang",
        "fdm.build_volume",
        "fdm.min_hole",
    ],
    verifications=[
        VerificationRule(
            id="fdm.fit_test",
            title="Fit test of a printed sample",
            kind="physical_test",
            when="tolerances_stated",
            needed="A printed sample fitted to its mating parts, with the measured fit "
            "against the stated tolerances (photo, measurement or note).",
            source="FDM parts shrink and vary with machine, material and orientation; a "
            "stated tolerance is only confirmed on a printed part",
        ),
        VerificationRule(
            id="fdm.layer_strength",
            title="Strength across the layers",
            kind="physical_test",
            when="functional_use",
            needed="A load or use test of a part printed in the intended orientation and "
            "material, with the result.",
            source="FDM parts are weakest between layers; no geometric check measures the "
            "strength of a printed part",
        ),
        VerificationRule(
            id="fdm.first_article",
            title="First article on the selected machine",
            kind="physical_test",
            when="production_quantity",
            needed="A first part printed on the selected machine and material, inspected "
            "against the model (dimensions, surface, supports removed).",
            source="Machine, filament and slicer settings decide the printed result; a "
            "first article confirms them before a batch",
        ),
    ],
    rules=[
        DfmRule(
            id="fdm.min_wall",
            title="Minimum wall thickness",
            check="min",
            quantity="wall_thickness",
            also_reads=["thinnest_section"],
            limit=0.8,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (two 0.4 mm extrusion widths)",
            machine_override="min_wall_mm",
            message="The wall is about {measured} {unit}; FDM walls under {limit} {unit} "
            "print weak or with gaps.",
            fix="Thicken the wall to at least {limit} {unit}.",
            fix_changes=["wall_thickness"],
            variant_cue="walls at least 0.8 mm thick",
        ),
        DfmRule(
            id="fdm.min_feature",
            title="Minimum feature size",
            check="min",
            quantity="feature_size",
            limit=2.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (small pins and details under 2 mm print poorly)",
            machine_override="min_feature_mm",
            message="A feature is about {measured} {unit}; details under {limit} {unit} "
            "may not print cleanly.",
            fix="Make the feature at least {limit} {unit}, or drop it.",
            fix_changes=["feature_size"],
            variant_cue="no details smaller than about 2 mm",
        ),
        DfmRule(
            id="fdm.overhang",
            title="Overhang angle",
            check="overhang",
            quantity="faces",
            unit="deg",
            limit=45.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (faces up to 45 degrees from vertical print without support)",
            machine_override="max_overhang_deg",
            message="{count} face(s) lean up to {measured} {unit} from vertical; past "
            "{limit} {unit} they need supports.",
            fix="Add a chamfer or reorient the part so faces lean at most {limit} {unit}; "
            "otherwise print with supports.",
            fix_changes=["orientation", "chamfer"],
            variant_cue="overhangs no steeper than 45 degrees, chamfers instead of flat undersides",
        ),
        DfmRule(
            id="fdm.bridge",
            title="Unsupported bridge length",
            check="max",
            quantity="bridge_length",
            limit=10.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (bridges over about 10 mm sag)",
            message="An unsupported bridge spans about {measured} {unit}; spans over "
            "{limit} {unit} sag.",
            fix="Shorten the span to {limit} {unit} or add a support pillar or arch.",
            fix_changes=["bridge_length"],
            variant_cue="short spans, arches instead of long flat bridges",
        ),
        DfmRule(
            id="fdm.build_volume",
            title="Build volume fit",
            check="envelope",
            quantity="envelope",
            kind=RuleKind.MACHINE_LIMIT,
            machine_capability="build_volume_mm",
            source="The selected machine's build volume, from the machine library entry "
            "and the source it cites",
            message="The part is about {measured} {unit}; the machine's build volume is "
            "{limit} {unit}.",
            fix="Split the part, scale it down, or choose a larger machine.",
            fix_changes=["envelope", "machine_or_supplier_profile"],
            variant_cue="sized to fit the printer's build volume",
        ),
        DfmRule(
            id="fdm.min_hole",
            title="Minimum hole diameter",
            check="min",
            quantity="hole_diameter",
            limit=2.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (holes under 2 mm close up; drill them after printing)",
            message="A hole is about {measured} {unit}; holes under {limit} {unit} close up "
            "when printed.",
            fix="Make the hole at least {limit} {unit}, or drill it after printing.",
            fix_changes=["hole_diameter"],
            variant_cue="holes of 2 mm or more",
        ),
        DfmRule(
            id="fdm.support_access",
            title="Support removal access",
            check="support_access",
            quantity="has_internal_cavity",
            unit="ratio",
            kind=RuleKind.HEURISTIC,
            source="MetaForge heuristic: supports printed inside an enclosed cavity "
            "cannot be removed",
            message="Faces that need supports sit inside an enclosed cavity, where the "
            "supports cannot be reached.",
            fix="Open the cavity, split the part, or reshape the inside so it needs no supports.",
            fix_changes=["orientation", "split"],
            variant_cue="no enclosed cavities that would trap supports",
        ),
    ],
)
