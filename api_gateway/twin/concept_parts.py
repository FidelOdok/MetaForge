"""Concept parts: split the L0 concept into parts, map them, redo one part.

Spec sections 2 and 3 (L0), SYNC-001/002/003/007 and DFM-004. The AI concept
mesh (``twin.generate_concept_mesh``, L0) is one model with no parts. This
module works on it in three paid steps, each a background job held like the
concept mesh itself, and one free step a person takes:

``twin.segment_concept_mesh``
    Tripo splits the accepted concept into parts (``mesh/segment``). The
    input is the concept's own Tripo task id when it has one, else its GLB
    uploaded through the file API, else a URL. The result is a new revision of
    the concept (still L0, still illustrative), fitted to the same envelope,
    recording provider, model version, granularity, credits, task id and
    provenance. :func:`api_gateway.twin.glb_parts.read_parts` reads its parts;
    a model returned whole is recorded as ``not_segmented``, never guessed
    into parts.

**Mapping** (free): each part is proposed a component of the concept assembly
(``CMP-...``) from its label, its relative size and position against the L1
block layout, and the order, each with a confidence. An ambiguous or
unmatched part is a question for the person, never a guess. The person
confirms or corrects the mapping; only then is ``component_id`` written into
that part's node ``extras`` in our stored copy, so selection, appearance, DFM
and reconstruction work per component.

``twin.regenerate_concept_part``
    Generates one part again (Tripo text or image to model, as for a whole
    concept), fits it to that part's original box and pose, and swaps only that
    node in our copy: every other node's bytes stay identical.

``twin.complete_concept_part``
    Tripo ``mesh/complete`` seals (``quick_cap``) or completes
    (``ai_completion``) one part of the segmentation, swapped in the same way.

Every result is a new revision with lineage (``concept_lineage``: the
operation, the previous revision) and the status ``proposed``. The concept a
person accepted stays the accepted one until they accept the new revision
(:meth:`ConceptPartsService.decide`), as concept alternatives do.

Nothing here is evidence: every revision carries ``authority: illustrative``
and an illustrative label, and its dimensions are estimates.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.glb_parts import (
    REGENERATED_KEY,
    GlbPart,
    GlbPartsError,
    PartsReading,
    node_face_samples,
    node_mesh_bytes,
    read_parts,
    set_node_extras,
    split_glb,
    subset_glb,
    swap_part,
)
from api_gateway.twin.mesh_generation import (
    COMPLETION_MODES,
    MAX_PROMPT_CHARS,
    SEGMENT_GRANULARITIES,
    SEGMENT_MODEL_GEOMETRY,
    SEGMENT_MODEL_SEMANTIC,
    SEGMENT_MODELS,
    GeneratedMesh,
    MeshGenerationError,
    MeshGenerationTimeoutError,
    MeshGenerationUnavailableError,
    resolve_mesh_generator,
    timeout_from_env,
)
from observability.tracing import get_tracer
from twin_core.representation import (
    CONCEPT_MESH_TOOL,
    PART_COMPONENT_IDS_KEY,
    REPRESENTATION_KEY,
    RepresentationAuthority,
    RepresentationLevel,
    descriptor,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.concept_parts")

SEGMENT_TOOL = "twin.segment_concept_mesh"
REGENERATE_TOOL = "twin.regenerate_concept_part"
COMPLETE_TOOL = "twin.complete_concept_part"
#: Every tool that writes a revision of a concept mesh.
CONCEPT_FAMILY_TOOLS: frozenset[str] = frozenset(
    {CONCEPT_MESH_TOOL, SEGMENT_TOOL, REGENERATE_TOOL, COMPLETE_TOOL}
)
#: The paid ones this module adds.
PAID_TOOLS: tuple[str, ...] = (SEGMENT_TOOL, REGENERATE_TOOL, COMPLETE_TOOL)

SEGMENTED_LABEL = "AI 3D concept in parts, illustrative only"
REGENERATED_LABEL = "AI 3D concept with one part redone, illustrative only"
COMPLETED_LABEL = "AI 3D concept with one part sealed, illustrative only"
GLB_MIME = "model/gltf-binary"

STATUS_KEY = "concept_status"
LINEAGE_KEY = "concept_lineage"
MAPPING_KEY = "part_mapping"
PARTS_KEY = "concept_parts"
SEGMENTATION_KEY = "segmentation"
#: Each part's triangles as normal bins, for the mesh overhang check per node.
PART_FACES_KEY = "part_face_samples"

#: A proposal at least this sure, and this far ahead of the next candidate, is
#: offered as a proposal; anything less is asked.
CONFIDENT = 0.6
MARGIN = 0.1
#: Triangles for a regenerated part (a part needs fewer than a whole product).
PART_FACE_LIMIT = 40_000
#: The largest image a person may send for a part, decoded.
MAX_PART_IMAGE_BYTES = 10 * 1024 * 1024
_GRACE_S = 30.0

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _metric(name: str, *args: str) -> None:
    try:
        getattr(_collector(), name)(*args)
    except Exception:  # noqa: BLE001 - a metric never fails a step
        pass


class ConceptPartsError(ValueError):
    """A request that cannot be honoured. ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _wp_type(wp: Any) -> str:
    return str(getattr(getattr(wp, "type", None), "value", getattr(wp, "type", "")))


def _meta(wp: Any) -> dict[str, Any]:
    return dict(getattr(wp, "metadata", None) or {})


def is_concept_revision(wp: Any) -> bool:
    """A concept mesh, or a revision of one this module wrote."""
    return _wp_type(wp) == "render" and _meta(wp).get("generated_by") in CONCEPT_FAMILY_TOOLS


def status_of(wp: Any) -> str:
    """``accepted`` (an original concept mesh is), ``proposed``, ``superseded`` or ``rejected``."""
    raw = _meta(wp).get(STATUS_KEY)
    return str(raw) if raw in ("proposed", "superseded", "rejected", "accepted") else "accepted"


def root_of(wp: Any) -> str:
    return str(_meta(wp).get("concept_root_node_id") or getattr(wp, "id", ""))


def revision_of(wp: Any) -> int:
    raw = _meta(wp).get("concept_revision")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 1


def _created(wp: Any) -> str:
    stamp = getattr(wp, "created_at", None)
    return stamp.isoformat() if isinstance(stamp, datetime) else ""


def _pid(project_id: Any) -> UUID | None:
    if project_id in (None, ""):
        return None
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ConceptPartsError(f"{project_id!r} is not a project id", 400) from exc


def _node_uuid(node_id: Any, field: str = "node_id") -> UUID:
    try:
        return node_id if isinstance(node_id, UUID) else UUID(str(node_id).strip())
    except ValueError as exc:
        raise ConceptPartsError(f"'{field}' is not a node id: {node_id!r}", 400) from exc


# ---------------------------------------------------------------------------
# Part to component mapping (pure)
# ---------------------------------------------------------------------------


def _fractions(
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Each box as (centre, size), both as fractions of the union box, per axis."""
    lo = [min(b[0][i] for b in boxes) for i in range(3)]
    hi = [max(b[1][i] for b in boxes) for i in range(3)]
    span = [max(hi[i] - lo[i], 1e-9) for i in range(3)]
    out = []
    for bmin, bmax in boxes:
        centre = tuple(((bmin[i] + bmax[i]) / 2 - lo[i]) / span[i] for i in range(3))
        size = tuple((bmax[i] - bmin[i]) / span[i] for i in range(3))
        out.append((centre, size))
    return out  # type: ignore[return-value]


#: The ways a layout (x width, y depth, z up) may sit in glTF axes (x, y up, z):
#: which layout axis each glTF axis carries, and whether it is mirrored.
_TRANSFORMS: tuple[tuple[str, tuple[int, int, int], tuple[bool, bool]], ...] = tuple(
    (f"{name}{', mirrored ' + mir if mir else ''}", axes, flips)
    for name, axes in (("x=width, z=depth", (0, 2, 1)), ("z=width, x=depth", (1, 2, 0)))
    for mir, flips in (
        ("", (False, False)),
        ("x", (True, False)),
        ("z", (False, True)),
        ("x and z", (True, True)),
    )
)


def _layout_in_gltf(
    frac: tuple[tuple[float, float, float], tuple[float, float, float]],
    axes: tuple[int, int, int],
    flips: tuple[bool, bool],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    centre, size = frac
    c = [centre[axes[0]], centre[axes[1]], centre[axes[2]]]
    s = [size[axes[0]], size[axes[1]], size[axes[2]]]
    if flips[0]:
        c[0] = 1 - c[0]
    if flips[1]:
        c[2] = 1 - c[2]
    return (c[0], c[1], c[2]), (s[0], s[1], s[2])


def _geometry_score(
    part: tuple[tuple[float, float, float], tuple[float, float, float]],
    block: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> float:
    (pc, ps), (bc, bs) = part, block
    size = sum(min(ps[i], bs[i]) / max(ps[i], bs[i], 1e-9) for i in range(3)) / 3
    pos = 1 - sum(abs(pc[i] - bc[i]) for i in range(3)) / 3
    return max(0.0, min(1.0, 0.5 * size + 0.5 * max(0.0, pos)))


def propose_mapping(
    parts: list[dict[str, Any]],
    components: list[dict[str, Any]],
    layout_blocks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Proposals from parts to components, with a confidence, and questions.

    ``parts``: ``{name, label, bbox_min, bbox_max}`` (glTF axes).
    ``components``: ``{component_id, name}`` of the concept assembly, in order.
    ``layout_blocks``: ``{component_id: {position_mm, size_mm}}`` from the L1
    block layout (x width, y depth, z up), when there is one.

    A part is *proposed* a component when one candidate is at least
    :data:`CONFIDENT` and :data:`MARGIN` ahead of the next. Every other part
    is a *question* with its candidates; the order alone never decides.
    """
    from twin_core.component_identity import match_name

    names = {str(c["component_id"]): str(c.get("name") or c["component_id"]) for c in components}
    order = [str(c["component_id"]) for c in components]
    name_to_id = {v: k for k, v in names.items()}
    blocks: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {}
    for cid, block in (layout_blocks or {}).items():
        try:
            pos = [float(v) for v in block["position_mm"]]
            size = [float(v) for v in block["size_mm"]]
        except (KeyError, TypeError, ValueError):
            continue
        if cid in names and len(pos) == 3 and len(size) == 3:
            blocks[cid] = (
                (pos[0], pos[1], pos[2]),
                (pos[0] + size[0], pos[1] + size[1], pos[2] + size[2]),
            )
    part_fracs = (
        _fractions([(tuple(p["bbox_min"]), tuple(p["bbox_max"])) for p in parts])  # type: ignore[misc]
        if parts
        else []
    )
    block_ids = list(blocks)
    block_fracs = dict(zip(block_ids, _fractions([blocks[c] for c in block_ids]), strict=True)) if blocks else {}

    # Which way the layout sits in the model: the transform the parts agree with best.
    transform = None
    geo: list[dict[str, float]] = [{} for _ in parts]
    #: Parts whose best block differs between equally good transforms (a
    #: mirror-symmetric pair): geometry cannot tell them apart.
    symmetric: set[int] = set()
    if block_fracs and len(parts) >= 2:
        tried: list[tuple[float, str, list[dict[str, float]]]] = []
        for label, axes, flips in _TRANSFORMS:
            scores = [
                {
                    cid: _geometry_score(pf, _layout_in_gltf(bf, axes, flips))
                    for cid, bf in block_fracs.items()
                }
                for pf in part_fracs
            ]
            tried.append((sum(max(s.values()) for s in scores if s), label, scores))
        best_total = max(t[0] for t in tried)
        best = next(t for t in tried if t[0] == best_total)
        _total, transform, geo = best
        near = [t for t in tried if t[0] >= best_total - 0.02 * max(len(parts), 1)]
        for i in range(len(parts)):
            tops = {max(t[2][i], key=lambda c, s=t[2][i]: s[c]) for t in near if t[2][i]}
            if len(tops) > 1:
                symmetric.add(i)

    candidates: list[list[tuple[float, str, list[str]]]] = []
    same_count = len(parts) == len(order)
    for i, part in enumerate(parts):
        label = part.get("label")
        by_label = match_name(str(label), name_to_id) if label else None
        row: list[tuple[float, str, list[str]]] = []
        for cid in order:
            g = geo[i].get(cid)
            basis: list[str] = []
            if by_label == cid:
                score = 0.6 + 0.4 * g if g is not None else 0.85
                basis = ["label", "geometry"] if g is not None else ["label"]
            elif by_label is not None:
                score = 0.3 * g if g is not None else 0.0
                basis = ["geometry"] if g is not None else []
            else:
                score = 0.8 * g if g is not None else 0.0
                basis = ["geometry"] if g is not None else []
            if same_count and order.index(cid) == i:
                score += 0.05
                basis.append("order")
            row.append((round(min(score, 1.0), 3), cid, basis))
        row.sort(key=lambda t: -t[0])
        candidates.append(row)

    # One component per part by the best scores first; a second part of the
    # same component is asked, not assumed.
    pairs = sorted(
        ((score, i, cid) for i, row in enumerate(candidates) for score, cid, _b in row),
        key=lambda t: -t[0],
    )
    chosen: dict[int, str] = {}
    taken: set[str] = set()
    for score, i, cid in pairs:
        if i in chosen or cid in taken or score <= 0:
            continue
        chosen[i] = cid
        taken.add(cid)

    out_parts: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        row = candidates[i]
        cid = chosen.get(i)
        score = next((s for s, c, _b in row if c == cid), 0.0) if cid else 0.0
        basis = next((b for _s, c, b in row if c == cid), []) if cid else []
        runner_up = max((s for s, c, _b in row if c != cid), default=0.0)
        sure = cid is not None and score >= CONFIDENT and score - runner_up >= MARGIN
        # A mirror-symmetric pair is only told apart by a label.
        if i in symmetric and "label" not in basis:
            sure = False
        listed = [
            {"component_id": c, "name": names[c], "score": s} for s, c, _b in row[:3] if s > 0
        ]
        entry: dict[str, Any] = {
            "part": part["name"],
            "node_index": part.get("node_index"),
            "label": part.get("label"),
            "proposed_component_id": cid if sure else None,
            "proposed_name": names[cid] if sure and cid else None,
            "confidence": score if sure else round(score, 3),
            "basis": basis if sure else [],
            "candidates": listed,
            "state": "proposed" if sure else "question",
        }
        out_parts.append(entry)
        if not sure:
            if not listed:
                why = "nothing about it matches a component of the concept"
            elif cid is None:
                why = "every component it resembles is already matched to another part"
            else:
                why = f"it could be {', '.join(c['name'] for c in listed[:2])}"
            questions.append(
                {
                    "part": part["name"],
                    "question": f"Which component is {part['name']}? ({why})",
                    "options": [*listed, {"component_id": None, "name": "Not a component"}],
                }
            )
    matched = {e["proposed_component_id"] for e in out_parts if e["proposed_component_id"]}
    return {
        "status": "proposed",
        "transform": transform,
        "method": (
            "label match, then relative size and position against the L1 block layout, "
            "then order; a part is only proposed when one component is clearly ahead"
        ),
        "parts": out_parts,
        "questions": questions,
        "unmatched_components": [
            {"component_id": c, "name": names[c]} for c in order if c not in matched
        ],
    }


def confirmed_parts(mapping: Any) -> dict[str, str]:
    """``{part name: component id}`` of the parts a person confirmed."""
    out: dict[str, str] = {}
    if not isinstance(mapping, dict):
        return out
    for entry in mapping.get("parts") or []:
        if isinstance(entry, dict) and entry.get("state") == "confirmed":
            cid = entry.get("confirmed_component_id")
            if isinstance(cid, str) and cid:
                out[str(entry.get("part"))] = cid
    return out


def apply_confirmation(
    mapping: dict[str, Any],
    assignments: dict[str, str | None],
    *,
    accept_proposals: bool,
    components: dict[str, str],
    actor: str,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """The mapping with a person's answers applied, and ``(part, outcome)`` per change.

    ``assignments`` maps a part to a component id, or to ``None`` for "not a
    component". With ``accept_proposals`` every confident proposal the person
    did not answer is confirmed as proposed. A question is only ever settled
    by an explicit answer.
    """
    parts = {str(e.get("part")): e for e in mapping.get("parts") or [] if isinstance(e, dict)}
    unknown = sorted(set(assignments) - set(parts))
    if unknown:
        raise ConceptPartsError(f"no such part: {', '.join(unknown)}", 400)
    for part, cid in assignments.items():
        if cid is not None and cid not in components:
            raise ConceptPartsError(f"{cid} is not a component of the concept assembly", 400)
    now = datetime.now(UTC).isoformat()
    outcomes: list[tuple[str, str]] = []
    new_parts = []
    for name, entry in parts.items():
        entry = dict(entry)
        if name in assignments:
            cid = assignments[name]
            if cid is None:
                entry.update(state="not_a_component", confirmed_component_id=None)
                outcomes.append((name, "not_a_component"))
            else:
                proposed = entry.get("proposed_component_id")
                entry.update(
                    state="confirmed", confirmed_component_id=cid, confirmed_name=components[cid]
                )
                outcomes.append((name, "confirmed" if proposed == cid else "corrected"))
            entry.update(confirmed_by=actor, confirmed_at=now)
        elif (
            accept_proposals
            and entry.get("state") == "proposed"
            and entry.get("proposed_component_id") in components
        ):
            cid = str(entry["proposed_component_id"])
            entry.update(
                state="confirmed",
                confirmed_component_id=cid,
                confirmed_name=components[cid],
                confirmed_by=actor,
                confirmed_at=now,
            )
            outcomes.append((name, "confirmed"))
        new_parts.append(entry)
    out = dict(mapping)
    out["parts"] = new_parts
    decided = {"confirmed", "not_a_component"}
    out["questions"] = [
        q
        for q in mapping.get("questions") or []
        if isinstance(q, dict)
        and next((p for p in new_parts if p.get("part") == q.get("part")), {}).get("state")
        not in decided
    ]
    pending = [p for p in new_parts if p.get("state") not in decided]
    out["status"] = "confirmed" if not pending else "partially_confirmed"
    if not any(p.get("state") in decided for p in new_parts):
        out["status"] = mapping.get("status") or "proposed"
    return out, outcomes


def extras_for_mapping(mapping: dict[str, Any], reading: PartsReading) -> dict[int, dict[str, Any]]:
    """The node ``extras`` a mapping writes: ids on confirmed parts only."""
    by_name = {p.name: p for p in reading.parts}
    updates: dict[int, dict[str, Any]] = {}
    for entry in mapping.get("parts") or []:
        part = by_name.get(str(entry.get("part")))
        if part is None:
            continue
        if entry.get("state") == "confirmed" and entry.get("confirmed_component_id"):
            updates[part.node_index] = {
                "component_id": entry["confirmed_component_id"],
                "part_name": entry.get("confirmed_name") or entry["confirmed_component_id"],
                "part_mapping": "confirmed",
                "segment_part": part.name,
            }
        else:
            updates[part.node_index] = {
                "component_id": None,
                "part_name": None,
                "part_mapping": None,
                "segment_part": part.name,
            }
    return updates


def _parts_in_metres(reading: PartsReading) -> list[dict[str, Any]]:
    return [p.as_dict() for p in reading.parts]


def part_extents_mm(wp: Any) -> dict[str, dict[str, Any]]:
    """Per confirmed component, its part's box in layout axes and mm, marked estimated.

    glTF ``(x, y up, z)`` in metres becomes ``(x, -z, y)`` in mm, so a
    reconstruction reads it like a layout block (x width, y depth, z up).
    Never ``confirmed``: the box is an AI model's guess.
    """
    meta = _meta(wp)
    confirmed = confirmed_parts(meta.get(MAPPING_KEY))
    parts = {str(p.get("name")): p for p in meta.get(PARTS_KEY) or [] if isinstance(p, dict)}
    out: dict[str, dict[str, Any]] = {}
    for part_name, cid in confirmed.items():
        part = parts.get(part_name)
        if not part:
            continue
        try:
            lo = [float(v) for v in part["bbox_min"]]
            hi = [float(v) for v in part["bbox_max"]]
        except (KeyError, TypeError, ValueError):
            continue
        lmin = (lo[0] * 1000, -hi[2] * 1000, lo[1] * 1000)
        lmax = (hi[0] * 1000, -lo[2] * 1000, hi[1] * 1000)
        size = [round(lmax[i] - lmin[i], 3) for i in range(3)]
        if min(size) <= 0:
            continue
        if cid in out:
            continue  # a component drawn by two parts: the first one stands for it
        out[cid] = {
            "position_mm": [round(v, 3) for v in lmin],
            "size_mm": size,
            "shape": "box",
            "provenance": "open",
            "confidence": "estimated",
            "source": f"segmented AI concept part {part_name} (L0), estimated",
            "node_id": str(getattr(wp, "id", "")),
        }
    return out


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class ConceptPartsService:
    """Segment, map, regenerate and complete concept parts; accept or reject revisions.

    ``generator`` is the mesh provider (defaults to the configured one per
    call); it must offer ``segment(...)``, ``complete(...)`` and be callable as
    a :class:`~api_gateway.twin.mesh_generation.MeshGenerator`. Blob reader and
    writer are injectable for tests.
    """

    def __init__(
        self,
        twin: Any,
        project_backend: Any = None,
        *,
        generator: Any = None,
        blob_reader: Callable[[Any], tuple[bytes, str]] | None = None,
        blob_writer: Callable[..., str] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.twin = twin
        self.project_backend = project_backend
        self._generator = generator
        self._read = blob_reader or self._default_reader
        self._write = blob_writer or self._default_writer
        self._timeout_s = timeout_s

    # -- plumbing ------------------------------------------------------------

    @staticmethod
    def _default_reader(wp: Any) -> tuple[bytes, str]:
        from fastapi import HTTPException

        from api_gateway.twin.blob_store import resolve_work_product_blob

        try:
            return resolve_work_product_blob(wp)
        except HTTPException as exc:
            raise ConceptPartsError(f"the stored model could not be read: {exc.detail}") from exc

    @staticmethod
    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    def _provider(self) -> Any:
        return self._generator if self._generator is not None else resolve_mesh_generator()

    def _limit(self) -> float:
        return self._timeout_s if self._timeout_s is not None else timeout_from_env()

    async def _get(self, node_id: Any) -> Any:
        wp = await self.twin.get_work_product(_node_uuid(node_id))
        if wp is None:
            raise ConceptPartsError(f"no such node: {node_id}", 404)
        return wp

    async def _blob(self, wp: Any, key: str | None = None) -> bytes:
        if key:
            proxy = type("_Blob", (), {})()
            proxy.metadata = {**_meta(wp), "minio_object_key": key}  # type: ignore[attr-defined]
            proxy.name = getattr(wp, "name", "")  # type: ignore[attr-defined]
            proxy.format = "glb"  # type: ignore[attr-defined]
            proxy.file_path = ""  # type: ignore[attr-defined]
            proxy.id = getattr(wp, "id", None)  # type: ignore[attr-defined]
            wp = proxy
        content, _name = await asyncio.to_thread(self._read, wp)
        return content

    async def _family(self, project_id: Any, root: str) -> list[Any]:
        from twin_core.models.enums import WorkProductType

        pid = _pid(project_id)
        renders = await self.twin.list_work_products(
            work_product_type=WorkProductType.RENDER, project_id=pid
        )
        return [r for r in renders if is_concept_revision(r) and root_of(r) == root]

    async def _call(self, coro: Any, label: str) -> GeneratedMesh:
        limit = self._limit()
        try:
            result: GeneratedMesh = await asyncio.wait_for(coro, timeout=limit + _GRACE_S)
        except TimeoutError as exc:
            raise MeshGenerationTimeoutError(
                f"the mesh provider did not finish the {label} in {limit:g}s"
            ) from exc
        if not result.glb_bytes:
            raise MeshGenerationError(f"the mesh provider returned an empty model for the {label}")
        return result

    async def _components(self, project_id: Any) -> dict[str, str]:
        from twin_core.component_identity import project_identity

        ident = await project_identity(self.twin, _pid(project_id))
        return {
            cid: str(info.get("name") or cid)
            for cid, info in (ident.get("components") or {}).items()
            if info.get("in_current", True)
        }

    async def _layout_blocks(self, project_id: Any) -> dict[str, dict[str, Any]]:
        """``{component_id: block}`` from the newest concept block layout."""
        from twin_core.component_identity import issue_ids, project_identity
        from twin_core.models.enums import WorkProductType

        pid = _pid(project_id)
        if pid is None:
            return {}
        try:
            sketches = await self.twin.list_work_products(
                work_product_type=WorkProductType.DESIGN_SKETCH, project_id=pid
            )
        except Exception:  # noqa: BLE001 - no layout, no geometry evidence
            return {}
        with_layout = sorted(
            (s for s in sketches if isinstance(_meta(s).get("concept_layout"), dict)),
            key=_created,
            reverse=True,
        )
        if not with_layout:
            return {}
        layout = _meta(with_layout[0])["concept_layout"]
        ident = await project_identity(self.twin, pid)
        blocks = [p for p in layout.get("parts") or [] if isinstance(p, dict) and p.get("name")]
        ids = issue_ids([str(p["name"]) for p in blocks], ident.get("name_to_id") or {})
        return {ids[str(p["name"])]: p for p in blocks}

    async def _store_revision(
        self,
        *,
        previous: Any,
        glb: bytes,
        tool: str,
        operation: str,
        label: str,
        extra: dict[str, Any],
        name: str,
        request_context: dict[str, Any] | None,
        mapping: dict[str, Any] | None,
        parts: list[dict[str, Any]],
    ) -> Any:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        prev_meta = _meta(previous)
        root = root_of(previous)
        family = await self._family(previous.project_id, root) if previous.project_id else []
        revision = 1 + max([revision_of(w) for w in family] + [revision_of(previous)])
        wp_id = uuid4()
        filename = f"concept-r{revision}.glb"
        key = await asyncio.to_thread(self._write, str(wp_id), filename, glb, content_type=GLB_MIME)
        confirmed = confirmed_parts(mapping)
        components = {
            str(e.get("confirmed_name") or cid): cid
            for e in (mapping or {}).get("parts") or []
            if isinstance(e, dict)
            for cid in [e.get("confirmed_component_id")]
            if e.get("state") == "confirmed" and isinstance(cid, str)
        }
        content_hash = hashlib.sha256(glb).hexdigest()
        faces: dict[str, list[dict[str, Any]]] = {}
        try:
            reading = read_parts(glb)
            if reading.status == "segmented":
                for part in reading.parts:
                    samples = node_face_samples(glb, part.node_index)
                    for sample in samples:
                        sample["face_id"] = f"{part.name}:{sample['face_id']}"
                    faces[part.name] = samples
        except GlbPartsError as exc:
            logger.debug("concept_parts_faces_unread", error=str(exc))
        metadata: dict[str, Any] = {
            PART_FACES_KEY: faces,
            "original_filename": filename,
            "minio_object_key": key,
            "content_sha256": content_hash,
            "size_bytes": len(glb),
            "generated_by": tool,
            # Never evidence, never CAD: still an AI model's guess.
            "authority": "illustrative",
            "illustrative_label": label,
            "dimensions": "estimated",
            "concept_root_node_id": root,
            "concept_revision": revision,
            "previous_revision_node_id": str(previous.id),
            "source_work_product_id": str(previous.id),
            STATUS_KEY: "proposed",
            "concept_operation": operation,
            LINEAGE_KEY: {
                "operation": operation,
                "previous_node_id": str(previous.id),
                "previous_revision": revision_of(previous),
                "root_node_id": root,
            },
            PARTS_KEY: parts,
            PART_COMPONENT_IDS_KEY: components,
            REPRESENTATION_KEY: descriptor(
                RepresentationLevel.L0,
                RepresentationAuthority.ILLUSTRATIVE,
                source_revision=f"node:{previous.id}",
                component_ids=sorted(set(confirmed.values())),
                tool=tool,
                dimensions="estimated",
            ),
            **extra,
        }
        if mapping is not None:
            metadata[MAPPING_KEY] = mapping
            metadata["part_mapping_status"] = mapping.get("status")
        for keep in (
            "concept_name",
            "envelope_mm",
            "source_item_ref",
            "manufacturing_context_ref",
            "manufacturing_note",
            "dfm_status",
            "dfm_status_label",
            "speculative",
        ):
            if keep in prev_meta and keep not in metadata:
                metadata[keep] = prev_meta[keep]
        if SEGMENTATION_KEY in prev_meta and SEGMENTATION_KEY not in metadata:
            metadata[SEGMENTATION_KEY] = prev_meta[SEGMENTATION_KEY]
            metadata["segmentation_status"] = prev_meta.get("segmentation_status")
        if request_context:
            metadata["request"] = dict(request_context)
        now = datetime.now(UTC)
        created = await self.twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.RENDER,
                domain=str(getattr(previous, "domain", "") or "mechanical"),
                file_path="",
                content_hash=content_hash,
                format="glb",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=tool,
                project_id=previous.project_id,
            )
        )
        await self.twin.add_edge(created.id, previous.id, EdgeType.GENERATED_FROM)
        if previous.project_id and self.project_backend is not None:
            try:
                await self.project_backend.link_work_product(
                    str(previous.project_id), str(created.id), name, "render"
                )
            except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                logger.warning("concept_parts_project_link_failed", error=str(exc))
        return created

    def summary(self, wp: Any) -> dict[str, Any]:
        """The JSON shape of one concept revision."""
        meta = _meta(wp)
        seg = meta.get(SEGMENTATION_KEY)
        return {
            "node_id": str(wp.id),
            "name": wp.name,
            "created_at": _created(wp),
            "generated_by": meta.get("generated_by"),
            "concept_revision": revision_of(wp),
            "concept_status": status_of(wp),
            "operation": meta.get("concept_operation") or "generated",
            "lineage": meta.get(LINEAGE_KEY),
            "segmentation": seg if isinstance(seg, dict) else None,
            "mapping": meta.get(MAPPING_KEY),
            "parts": meta.get(PARTS_KEY) or [],
            "regenerated_parts": meta.get("regenerated_parts") or [],
            "completed_parts": meta.get("completed_parts") or [],
            "authority": meta.get("authority") or "illustrative",
            "label": meta.get("illustrative_label"),
        }

    # -- segmentation ----------------------------------------------------------

    @staticmethod
    def segment_options(
        model: Any, granularity: Any, split_by_connectivity: Any
    ) -> tuple[str, str | None, bool | None]:
        tool = SEGMENT_TOOL
        model = SEGMENT_MODEL_GEOMETRY if model in (None, "") else model
        if model not in SEGMENT_MODELS:
            raise ConceptPartsError(f"{tool}: 'model' must be one of {', '.join(SEGMENT_MODELS)}")
        if granularity is not None and granularity not in SEGMENT_GRANULARITIES:
            raise ConceptPartsError(
                f"{tool}: 'granularity' must be one of {', '.join(SEGMENT_GRANULARITIES)}"
            )
        if split_by_connectivity is not None and not isinstance(split_by_connectivity, bool):
            raise ConceptPartsError(f"{tool}: 'split_by_connectivity' must be true or false")
        if model != SEGMENT_MODEL_SEMANTIC and (
            granularity is not None or split_by_connectivity is not None
        ):
            raise ConceptPartsError(
                f"{tool}: 'granularity' and 'split_by_connectivity' need the semantic "
                f"model {SEGMENT_MODEL_SEMANTIC}"
            )
        return str(model), granularity, split_by_connectivity

    async def segment(
        self,
        *,
        node_id: str,
        model: str | None = None,
        granularity: str | None = None,
        split_by_connectivity: bool | None = None,
        source_url: str | None = None,
        validate_only: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Split an accepted concept mesh into parts; a new revision with a proposed mapping."""
        model_id, gran, split = self.segment_options(model, granularity, split_by_connectivity)
        source = await self._get(node_id)
        if not is_concept_revision(source):
            raise ConceptPartsError(f"{SEGMENT_TOOL}: {node_id} is not a 3D concept (L0)")
        if status_of(source) != "accepted":
            raise ConceptPartsError(
                f"{SEGMENT_TOOL}: {node_id} is {status_of(source)}; only an accepted 3D "
                "concept is split into parts",
                409,
            )
        if source_url is not None and not str(source_url).startswith("https://"):
            raise ConceptPartsError(f"{SEGMENT_TOOL}: 'source_url' must be an https URL")
        meta = _meta(source)
        task_input = None
        if (
            meta.get("generated_by") == CONCEPT_MESH_TOOL
            and meta.get("provider") == "tripo"
            and meta.get("task_id")
            and meta.get("mode") in ("text_to_model", "image_to_model", "multiview_to_model")
        ):
            task_input = str(meta["task_id"])
        if validate_only:
            if self._generator is None:
                resolve_mesh_generator()
            return {"validated": True, "input_kind": "task_id" if task_input else "upload"}

        with tracer.start_as_current_span(SEGMENT_TOOL) as span:
            span.set_attribute("concept_parts.source_node_id", str(source.id))
            span.set_attribute("concept_parts.model", model_id)
            try:
                gen = self._provider()
                upload: bytes | None = None
                kind = "task_id"
                if task_input is None and source_url:
                    kind = "url"
                elif task_input is None:
                    kind = "file_token"
                    upload = await self._blob(source, meta.get("provider_glb_object_key"))
                result = await self._call(
                    gen.segment(
                        task_id=task_input,
                        glb=upload,
                        url=source_url if kind == "url" else None,
                        model=model_id,
                        granularity=gran,
                        split_by_connectivity=split,
                    ),
                    "segmentation",
                )
                span.set_attribute("concept_parts.task_id", result.task_id)
                try:
                    reading = read_parts(result.glb_bytes)
                except GlbPartsError as exc:
                    raise MeshGenerationError(
                        f"the segmented model (task {result.task_id}) could not be read: {exc}"
                    ) from exc
                stored, size_record = self._fit_like(source, result.glb_bytes)
                reading = read_parts(stored)
                span.set_attribute("concept_parts.segmentation_status", reading.status)
                span.set_attribute("concept_parts.parts", len(reading.parts))
                parts = _parts_in_metres(reading)
                if reading.status == "segmented":
                    mapping = propose_mapping(
                        parts,
                        [
                            {"component_id": c, "name": n}
                            for c, n in (await self._components(source.project_id)).items()
                        ]
                        if source.project_id
                        else [],
                        await self._layout_blocks(source.project_id),
                    )
                else:
                    mapping = {
                        "status": "not_segmented",
                        "parts": [],
                        "questions": [],
                        "unmatched_components": [],
                        "method": "no parts to map",
                    }
                segmentation = {
                    "provider": result.provider,
                    "model_version": result.model_version,
                    "granularity": gran,
                    "split_by_connectivity": split,
                    "task_id": result.task_id,
                    "input_kind": kind,
                    "input_task_id": task_input,
                    "source_node_id": str(source.id),
                    "credits": result.credits,
                    "rendered_image_url": result.output.get("rendered_image_url"),
                    "status": reading.status,
                    "note": reading.note,
                    "labelled": reading.labelled,
                    "part_count": len(reading.parts) if reading.status == "segmented" else 0,
                    "segmented_at": datetime.now(UTC).isoformat(),
                }
                concept = str(meta.get("concept_name") or "concept")
                created = await self._store_revision(
                    previous=source,
                    glb=stored,
                    tool=SEGMENT_TOOL,
                    operation="segmented",
                    label=SEGMENTED_LABEL,
                    extra={
                        SEGMENTATION_KEY: segmentation,
                        "segmentation_status": reading.status,
                        "provider": result.provider,
                        "model_version": result.model_version,
                        "task_id": result.task_id,
                        "mode": result.mode,
                        **size_record,
                    },
                    name=f"3D concept in parts: {concept}",
                    request_context=request_context,
                    mapping=mapping,
                    parts=parts,
                )
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _metric("record_concept_segmentation", "unavailable", model_id)
                raise
            except MeshGenerationTimeoutError as exc:
                span.record_exception(exc)
                _metric("record_concept_segmentation", "timeout", model_id)
                raise
            except (MeshGenerationError, ConceptPartsError) as exc:
                span.record_exception(exc)
                _metric("record_concept_segmentation", "failed", model_id)
                logger.warning("concept_segmentation_failed", node_id=node_id, error=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _metric("record_concept_segmentation", "failed", model_id)
                raise MeshGenerationError(f"the segmentation failed: {exc}") from exc
        _metric("record_concept_segmentation", reading.status, model_id)
        logger.info(
            "concept_segmented",
            node_id=str(created.id),
            source_node_id=str(source.id),
            status=reading.status,
            parts=len(reading.parts),
            task_id=result.task_id,
            model=model_id,
            credits=result.credits,
            questions=len(mapping.get("questions") or []),
        )
        out = self.summary(created)
        out.update(
            authority="illustrative",
            label=SEGMENTED_LABEL,
            segmentation_status=reading.status,
            task_id=result.task_id,
        )
        return out

    @staticmethod
    def _fit_like(source: Any, glb: bytes) -> tuple[bytes, dict[str, Any]]:
        """The segmented model sized as its source was: to the same envelope."""
        from api_gateway.twin.concept_mesh import _size_and_fit

        meta = _meta(source)
        envelope = meta.get("envelope_mm")
        if not (isinstance(envelope, list) and len(envelope) == 3):
            return glb, {}
        try:
            env = [float(v) for v in envelope]
        except (TypeError, ValueError):
            return glb, {}
        fit = bool(meta.get("fit_to_envelope"))
        record, stored = _size_and_fit(glb, env, metres=False, fit=fit)
        return stored, record

    # -- mapping ---------------------------------------------------------------

    async def mapping(self, node_id: str) -> dict[str, Any]:
        wp = await self._get(node_id)
        if not is_concept_revision(wp):
            raise ConceptPartsError(f"{node_id} is not a 3D concept (L0)", 404)
        return self.summary(wp)

    async def confirm_mapping(
        self,
        *,
        node_id: str,
        assignments: dict[str, str | None],
        accept_proposals: bool = False,
        actor: str,
    ) -> dict[str, Any]:
        """A person's confirmation or correction; ids go into the GLB for confirmed parts only."""
        from twin_core.geometry_state import set_node_metadata

        with tracer.start_as_current_span("concept_parts.confirm_mapping") as span:
            wp = await self._get(node_id)
            meta = _meta(wp)
            mapping = meta.get(MAPPING_KEY)
            if not is_concept_revision(wp) or not isinstance(mapping, dict):
                raise ConceptPartsError(f"{node_id} has no part mapping to confirm", 404)
            if mapping.get("status") == "not_segmented":
                raise ConceptPartsError("this concept came back whole: there are no parts to map")
            if status_of(wp) in ("rejected", "superseded"):
                raise ConceptPartsError(f"{node_id} is {status_of(wp)}; map the current one", 409)
            components = await self._components(wp.project_id)
            updated, outcomes = apply_confirmation(
                mapping,
                assignments,
                accept_proposals=accept_proposals,
                components=components,
                actor=actor,
            )
            glb = await self._blob(wp)
            reading = read_parts(glb)
            new_glb = set_node_extras(glb, extras_for_mapping(updated, reading))
            key = await asyncio.to_thread(
                self._write,
                str(wp.id),
                f"concept-r{revision_of(wp)}-mapped.glb",
                new_glb,
                content_type=GLB_MIME,
            )
            confirmed = confirmed_parts(updated)
            names = {
                str(e.get("confirmed_name") or e.get("confirmed_component_id")): str(
                    e["confirmed_component_id"]
                )
                for e in updated["parts"]
                if e.get("state") == "confirmed"
            }
            representation = dict(meta.get(REPRESENTATION_KEY) or {})
            representation["component_ids"] = sorted(set(confirmed.values()))
            content_hash = hashlib.sha256(new_glb).hexdigest()
            await set_node_metadata(
                self.twin,
                wp,
                **{
                    MAPPING_KEY: updated,
                    "part_mapping_status": updated["status"],
                    PART_COMPONENT_IDS_KEY: names,
                    REPRESENTATION_KEY: representation,
                    "minio_object_key": key,
                    "content_sha256": content_hash,
                    "size_bytes": len(new_glb),
                    "unmapped_glb_object_key": meta.get("unmapped_glb_object_key")
                    or meta.get("minio_object_key"),
                },
            )
            try:
                await self.twin.graph.update_node(wp.id, {"content_hash": content_hash})
            except Exception as exc:  # noqa: BLE001 - the metadata names the hash too
                logger.debug("concept_parts_hash_update_failed", error=str(exc))
            span.set_attribute("concept_parts.confirmed", len(confirmed))
            for _part, outcome in outcomes:
                _metric("record_concept_part_mapping", outcome)
            logger.info(
                "concept_part_mapping_confirmed",
                node_id=str(wp.id),
                confirmed=len(confirmed),
                outcomes=dict(outcomes),
                status=updated["status"],
                actor=actor,
            )
            return self.summary(await self._get(node_id))

    # -- one part --------------------------------------------------------------

    async def _part_of(self, wp: Any, component_id: str, tool: str) -> tuple[bytes, GlbPart]:
        if not is_concept_revision(wp) or not isinstance(_meta(wp).get(MAPPING_KEY), dict):
            raise ConceptPartsError(f"{tool}: {wp.id} is not a 3D concept in parts")
        if status_of(wp) in ("rejected", "superseded"):
            raise ConceptPartsError(f"{tool}: {wp.id} is {status_of(wp)}; use the current one", 409)
        confirmed = confirmed_parts(_meta(wp)[MAPPING_KEY])
        names = [p for p, c in confirmed.items() if c == component_id]
        if not names:
            raise ConceptPartsError(
                f"{tool}: no part is confirmed as {component_id}; confirm the part mapping first",
                409,
            )
        if len(names) > 1:
            raise ConceptPartsError(
                f"{tool}: {component_id} is drawn by {len(names)} parts ({', '.join(names)}); "
                "one part at a time can be redone",
                409,
            )
        glb = await self._blob(wp)
        part = next((p for p in read_parts(glb).parts if p.name == names[0]), None)
        if part is None:
            raise ConceptPartsError(f"{tool}: part {names[0]} is not in the stored model", 409)
        return glb, part

    @staticmethod
    def _swap_checked(glb: bytes, part: GlbPart, new_part: bytes) -> tuple[bytes, dict[str, Any]]:
        """Swap one node, and prove every other node's bytes did not move."""
        doc, _b, _v = split_glb(glb)
        others = [
            i
            for i, n in enumerate(doc.get("nodes") or [])
            if i != part.node_index and isinstance(n, dict) and isinstance(n.get("mesh"), int)
        ]
        before = {i: node_mesh_bytes(glb, i) for i in others}
        try:
            swapped, record = swap_part(glb, part.node_index, new_part)
        except GlbPartsError as exc:
            raise ConceptPartsError(f"the new part could not be put in place: {exc}") from exc
        moved = [i for i in others if node_mesh_bytes(swapped, i) != before[i]]
        if moved:  # never expected: the swap only appends
            raise ConceptPartsError(f"the swap changed other parts ({moved}); nothing was stored")
        record["other_nodes_unchanged"] = len(others)
        return swapped, record

    async def _part_image(
        self, project_id: Any, image_node_id: str | None, image_b64: str | None, mime: str | None
    ) -> tuple[bytes, str, str | None]:
        """The image a person gave for a part: a stored image node, or uploaded bytes."""
        from api_gateway.twin.image_critique import stored_image_bytes

        if image_node_id:
            wp = await self._get(image_node_id)
            if project_id and str(getattr(wp, "project_id", "") or "") != str(project_id):
                raise ConceptPartsError("the image is not in this project", 404)
            content, _name = await asyncio.to_thread(self._read, wp)
            image, got = stored_image_bytes(content, str(getattr(wp, "format", "") or ""))
            if got not in ("image/png", "image/jpeg", "image/webp"):
                raise ConceptPartsError(f"the image is {got}; PNG, JPEG or WebP is needed")
            return image, got, str(wp.id)
        if image_b64:
            try:
                raw = base64.b64decode(image_b64, validate=True)
            except ValueError as exc:
                raise ConceptPartsError("the image is not valid base64", 400) from exc
            if len(raw) > MAX_PART_IMAGE_BYTES:
                raise ConceptPartsError("the image is larger than 10 MB", 413)
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise ConceptPartsError("the image must be PNG, JPEG or WebP")
            return raw, str(mime), None
        raise ConceptPartsError("an image source needs an image (image_node_id or image data)")

    async def regenerate_part(
        self,
        *,
        node_id: str,
        component_id: str,
        source: str,
        prompt: str | None = None,
        image_node_id: str | None = None,
        image_base64: str | None = None,
        image_mime: str | None = None,
        validate_only: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate one part again and swap only that node; a new proposed revision."""
        from api_gateway.twin.concept_mesh import generation_options, mesh_prompt

        tool = REGENERATE_TOOL
        if source not in ("text", "image"):
            raise ConceptPartsError(f"{tool}: 'source' must be text or image")
        text = (prompt or "").strip() or None
        if source == "text" and not text:
            raise ConceptPartsError(f"{tool}: a text source needs a prompt")
        if text and len(text) > MAX_PROMPT_CHARS:
            raise ConceptPartsError(f"{tool}: 'prompt' is longer than {MAX_PROMPT_CHARS} characters")
        if source == "image" and not (image_node_id or image_base64):
            raise ConceptPartsError(f"{tool}: an image source needs an image")
        wp = await self._get(node_id)
        glb, part = await self._part_of(wp, component_id, tool)
        if validate_only:
            if self._generator is None:
                resolve_mesh_generator()
            return {"validated": True, "part": part.name}

        with tracer.start_as_current_span(tool) as span:
            span.set_attribute("concept_parts.component_id", component_id)
            span.set_attribute("concept_parts.source", source)
            try:
                image: bytes | None = None
                mime = ""
                image_ref: str | None = None
                if source == "image":
                    image, mime, image_ref = await self._part_image(
                        wp.project_id, image_node_id, image_base64, image_mime
                    )
                components = await self._components(wp.project_id) if wp.project_id else {}
                comp_name = components.get(component_id, component_id)
                sent_text = None
                manufacturing: Any = None
                if source == "text":
                    from api_gateway.twin.manufacturing_context import (
                        manufacturing_context_block,
                    )

                    manufacturing = await manufacturing_context_block(
                        str(wp.project_id) if wp.project_id else None,
                        component_id,
                        twin=self.twin,
                        generator="concept_mesh",
                        hint=str(text),
                    )
                    sent_text = mesh_prompt(f"{text} (one part only: {comp_name})", manufacturing.text)
                options, _note = generation_options(
                    auto_size=False, face_limit=PART_FACE_LIMIT, texture=True
                )
                gen = self._provider()
                result = await self._call(
                    gen(image, mime, sent_text, options=options), "part regeneration"
                )
                span.set_attribute("concept_parts.task_id", result.task_id)
                stored, fit = self._swap_checked(glb, part, result.glb_bytes)
                entry = {
                    "component_id": component_id,
                    "component_name": comp_name,
                    "part": part.name,
                    "source": source,
                    "prompt": text,
                    "prompt_sent": sent_text,
                    "image_node_id": image_ref,
                    "provider": result.provider,
                    "model_version": result.model_version,
                    "task_id": result.task_id,
                    "mode": result.mode,
                    "credits": result.credits,
                    "fit": fit,
                    "regenerated_at": datetime.now(UTC).isoformat(),
                }
                meta = _meta(wp)
                extra: dict[str, Any] = {
                    "regenerated_parts": [*list(meta.get("regenerated_parts") or []), entry],
                    "completed_parts": list(meta.get("completed_parts") or []),
                    "provider": result.provider,
                    "model_version": result.model_version,
                    "task_id": result.task_id,
                    "mode": result.mode,
                }
                if manufacturing is not None:
                    extra.update(manufacturing.provenance())
                created = await self._store_revision(
                    previous=wp,
                    glb=stored,
                    tool=tool,
                    operation="regenerated_part",
                    label=REGENERATED_LABEL,
                    extra={
                        **extra,
                        LINEAGE_KEY: {
                            "operation": "regenerated_part",
                            "previous_node_id": str(wp.id),
                            "previous_revision": revision_of(wp),
                            "root_node_id": root_of(wp),
                            "component_id": component_id,
                            "part": part.name,
                        },
                    },
                    name=f"3D concept, {comp_name} redone",
                    request_context=request_context,
                    mapping=meta.get(MAPPING_KEY),
                    parts=_parts_in_metres(read_parts(stored)),
                )
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _metric("record_concept_part_regeneration", "unavailable", source)
                raise
            except MeshGenerationTimeoutError as exc:
                span.record_exception(exc)
                _metric("record_concept_part_regeneration", "timeout", source)
                raise
            except (MeshGenerationError, ConceptPartsError) as exc:
                span.record_exception(exc)
                _metric("record_concept_part_regeneration", "failed", source)
                logger.warning("concept_part_regeneration_failed", error=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _metric("record_concept_part_regeneration", "failed", source)
                raise MeshGenerationError(f"the part regeneration failed: {exc}") from exc
        _metric("record_concept_part_regeneration", "regenerated", source)
        logger.info(
            "concept_part_regenerated",
            node_id=str(created.id),
            previous=str(wp.id),
            component_id=component_id,
            part=part.name,
            source=source,
            task_id=result.task_id,
            scale_factor=fit.get("scale_factor"),
        )
        out = self.summary(created)
        out.update(authority="illustrative", label=REGENERATED_LABEL, fit=fit)
        return out

    async def complete_part(
        self,
        *,
        node_id: str,
        component_id: str,
        completion_mode: str = "quick_cap",
        validate_only: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Seal or complete one part with Tripo ``mesh/complete``; a new proposed revision."""
        tool = COMPLETE_TOOL
        if completion_mode not in COMPLETION_MODES:
            raise ConceptPartsError(
                f"{tool}: 'completion_mode' must be one of {', '.join(COMPLETION_MODES)}"
            )
        wp = await self._get(node_id)
        glb, part = await self._part_of(wp, component_id, tool)
        if part.extras.get(REGENERATED_KEY):
            raise ConceptPartsError(
                f"{tool}: {part.name} was redone after the split, so the provider's "
                "segmentation no longer has it; redo it again instead",
                409,
            )
        seg = _meta(wp).get(SEGMENTATION_KEY)
        seg_task = seg.get("task_id") if isinstance(seg, dict) else None
        if not seg_task:
            raise ConceptPartsError(f"{tool}: the segmentation task of this concept is unknown")
        if validate_only:
            if self._generator is None:
                resolve_mesh_generator()
            return {"validated": True, "part": part.name}
        with tracer.start_as_current_span(tool) as span:
            span.set_attribute("concept_parts.component_id", component_id)
            span.set_attribute("concept_parts.completion_mode", completion_mode)
            try:
                gen = self._provider()
                result = await self._call(
                    gen.complete(
                        segment_task_id=str(seg_task),
                        part_names=[part.name],
                        completion_mode=completion_mode,
                    ),
                    "part completion",
                )
                span.set_attribute("concept_parts.task_id", result.task_id)
                new_part = self._completed_part(result.glb_bytes, part.name)
                stored, fit = self._swap_checked(glb, part, new_part)
                entry = {
                    "component_id": component_id,
                    "part": part.name,
                    "completion_mode": completion_mode,
                    "provider": result.provider,
                    "task_id": result.task_id,
                    "segment_task_id": seg_task,
                    "credits": result.credits,
                    "fit": fit,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                meta = _meta(wp)
                created = await self._store_revision(
                    previous=wp,
                    glb=stored,
                    tool=tool,
                    operation="completed_part",
                    label=COMPLETED_LABEL,
                    extra={
                        "completed_parts": [*list(meta.get("completed_parts") or []), entry],
                        "regenerated_parts": list(meta.get("regenerated_parts") or []),
                        "provider": result.provider,
                        "model_version": result.model_version,
                        "task_id": result.task_id,
                        "mode": result.mode,
                        LINEAGE_KEY: {
                            "operation": "completed_part",
                            "previous_node_id": str(wp.id),
                            "previous_revision": revision_of(wp),
                            "root_node_id": root_of(wp),
                            "component_id": component_id,
                            "part": part.name,
                            "completion_mode": completion_mode,
                        },
                    },
                    name=f"3D concept, {part.name} sealed",
                    request_context=request_context,
                    mapping=meta.get(MAPPING_KEY),
                    parts=_parts_in_metres(read_parts(stored)),
                )
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _metric("record_concept_part_completion", "unavailable", completion_mode)
                raise
            except MeshGenerationTimeoutError as exc:
                span.record_exception(exc)
                _metric("record_concept_part_completion", "timeout", completion_mode)
                raise
            except (MeshGenerationError, ConceptPartsError) as exc:
                span.record_exception(exc)
                _metric("record_concept_part_completion", "failed", completion_mode)
                logger.warning("concept_part_completion_failed", error=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _metric("record_concept_part_completion", "failed", completion_mode)
                raise MeshGenerationError(f"the part completion failed: {exc}") from exc
        _metric("record_concept_part_completion", "completed", completion_mode)
        logger.info(
            "concept_part_completed",
            node_id=str(created.id),
            previous=str(wp.id),
            component_id=component_id,
            part=part.name,
            mode=completion_mode,
            task_id=result.task_id,
        )
        out = self.summary(created)
        out.update(authority="illustrative", label=COMPLETED_LABEL, fit=fit)
        return out

    @staticmethod
    def _completed_part(glb: bytes, part_name: str) -> bytes:
        """The completed part out of a completion result: its own node, else the whole model."""
        try:
            reading = read_parts(glb)
        except GlbPartsError as exc:
            raise MeshGenerationError(f"the completed model could not be read: {exc}") from exc
        named = [p for p in reading.parts if p.name == part_name]
        if named:
            return subset_glb(glb, named[0].node_index)
        if len(reading.parts) == 1:
            return glb
        raise MeshGenerationError(
            f"the completed model has {len(reading.parts)} parts and none is named {part_name}; "
            "nothing was changed"
        )

    # -- accepting a revision --------------------------------------------------

    async def decide(self, *, node_id: str, decision: str, actor: str) -> dict[str, Any]:
        """Accept a proposed revision (the previous accepted one is superseded) or reject it."""
        from twin_core.geometry_state import set_node_metadata
        from twin_core.models.enums import EdgeType

        if decision not in ("accept", "reject"):
            raise ConceptPartsError("'decision' must be accept or reject", 400)
        wp = await self._get(node_id)
        if not is_concept_revision(wp):
            raise ConceptPartsError(f"{node_id} is not a 3D concept (L0)", 404)
        if status_of(wp) != "proposed":
            raise ConceptPartsError(f"{node_id} is {status_of(wp)}, not a proposal", 409)
        now = datetime.now(UTC).isoformat()
        superseded: list[str] = []
        with tracer.start_as_current_span("concept_parts.decide") as span:
            span.set_attribute("concept_parts.decision", decision)
            if decision == "accept":
                family = await self._family(wp.project_id, root_of(wp))
                for other in family:
                    if other.id != wp.id and status_of(other) == "accepted":
                        await set_node_metadata(
                            self.twin,
                            other,
                            **{STATUS_KEY: "superseded", "superseded_by": str(wp.id)},
                        )
                        await self.twin.add_edge(wp.id, other.id, EdgeType.SUPERSEDES)
                        superseded.append(str(other.id))
            await set_node_metadata(
                self.twin,
                wp,
                **{
                    STATUS_KEY: "accepted" if decision == "accept" else "rejected",
                    "concept_decided_by": actor,
                    "concept_decided_at": now,
                },
            )
        wp = await self._get(node_id)
        logger.info(
            "concept_revision_decided",
            node_id=str(wp.id),
            decision=decision,
            superseded=superseded,
            actor=actor,
        )
        out = self.summary(wp)
        out["superseded"] = superseded
        return out

    # -- reading the project's state -------------------------------------------

    async def state(self, project_id: str) -> dict[str, Any]:
        """The accepted concept, the proposals beside it and the components to map to."""
        from twin_core.models.enums import WorkProductType

        pid = _pid(project_id)
        renders = await self.twin.list_work_products(
            work_product_type=WorkProductType.RENDER, project_id=pid
        )
        family = sorted((r for r in renders if is_concept_revision(r)), key=_created, reverse=True)
        current = next((r for r in family if status_of(r) == "accepted"), None)
        proposals = [r for r in family if status_of(r) == "proposed"]
        components = await self._components(pid) if pid else {}
        return {
            "current": self.summary(current) if current is not None else None,
            "proposals": [self.summary(p) for p in proposals],
            "components": [{"component_id": c, "name": n} for c, n in components.items()],
        }


def segmented_heads(nodes: list[Any]) -> list[Any]:
    """The accepted segmented concept revisions among ``nodes``, newest first."""
    return sorted(
        (
            n
            for n in nodes
            if is_concept_revision(n)
            and status_of(n) == "accepted"
            and isinstance(_meta(n).get(MAPPING_KEY), dict)
            and confirmed_parts(_meta(n)[MAPPING_KEY])
        ),
        key=_created,
        reverse=True,
    )


def part_label_ok(label: str) -> bool:
    """A label never claims more than an illustration (DFM-004, 3D-RC-012)."""
    return "illustrative" in label and not re.search(
        r"\b(verified|validated|production[- ]ready|certified)\b", label, re.IGNORECASE
    )


def make_concept_parts_service(twin: Any, project_backend: Any = None) -> ConceptPartsService:
    """The service the gateway and the MCP sidecar register."""
    return ConceptPartsService(twin, project_backend)


__all__ = [
    "COMPLETE_TOOL",
    "CONCEPT_FAMILY_TOOLS",
    "MAPPING_KEY",
    "PAID_TOOLS",
    "PART_FACES_KEY",
    "REGENERATE_TOOL",
    "SEGMENT_TOOL",
    "ConceptPartsError",
    "ConceptPartsService",
    "apply_confirmation",
    "confirmed_parts",
    "extras_for_mapping",
    "is_concept_revision",
    "make_concept_parts_service",
    "part_extents_mm",
    "part_label_ok",
    "propose_mapping",
    "segmented_heads",
    "status_of",
]
