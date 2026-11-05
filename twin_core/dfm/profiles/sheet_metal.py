"""Sheet metal starter profile.

General guidance widely published in sheet metal design guides, as multiples
of the sheet thickness: an inside bend radius of at least one thickness,
flanges of at least four, holes at least one thickness across and kept away
from bends. Heuristics, not limits of any press brake or shop.
"""

from __future__ import annotations

from twin_core.dfm.profiles.model import DfmProfile, DfmRule, RuleKind

_GUIDE = (
    "General sheet metal design guidance, widely published in sheet metal design guides; "
    "a heuristic in multiples of the sheet thickness, not a limit of any press brake or shop"
)

PROFILE = DfmProfile(
    id="sheet_metal",
    version="1.0.0",
    process="Sheet metal",
    title="Sheet metal bending (starter heuristics)",
    aliases=["sheet metal", "sheet", "bent", "bending", "press brake", "folded"],
    rules=[
        DfmRule(
            id="sheet_metal.bend_radius",
            title="Inside bend radius",
            check="min",
            quantity="bend_radius",
            relative_to="sheet_thickness",
            factor=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (an inside radius of at least one thickness avoids cracking)",
            machine_override="min_bend_radius_mm",
            message="A bend has an inside radius of about {measured} {unit}; at least "
            "{limit} {unit} (one sheet thickness) avoids cracking.",
            fix="Use an inside bend radius of at least {limit} {unit}.",
            fix_changes=["bend_radius"],
            variant_cue="generous bend radii",
        ),
        DfmRule(
            id="sheet_metal.flange_length",
            title="Minimum flange length",
            check="min",
            quantity="flange_length",
            relative_to="sheet_thickness",
            factor=4.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (a flange shorter than about four thicknesses cannot be held "
            "in the brake)",
            message="A flange is about {measured} {unit}; at least {limit} {unit} (four "
            "thicknesses) can be bent.",
            fix="Lengthen the flange to at least {limit} {unit}.",
            fix_changes=["flange_length"],
            variant_cue="flanges at least four sheet thicknesses long",
        ),
        DfmRule(
            id="sheet_metal.hole_to_bend",
            title="Hole distance from a bend",
            check="min",
            quantity="hole_to_bend",
            relative_to="sheet_thickness",
            factor=2.5,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (holes closer than about 2.5 thicknesses to a bend distort)",
            message="A hole is about {measured} {unit} from a bend; closer than {limit} "
            "{unit} it distorts.",
            fix="Move the hole at least {limit} {unit} from the bend, or add bend relief.",
            fix_changes=["hole_to_bend"],
            variant_cue="holes well away from bends",
        ),
        DfmRule(
            id="sheet_metal.min_hole",
            title="Minimum hole diameter",
            check="min",
            quantity="hole_diameter",
            relative_to="sheet_thickness",
            factor=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (holes smaller than one thickness tear when punched)",
            message="A hole is about {measured} {unit}; at least {limit} {unit} (one sheet "
            "thickness) cuts cleanly.",
            fix="Make the hole at least {limit} {unit}.",
            fix_changes=["hole_diameter"],
            variant_cue="holes at least one sheet thickness across",
        ),
    ],
)
