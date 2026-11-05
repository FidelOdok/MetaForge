"""Resin (SLA, MSLA, DLP) starter profile.

General guidance widely published in resin printing design guides: thin
unsupported walls, small details and holes, and drain holes for hollow parts
so uncured resin is not trapped. Heuristics, not limits of any printer. The
build volume comes from the selected machine.
"""

from __future__ import annotations

from twin_core.dfm.profiles.model import DfmProfile, DfmRule, RuleKind

_GUIDE = (
    "General resin printing design guidance, widely published in SLA and MSLA design "
    "guides; a heuristic, not a limit of any machine"
)

PROFILE = DfmProfile(
    id="resin",
    version="1.0.0",
    process="Resin printing",
    title="Resin printing, SLA / MSLA / DLP (starter heuristics)",
    aliases=["resin", "sla", "msla", "dlp", "stereolithography", "lcd print"],
    rules=[
        DfmRule(
            id="resin.min_wall",
            title="Minimum unsupported wall",
            check="min",
            quantity="wall_thickness",
            also_reads=["thinnest_section"],
            limit=0.6,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (unsupported walls under about 0.6 mm warp or break)",
            machine_override="min_wall_mm",
            message="The wall is about {measured} {unit}; unsupported resin walls under "
            "{limit} {unit} warp or break.",
            fix="Thicken the wall to at least {limit} {unit}.",
            fix_changes=["wall_thickness"],
            variant_cue="walls at least 0.6 mm thick",
        ),
        DfmRule(
            id="resin.min_feature",
            title="Minimum detail size",
            check="min",
            quantity="feature_size",
            limit=0.5,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (embossed and engraved details under about 0.5 mm are lost)",
            machine_override="min_feature_mm",
            message="A detail is about {measured} {unit}; details under {limit} {unit} "
            "may be lost.",
            fix="Make the detail at least {limit} {unit}.",
            fix_changes=["feature_size"],
            variant_cue="no details smaller than about 0.5 mm",
        ),
        DfmRule(
            id="resin.min_hole",
            title="Minimum hole diameter",
            check="min",
            quantity="hole_diameter",
            limit=0.5,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (holes under about 0.5 mm close up)",
            message="A hole is about {measured} {unit}; holes under {limit} {unit} close up.",
            fix="Make the hole at least {limit} {unit}, or drill it after curing.",
            fix_changes=["hole_diameter"],
            variant_cue="holes of 0.5 mm or more",
        ),
        DfmRule(
            id="resin.drainage",
            title="Drain holes for hollow parts",
            check="requires",
            quantity="has_internal_cavity",
            relative_to="drain_hole_diameter",
            limit=3.5,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (a hollow part needs drain holes of about 3.5 mm so uncured "
            "resin can escape)",
            message="The part is hollow and has no drain hole of {limit} {unit} or more, "
            "so resin can be trapped inside.",
            fix="Add at least one drain hole of {limit} {unit} or more near the lowest "
            "point, or make the part solid.",
            fix_changes=["drain_hole_diameter"],
            variant_cue="hollow parts with drain holes, or solid parts",
        ),
        DfmRule(
            id="resin.build_volume",
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
    ],
)
