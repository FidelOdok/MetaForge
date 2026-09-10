"""Shared joint-mapping errors for the assembly export builders
(URDF/SDF in ``operations.py``, USD in ``usd_export.py``).

A tiny standalone module rather than living in ``operations.py`` (which
already imports ``usd_export``) so both format-specific builders can raise
the same exception types without a circular import.
"""

from __future__ import annotations


class UnsupportedJointTypeError(ValueError):
    """Raised for a FreeCAD joint type with no faithful single-joint
    equivalent in the target format (e.g. ``cylindrical`` for URDF/SDF/USD,
    or ``ball`` for URDF specifically) -- see each builder's own
    joint-type-map module comment for the per-format reasoning."""


class MissingJointLimitsError(ValueError):
    """Raised when a joint that requires an explicit range (a ``slider``/
    prismatic joint in URDF, SDF, or USD) has no caller-supplied ``limits``
    -- MetaForge's joint metadata never captures one, so fabricating a
    value would silently claim data that doesn't exist."""
