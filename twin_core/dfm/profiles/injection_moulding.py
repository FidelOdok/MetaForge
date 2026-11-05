"""Injection moulding starter profile.

General guidance widely published in injection moulding design guides:
walls in a modest band so they fill and cool evenly, draft on faces in the
pull direction, ribs thinner than the wall they stiffen. Heuristics for
thermoplastics, not limits of any mould shop.
"""

from __future__ import annotations

from twin_core.dfm.profiles.model import DfmProfile, DfmRule, RuleKind

_GUIDE = (
    "General injection moulding design guidance for thermoplastics, widely published in "
    "moulding design guides; a heuristic, not a limit of any mould shop"
)

PROFILE = DfmProfile(
    id="injection_moulding",
    version="1.0.0",
    process="Injection moulding",
    title="Injection moulding (starter heuristics)",
    aliases=["injection mould", "injection mold", "moulding", "molding", "moulded", "molded"],
    rules=[
        DfmRule(
            id="injection_moulding.min_wall",
            title="Minimum wall thickness",
            check="min",
            quantity="wall_thickness",
            also_reads=["thinnest_section"],
            limit=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (walls under about 1 mm may not fill)",
            message="A wall is about {measured} {unit}; moulded walls under {limit} {unit} "
            "may not fill.",
            fix="Thicken the wall to at least {limit} {unit}.",
            fix_changes=["wall_thickness"],
            variant_cue="even walls between 1 and 4 mm",
        ),
        DfmRule(
            id="injection_moulding.max_wall",
            title="Maximum wall thickness",
            check="max",
            quantity="wall_thickness",
            limit=4.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (walls over about 4 mm sink and take long to cool)",
            message="A wall is about {measured} {unit}; moulded walls over {limit} {unit} "
            "sink and cool slowly.",
            fix="Core the section out to a wall of at most {limit} {unit}, with ribs if it "
            "needs stiffness.",
            fix_changes=["wall_thickness"],
            variant_cue="cored-out thick sections with ribs",
        ),
        DfmRule(
            id="injection_moulding.wall_uniformity",
            title="Wall uniformity",
            check="max",
            quantity="wall_thickness_max",
            relative_to="wall_thickness",
            factor=1.5,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (neighbouring walls differing by more than about 1.5 times "
            "cool unevenly and warp)",
            message="The thickest wall is about {measured} {unit}; more than {limit} {unit} "
            "(1.5 times the thinnest) cools unevenly.",
            fix="Even out the walls to within 1.5 times of each other, with gradual transitions.",
            fix_changes=["wall_thickness"],
            variant_cue="uniform wall thickness with gradual transitions",
        ),
        DfmRule(
            id="injection_moulding.draft",
            title="Draft angle",
            check="min",
            quantity="draft_angle",
            unit="deg",
            limit=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (faces in the pull direction need about 1 degree of draft to "
            "release from the mould)",
            message="A face has about {measured} {unit} of draft; at least {limit} {unit} "
            "helps it release from the mould.",
            fix="Add at least {limit} {unit} of draft to faces parallel to the pull direction.",
            fix_changes=["draft_angle"],
            variant_cue="slightly tapered sides (draft) in the pull direction",
        ),
        DfmRule(
            id="injection_moulding.rib_thickness",
            title="Rib thickness",
            check="max",
            quantity="rib_thickness",
            relative_to="wall_thickness",
            factor=0.6,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (ribs thicker than about 60 percent of the wall leave sink marks)",
            message="A rib is about {measured} {unit}; ribs over {limit} {unit} (60 percent "
            "of the wall) leave sink marks.",
            fix="Make ribs at most {limit} {unit} thick.",
            fix_changes=["rib_thickness"],
            variant_cue="thin ribs instead of thick walls",
        ),
    ],
)
