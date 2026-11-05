"""What a displayable artifact is, and how far it can be trusted (C3D slice 2).

Every artifact a screen can show as "the product" (a concept image, an AI
mesh, a structured block mesh, an engineering model, a sketch) carries a
**representation descriptor**:

``level``
    ``illustration_image`` (a picture or sketch), ``L0`` (an AI mesh, no CAD
    semantics), ``L1`` (a structured mesh of named parts with estimated
    sizes), ``L2`` (a preview mesh from feature definitions, or an exact
    model not yet validated) or ``L3`` (tessellation of an exact, *validated*
    B-Rep).
``source_revision``
    The item revision it derives from (``KEY@n``), or ``None``.
``authority``
    ``illustrative``, ``structured``, ``preview``, ``exact`` or ``validated``.
``component_ids``
    The stable component ids it shows (``CMP-...``).
``generated_by``
    ``{tool, operation_id}``.

The descriptor is stored in the work product's metadata
(``metadata.representation``) by the generators that know it (the L1 block
mesh). Every older artifact gets one derived at read time from its type and
geometry state, so nothing is backfilled.

Two rules are enforced on every read (:func:`guard`), whatever was stored:

* ``L3`` only for a ``cad_model`` revision whose geometry state is
  ``validated`` or ``released``. Anything else claiming ``L3`` reads ``L2``.
* ``L0``, ``L1`` and pictures are never ``exact`` or ``validated``. A stored
  claim above their ceiling is clamped down.

A clamp is a mislabel the generator should not have written; it is logged and
counted (``metaforge_representation_mislabel_total``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.geometry_state import GeometryState, geometry_state_of, validated_states

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.representation")

#: Node metadata key the descriptor is stored under.
REPRESENTATION_KEY = "representation"
#: Node metadata keys carrying component ids (see ``twin_core.component_identity``).
COMPONENT_ID_KEY = "component_id"
PART_COMPONENT_IDS_KEY = "part_component_ids"

#: The tool that makes AI meshes (L0) and the one that makes block meshes (L1).
CONCEPT_MESH_TOOL = "twin.generate_concept_mesh"
STRUCTURED_MESH_TOOL = "twin.render_structured_mesh"


class RepresentationLevel(StrEnum):
    ILLUSTRATION_IMAGE = "illustration_image"
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class RepresentationAuthority(StrEnum):
    ILLUSTRATIVE = "illustrative"
    STRUCTURED = "structured"
    PREVIEW = "preview"
    EXACT = "exact"
    VALIDATED = "validated"


#: Authorities in increasing order of trust.
_AUTHORITY_ORDER: tuple[RepresentationAuthority, ...] = tuple(RepresentationAuthority)

#: The most a level may ever claim.
_CEILING: dict[RepresentationLevel, RepresentationAuthority] = {
    RepresentationLevel.ILLUSTRATION_IMAGE: RepresentationAuthority.ILLUSTRATIVE,
    RepresentationLevel.L0: RepresentationAuthority.ILLUSTRATIVE,
    RepresentationLevel.L1: RepresentationAuthority.STRUCTURED,
    RepresentationLevel.L2: RepresentationAuthority.EXACT,
    RepresentationLevel.L3: RepresentationAuthority.VALIDATED,
}

#: How much a screen prefers to show each level (higher first, SYNC-010).
DISPLAY_RANK: dict[RepresentationLevel, int] = {
    RepresentationLevel.L3: 5,
    RepresentationLevel.L2: 4,
    RepresentationLevel.L1: 3,
    RepresentationLevel.L0: 2,
    RepresentationLevel.ILLUSTRATION_IMAGE: 1,
}

#: The maturity word Create shows for each authority (section 16.2 item 3).
_LABEL: dict[RepresentationAuthority, str] = {
    RepresentationAuthority.ILLUSTRATIVE: "Illustrative",
    RepresentationAuthority.STRUCTURED: "Preview",
    RepresentationAuthority.PREVIEW: "Preview",
    RepresentationAuthority.EXACT: "Exact",
    RepresentationAuthority.VALIDATED: "Validated",
}

#: Picture and sketch types: their level is ``illustration_image``.
_PICTURE_TYPES = frozenset({"image", "design_sketch", "generated_sketch", "sketch_strokes"})
_MESH_FORMATS = frozenset({"glb", "gltf", "stl", "3mf", "obj", "ply"})

_metrics: Any = None


def _record_mislabel(level: str, claimed: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_representation_mislabel(level, claimed)
    except Exception:  # noqa: BLE001 - a metric never fails a read
        pass


def _item_ref(meta: dict[str, Any]) -> str | None:
    key = meta.get("item_key")
    rev = meta.get("item_revision")
    if key and isinstance(rev, int) and not isinstance(rev, bool):
        return f"{key}@{rev}"
    return None


def component_ids_of(meta: dict[str, Any] | None) -> list[str]:
    """Every component id a node's metadata names, in a stable order."""
    m = meta or {}
    out: list[str] = []
    single = m.get(COMPONENT_ID_KEY)
    if isinstance(single, str) and single:
        out.append(single)
    parts = m.get(PART_COMPONENT_IDS_KEY)
    if isinstance(parts, dict):
        out.extend(str(v) for v in parts.values() if isinstance(v, str) and v)
    stored = m.get(REPRESENTATION_KEY)
    if isinstance(stored, dict) and isinstance(stored.get("component_ids"), list):
        out.extend(str(v) for v in stored["component_ids"] if isinstance(v, str) and v)
    return list(dict.fromkeys(out))


def _generated_by(meta: dict[str, Any]) -> dict[str, Any]:
    tool = meta.get("generated_by") or meta.get("source_tool") or meta.get("recorded_by")
    op = meta.get("operation_id") or meta.get("op_id") or meta.get("task_id") or meta.get("job_id")
    return {"tool": str(tool) if tool else None, "operation_id": str(op) if op else None}


def _derive(wp_type: str, meta: dict[str, Any], fmt: str) -> dict[str, Any] | None:
    """The descriptor implied by a node's type and geometry state, or ``None``."""
    if wp_type == "cad_model":
        state = geometry_state_of(wp_type, meta)
        if state in validated_states():
            level, auth = RepresentationLevel.L3, RepresentationAuthority.VALIDATED
        elif state in (GeometryState.FAILED, GeometryState.PREVIEW):
            level, auth = RepresentationLevel.L2, RepresentationAuthority.PREVIEW
        else:
            # An exact B-Rep exists but nothing validated it: not L3.
            level, auth = RepresentationLevel.L2, RepresentationAuthority.EXACT
        return {"level": level.value, "authority": auth.value, "source_revision": _item_ref(meta)}
    if wp_type == "render":
        tool = str(meta.get("generated_by") or "")
        if tool == STRUCTURED_MESH_TOOL:
            level, auth = RepresentationLevel.L1, RepresentationAuthority.STRUCTURED
        elif tool == CONCEPT_MESH_TOOL or fmt in _MESH_FORMATS:
            level, auth = RepresentationLevel.L0, RepresentationAuthority.ILLUSTRATIVE
        else:
            level, auth = (
                RepresentationLevel.ILLUSTRATION_IMAGE,
                RepresentationAuthority.ILLUSTRATIVE,
            )
        return {
            "level": level.value,
            "authority": auth.value,
            "source_revision": _item_ref(meta) or meta.get("source_item_ref"),
        }
    if wp_type in _PICTURE_TYPES:
        return {
            "level": RepresentationLevel.ILLUSTRATION_IMAGE.value,
            "authority": RepresentationAuthority.ILLUSTRATIVE.value,
            "source_revision": _item_ref(meta),
        }
    return None


def guard(
    descriptor: dict[str, Any], wp_type: str, meta: dict[str, Any], fmt: str = ""
) -> tuple[dict[str, Any], list[str]]:
    """``descriptor`` with the two truth rules applied, and what was corrected."""
    out = dict(descriptor)
    fixes: list[str] = []
    fallback = _derive(wp_type, meta, fmt) or {
        "level": RepresentationLevel.ILLUSTRATION_IMAGE.value
    }
    try:
        level = RepresentationLevel(str(out.get("level")))
    except ValueError:
        fixes.append(f"unknown level {out.get('level')!r}")
        level = RepresentationLevel(fallback["level"])
        out["level"] = level.value
    if level is RepresentationLevel.L3:
        state = geometry_state_of(wp_type, meta) if wp_type == "cad_model" else None
        if state not in validated_states():
            fixes.append("L3 without a validated exact revision")
            # What the artifact actually is: an unvalidated CAD revision is
            # L2; anything else falls back to the level its type implies.
            level = RepresentationLevel(fallback["level"])
            if level is RepresentationLevel.L3:
                level = RepresentationLevel.L2
            out["level"] = level.value
    try:
        auth = RepresentationAuthority(str(out.get("authority")))
    except ValueError:
        fixes.append(f"unknown authority {out.get('authority')!r}")
        auth = _CEILING[level]
    ceiling = _CEILING[level]
    if _AUTHORITY_ORDER.index(auth) > _AUTHORITY_ORDER.index(ceiling):
        fixes.append(f"{level.value} cannot be {auth.value}")
        auth = ceiling
    out["authority"] = auth.value
    return out, fixes


def representation_of(
    wp_type: str | None,
    metadata: dict[str, Any] | None,
    *,
    fmt: str | None = None,
) -> dict[str, Any] | None:
    """The descriptor of one work product revision, or ``None`` if it is not displayable.

    Stored descriptors are read and guarded; older artifacts get one derived
    from their type and geometry state. The result always has ``level``,
    ``authority``, ``label``, ``source_revision``, ``component_ids``,
    ``generated_by``, ``derived`` and ``display_rank``.
    """
    t = str(wp_type or "")
    meta = metadata or {}
    stored = meta.get(REPRESENTATION_KEY)
    derived = not isinstance(stored, dict)
    f = str(fmt or "").lower()
    base = _derive(t, meta, f) if derived else dict(stored or {})
    if base is None:
        return None
    guarded, fixes = guard(base, t, meta, f)
    if fixes:
        logger.warning(
            "representation_mislabel_guarded",
            wp_type=t,
            claimed_level=str(base.get("level")),
            claimed_authority=str(base.get("authority")),
            level=guarded["level"],
            authority=guarded["authority"],
            fixes=fixes,
        )
        _record_mislabel(str(guarded["level"]), str(base.get("authority") or "unknown"))
    level = RepresentationLevel(guarded["level"])
    auth = RepresentationAuthority(guarded["authority"])
    gen = guarded.get("generated_by")
    return {
        "level": level.value,
        "authority": auth.value,
        "label": _LABEL[auth],
        "source_revision": guarded.get("source_revision") or _item_ref(meta),
        "component_ids": component_ids_of(meta),
        "generated_by": gen if isinstance(gen, dict) else _generated_by(meta),
        "derived": derived,
        "display_rank": DISPLAY_RANK[level],
        "dimensions": guarded.get("dimensions")
        or ("estimated" if level in (RepresentationLevel.L0, RepresentationLevel.L1) else None),
        "guarded": fixes or None,
    }


def descriptor(
    level: RepresentationLevel,
    authority: RepresentationAuthority,
    *,
    source_revision: str | None,
    component_ids: list[str],
    tool: str,
    operation_id: str | None = None,
    dimensions: str | None = None,
) -> dict[str, Any]:
    """A descriptor for a generator to store in ``metadata.representation``.

    Refuses (``ValueError``) a combination the read-time guard would clamp,
    so a generator can never write a mislabel in the first place.
    """
    if _AUTHORITY_ORDER.index(authority) > _AUTHORITY_ORDER.index(_CEILING[level]):
        raise ValueError(f"a {level.value} representation cannot be {authority.value}")
    if level is RepresentationLevel.L3:
        raise ValueError("L3 is derived from a validated exact revision, never stamped")
    out: dict[str, Any] = {
        "level": level.value,
        "authority": authority.value,
        "source_revision": source_revision,
        "component_ids": list(component_ids),
        "generated_by": {"tool": tool, "operation_id": operation_id},
    }
    if dimensions:
        out["dimensions"] = dimensions
    return out


__all__ = [
    "COMPONENT_ID_KEY",
    "CONCEPT_MESH_TOOL",
    "DISPLAY_RANK",
    "PART_COMPONENT_IDS_KEY",
    "REPRESENTATION_KEY",
    "STRUCTURED_MESH_TOOL",
    "RepresentationAuthority",
    "RepresentationLevel",
    "component_ids_of",
    "descriptor",
    "guard",
    "representation_of",
]
