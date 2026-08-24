"""Design IR validation (requirements doc §6.3) beyond what Pydantic already enforces.

Structural validation and per-field bounds sanity (positive dimensions,
non-zero radii, pattern counts > 1, angles in (0, 360]) happen at
construction time via the ``Field(gt=..., le=...)`` constraints in
``models.py`` -- an out-of-bounds ``DesignIR`` never exists as an object,
Pydantic raises first. What's left, and what this module covers, is
whatever can only be checked against the *whole* document: referential
integrity and duplicate entity ids.
"""

from __future__ import annotations

import structlog

from observability.tracing import get_tracer
from twin_core.design_ir.models import DesignIR

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.design_ir.validation")


def _ref_field_values(entity: object) -> dict[str, str | list[str]]:
    """Every field on ``entity`` whose name ends in ``_ref``/``_refs``, by naming
    convention -- not a hand-maintained per-type registry, so a new entity type
    participates automatically as long as it follows the convention.

    Fields like ``edge_selectors``/``face_selectors`` deliberately don't match
    this suffix: they hold real FreeCAD topology names (``"Edge3"``), not
    entity ids, and must never be checked against ``seen_ids`` below.
    """
    model_fields = type(entity).model_fields  # type: ignore[attr-defined]
    return {
        name: getattr(entity, name)
        for name in model_fields
        if name.endswith("_ref") or name.endswith("_refs")
    }


def validate_referential_integrity(doc: DesignIR) -> list[str]:
    """Every ``*_ref``/``*_refs`` must resolve to an entity strictly earlier in
    ``doc.entities``, and no two entities may share an id.

    Checking each entity's own refs before adding its id to the seen set
    means a self-reference is correctly rejected too (an entity is never
    "earlier" than itself).
    """
    errors: list[str] = []
    seen_ids: set[str] = set()

    for index, entity in enumerate(doc.entities):
        if entity.id in seen_ids:
            errors.append(f"entity[{index}] (id={entity.id!r}): duplicate id")

        for field_name, value in _ref_field_values(entity).items():
            refs = value if isinstance(value, list) else [value]
            for ref in refs:
                if not ref:
                    continue
                if ref not in seen_ids:
                    errors.append(
                        f"entity[{index}] (id={entity.id!r}): {field_name} references "
                        f"{ref!r}, which is not an earlier entity id in this document"
                    )

        seen_ids.add(entity.id)

    if errors:
        logger.warning("design_ir_referential_integrity_failed", error_count=len(errors))
    return errors


def validate_design_ir(doc: DesignIR) -> list[str]:
    """Full validation pipeline for an already-constructed Design IR document.

    Returns a list of human-readable error strings (empty if valid), matching
    this repo's ``validate_preconditions``/``validate_output`` convention
    rather than raising -- callers decide whether to reject or report.
    """
    with tracer.start_as_current_span("design_ir.validate") as span:
        span.set_attribute("entity_count", len(doc.entities))
        errors = validate_referential_integrity(doc)
        span.set_attribute("error_count", len(errors))
        return errors
