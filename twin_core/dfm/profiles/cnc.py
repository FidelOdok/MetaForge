"""CNC machining starter profile.

General guidance widely published in CNC machining design guides: a round
cutter leaves a radius in every internal corner, deep narrow pockets need
long tools, and thin walls chatter. Heuristics, not limits of any machine or
shop. The working envelope is read from the selected machine.
"""

from __future__ import annotations

from twin_core.dfm.profiles.model import DfmProfile, DfmRule, RuleKind

_GUIDE = (
    "General CNC machining design guidance, widely published in machining design "
    "guides; a heuristic, not a limit of any machine or shop"
)

PROFILE = DfmProfile(
    id="cnc",
    version="1.0.0",
    process="CNC machining",
    title="CNC machining (starter heuristics)",
    aliases=["cnc", "milling", "machining", "machined", "router", "routing", "lathe", "turning"],
    rules=[
        DfmRule(
            id="cnc.internal_corner_radius",
            title="Internal corner radius",
            check="min",
            quantity="internal_corner_radius",
            limit=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (a round cutter cannot cut a sharp internal corner; about "
            "1 mm is a practical small radius)",
            machine_override="min_tool_diameter_mm",
            machine_override_scale=0.5,
            message="An internal corner has a radius of about {measured} {unit}; a round "
            "cutter needs at least {limit} {unit}.",
            fix="Give internal corners a radius of at least {limit} {unit}, or use "
            "dog-bone reliefs.",
            fix_changes=["internal_corner_radius"],
            variant_cue="rounded internal corners, no sharp inside corners",
        ),
        DfmRule(
            id="cnc.corner_to_depth",
            title="Corner radius for the pocket depth",
            check="min",
            quantity="internal_corner_radius",
            relative_to="cavity_depth",
            factor=1.0 / 3.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (internal radii of at least a third of the pocket depth let "
            "a standard tool reach the bottom)",
            message="An internal corner of about {measured} {unit} is tight for the pocket "
            "depth; at least {limit} {unit} lets a standard tool reach.",
            fix="Increase the corner radius to {limit} {unit} or make the pocket shallower.",
            fix_changes=["internal_corner_radius", "cavity_depth"],
            variant_cue="shallow pockets with generous corner radii",
        ),
        DfmRule(
            id="cnc.pocket_depth",
            title="Pocket depth to width",
            check="max",
            quantity="cavity_depth",
            relative_to="cavity_width",
            factor=4.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (pockets deeper than about four times their width need long, "
            "flexible tools)",
            message="A pocket is about {measured} {unit} deep; more than {limit} {unit} "
            "(four times its width) needs long tools.",
            fix="Make the pocket at most {limit} {unit} deep or wider.",
            fix_changes=["cavity_depth", "cavity_width"],
            variant_cue="no deep narrow pockets",
        ),
        DfmRule(
            id="cnc.min_wall_metal",
            title="Minimum wall, metal",
            check="min",
            quantity="wall_thickness",
            also_reads=["thinnest_section"],
            limit=0.8,
            material_families=["metal"],
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (metal walls under about 0.8 mm chatter and deflect)",
            machine_override="min_wall_mm",
            message="A wall is about {measured} {unit}; machined metal walls under {limit} "
            "{unit} chatter and deflect.",
            fix="Thicken the wall to at least {limit} {unit}.",
            fix_changes=["wall_thickness"],
            variant_cue="metal walls at least 0.8 mm",
        ),
        DfmRule(
            id="cnc.min_wall_plastic",
            title="Minimum wall, plastic",
            check="min",
            quantity="wall_thickness",
            also_reads=["thinnest_section"],
            limit=1.5,
            material_families=["plastic"],
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (plastic walls under about 1.5 mm warp and melt under the cutter)",
            machine_override="min_wall_mm",
            message="A wall is about {measured} {unit}; machined plastic walls under "
            "{limit} {unit} warp.",
            fix="Thicken the wall to at least {limit} {unit}.",
            fix_changes=["wall_thickness"],
            variant_cue="plastic walls at least 1.5 mm",
        ),
        DfmRule(
            id="cnc.min_hole",
            title="Minimum hole diameter",
            check="min",
            quantity="hole_diameter",
            limit=1.0,
            kind=RuleKind.GENERAL_GUIDANCE,
            source=_GUIDE + " (holes under about 1 mm need micro drills)",
            message="A hole is about {measured} {unit}; holes under {limit} {unit} need "
            "micro drills.",
            fix="Make the hole at least {limit} {unit}.",
            fix_changes=["hole_diameter"],
            variant_cue="standard drill sizes",
        ),
        DfmRule(
            id="cnc.envelope",
            title="Machine travel fit",
            check="envelope",
            quantity="envelope",
            kind=RuleKind.MACHINE_LIMIT,
            machine_capability="travel_mm",
            source="The selected machine's travel, from the machine library entry and the "
            "source it cites",
            message="The part is about {measured} {unit}; the machine's travel is {limit} {unit}.",
            fix="Split the part or choose a machine with more travel.",
            fix_changes=["envelope", "machine_or_supplier_profile"],
            variant_cue="sized to fit the machine's travel",
        ),
    ],
)
