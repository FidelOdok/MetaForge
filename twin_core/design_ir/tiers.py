"""Operation-to-tier mapping (requirements doc §6.2), for capability-based routing.

This is the schema-side half of an adapter's ``capabilities()`` declaration
(§6.6.1's ``feature_tree_model`` flag): routing code looks up an entity's
tier here, then checks whether the target adapter declares support for that
tier. Adding a new entity type means adding one line here, not touching any
routing code.
"""

from enum import StrEnum


class Tier(StrEnum):
    """A §6.2 operation tier."""

    PRIMITIVE_SOLID = "primitive_solid"  # 6.2.1 -- FreeCAD only
    PARAMETRIC_TEMPLATE = "parametric_template"  # 6.2.2 -- both adapters
    FEATURE_TREE = "feature_tree"  # 6.2.3 -- FreeCAD only
    BOOLEAN = "boolean"  # 6.2.4 -- both adapters
    TRANSFORM = "transform"  # 6.2.5 -- FreeCAD only
    ASSEMBLY = "assembly"  # 6.2.6 -- both adapters (typed joints FreeCAD only)


OP_TIER: dict[str, Tier] = {
    "create_primitive": Tier.PRIMITIVE_SOLID,
    "create_parametric": Tier.PARAMETRIC_TEMPLATE,
    "create_body": Tier.FEATURE_TREE,
    "sketch": Tier.FEATURE_TREE,
    "pad": Tier.FEATURE_TREE,
    "pocket": Tier.FEATURE_TREE,
    "revolve": Tier.FEATURE_TREE,
    "loft": Tier.FEATURE_TREE,
    "sweep": Tier.FEATURE_TREE,
    "fillet_edges": Tier.FEATURE_TREE,
    "chamfer_edges": Tier.FEATURE_TREE,
    "linear_pattern": Tier.FEATURE_TREE,
    "polar_pattern": Tier.FEATURE_TREE,
    "mirror": Tier.FEATURE_TREE,
    "shell": Tier.FEATURE_TREE,
    "boolean": Tier.BOOLEAN,
    "fillet": Tier.BOOLEAN,  # Plain Part tier, grouped with boolean: no body, target_ref only
    "chamfer": Tier.BOOLEAN,
    "transform": Tier.TRANSFORM,
    "create_assembly": Tier.ASSEMBLY,
    "place": Tier.ASSEMBLY,
    "joint": Tier.ASSEMBLY,
}


def tier_for(op: str) -> Tier:
    """Return the tier for an operation name.

    Raises:
        KeyError: If ``op`` isn't a known Design IR operation.
    """
    return OP_TIER[op]
