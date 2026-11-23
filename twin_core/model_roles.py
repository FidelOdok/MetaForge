"""What a cad_model is for: the product itself, or something made to serve it.

A cad_model's ``model_role`` metadata says which. ``design`` (the default) is
the product's geometry. Two roles are not:

* ``analysis`` (FORGE-596): a model built only to analyse the design, such as an
  FEA idealisation with simplified supports.
* ``component``: a bought component's own 3D model, made so a board's 3D export
  shows it (a footprint model). It is part of the board's model, never a part
  of the product to assemble or release on its own.

The gate's geometry, DFM, assembly and release checks judge the product only,
so each of them asks :func:`is_product_geometry`.
"""

from __future__ import annotations

from typing import Any

DESIGN = "design"
ANALYSIS = "analysis"
COMPONENT = "component"
ROLES = (DESIGN, ANALYSIS, COMPONENT)
NOT_PRODUCT = frozenset({ANALYSIS, COMPONENT})


def role_of(metadata: Any) -> str:
    """The model's role; ``design`` when none is recorded."""
    meta = metadata if isinstance(metadata, dict) else {}
    return str(meta.get("model_role") or DESIGN)


def is_product_geometry(metadata: Any) -> bool:
    """Whether a cad_model with this metadata is the product's own geometry."""
    return role_of(metadata) not in NOT_PRODUCT
