"""Face-attached sketch planes and reattachment (CDE slice 9, FRS PLN-009..011, RC-05).

A sketch can sit on a planar face of a validated exact CAD revision. This
module is everything about that attachment:

* **Faces** (:func:`list_faces`): the planar faces of one validated
  revision, from ``cadquery.describe_faces`` over the revision's own STEP,
  each with a semantic tag where the revision's recorded build source says
  which sketch primitive made it, and a map from face id to the GLB triangle
  range the viewer picks and highlights. Kept on the revision
  (``face_index``) keyed by its geometry hash, so a second read does no
  adapter work.
* **Attach** (:func:`attach_to_face`): the plane frame from the face (origin
  at the face centroid on its plane, U along the longest straight outer
  edge, the normal outward, right-handed) and the face's geometry
  signature, stored as the sketch plane's next revision, ``ATTACHED``.
* **Remap on a head move** (:func:`remap_on_head_moved`, registered as a
  twin head-moved listener): when a CAD item gets a new head, every plane
  attached to it is looked for on the new revision by signature, never by
  face id (PLN-009). A unique match re-attaches (a new plane revision with
  the moved frame and ``remapped_from``; when the face did not move, only
  the attachment's revision is carried forward in place, so nothing built
  on the plane goes stale). An ambiguous or missing match writes a
  ``REATTACHMENT_REQUIRED`` revision that keeps the last valid attachment
  and frame (PLN-010) and asks a person with a ``review_required`` operation
  event. Nothing is ever picked silently.
* **Resolve** (:func:`resolve_reattachment`): the person picks a face on the
  current revision, or detaches the sketch (to unassigned, or onto a
  principal plane).
* **Blocking** (:func:`plane_block_reason`, :func:`resolve_build_plane`):
  while a plane is ``REATTACHMENT_REQUIRED`` or ``INVALID``, the exact
  build from a sketch on it and the sketch's promotion are refused (409);
  previews and reads are not. Exact revisions built from such a sketch are
  pinned to the plane revision they used (a ``revision_pin``), so the
  plane's next revision makes them, and their STEP, stale through the same
  staleness every record uses (PLN-011).

Every write is a ``sketch`` operation (``job:sketch-plane-<op_id>``), and a
retried ``op_id`` returns the revision it already made.
"""

from __future__ import annotations

import base64
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from api_gateway.twin.sketch_planes import (
    ITEM_TYPE,
    PlaneChangeResult,
    PlaneError,
    _find_by_op_id,
    _lock_for,
    _plane_head,
    _plane_of,
    _publish,
    _ref,
    _result,
    _sketch_head,
    _with_operation,
    _write_revision,
    plane_item_key,
)
from observability.tracing import get_tracer
from twin_core.component_identity import tag_with_component
from twin_core.geometry.face_signature import (
    FaceSignature,
    PlanarFace,
    RemapOutcome,
    frame_from_face,
    part_size_of,
    remap,
    signature_of,
    tag_faces,
)
from twin_core.models.sketch_plane import (
    FaceAttachment,
    PlaneKind,
    PlaneState,
    PlaneUnit,
    PrincipalPlane,
    ProposedBy,
    SketchPlane,
    principal_plane,
    unassigned_plane,
)
from twin_core.representation import COMPONENT_ID_KEY

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.face_attachment")

DESCRIBE_FACES_TOOL = "cadquery.describe_faces"
#: Node metadata key: the revision's face catalogue, keyed by geometry hash.
FACE_INDEX_KEY = "face_index"
#: Plane states that block authoritative commits that depend on the plane.
BLOCKING_STATES = frozenset({PlaneState.REATTACHMENT_REQUIRED, PlaneState.INVALID})
#: A frame that moved less than this (mm, and axis components) did not move.
FRAME_TOLERANCE = 1e-6
CAD_ITEM_TYPES = ("cad_model", "assembly")

MSG_REATTACH = "The surface this sketch was on changed; choose where it goes"

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _record(method: str, *args: Any) -> None:
    try:
        getattr(_collector(), method)(*args)
    except Exception as exc:  # noqa: BLE001 - a counter never fails the work
        logger.debug("face_attachment_metric_failed", method=method, error=str(exc))


class FaceAttachmentError(PlaneError):
    """A face request that cannot be honoured as asked (409 at the route)."""


class FaceCatalogUnavailableError(RuntimeError):
    """The faces of a revision could not be read (the adapter is down, no STEP)."""


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------

_OP_ID = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class FaceView(BaseModel):
    face_id: str
    normal: tuple[float, float, float]
    offset: float
    area: float
    centroid: tuple[float, float, float]
    bounding_box: dict[str, float] | None = None
    outer_loop_edges: int = 0
    longest_edge: dict[str, Any] | None = None
    adjacent: list[dict[str, Any]] = Field(default_factory=list)
    #: Where the face's triangles sit in the revision's converted GLB.
    mesh: dict[str, int] | None = None
    semantic_tag: str | None = None
    signature_summary: str = ""


class FaceListResponse(BaseModel):
    project_id: str
    item_key: str
    revision: int
    item_ref: str
    node_id: str
    geometry_state: str | None = None
    part_size: float
    solid_count: int = 0
    face_count: int = 0
    faces: list[FaceView]
    #: ``face_id`` -> ``{part_index, face_index, triangle_start, triangle_count}``.
    mesh_map: dict[str, dict[str, int]] = Field(default_factory=dict)
    mesh_source: str = ""
    cached: bool = False


class AttachableRevision(BaseModel):
    item_key: str
    name: str
    revision: int
    item_ref: str
    node_id: str
    geometry_state: str | None = None


class AttachableResponse(BaseModel):
    """The CAD revisions a sketch can be attached to (validated exact heads)."""

    project_id: str
    items: list[AttachableRevision]
    #: Why the list is empty, in plain words; ``None`` when it is not.
    reason: str | None = None


class AttachFaceRequest(BaseModel):
    op_id: str = _OP_ID
    item_key: str = Field(min_length=1, max_length=120)
    #: The CAD revision the face was picked on; defaults to the head.
    revision: int | None = Field(default=None, ge=1)
    face_id: str = Field(min_length=1, max_length=200)
    #: The plane revision the client last saw (``PLN-...@n``); a newer head is a conflict.
    base_revision: str | None = Field(default=None, max_length=200)


ResolveAction = Literal["pick_face", "detach_unassigned", "detach_principal"]


class ResolveReattachmentRequest(BaseModel):
    op_id: str = _OP_ID
    action: ResolveAction
    #: ``pick_face``: the face on the attached item's current revision.
    face_id: str | None = Field(default=None, max_length=200)
    revision: int | None = Field(default=None, ge=1)
    #: ``detach_principal``: the principal plane the sketch moves to.
    principal: PrincipalPlane | None = None
    base_revision: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pid(project_id: str | UUID) -> UUID:
    try:
        return UUID(str(project_id))
    except ValueError as exc:
        raise FaceAttachmentError(f"project id {project_id!r} is not a UUID") from exc


#: The exact CAD service face reads go through when this process has no
#: global one (the MCP sidecar passes its own to :func:`register`).
_service_override: Any = None


def _exact_service() -> Any:
    from api_gateway.twin.exact_cad import get_exact_cad_service

    service = get_exact_cad_service() or _service_override
    if service is None:
        raise FaceCatalogUnavailableError("exact CAD is not configured on this gateway")
    return service


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _cad_revision(
    twin: Any, pid: UUID, item_key: str, revision: int | None
) -> tuple[Any, int, Any]:
    """``(item, revision, node)`` of a CAD item's revision; the head when ``None``."""
    from twin_core.items import find_item, item_history, parse_item_ref

    try:
        key, pinned = parse_item_ref(item_key)
    except ValueError as exc:
        raise FaceAttachmentError(str(exc)) from exc
    item = await find_item(twin, key, pid)
    if item is None or item.head_node_id is None:
        raise FaceAttachmentError(f"no CAD item {key} with a current revision in this project")
    if item.item_type not in CAD_ITEM_TYPES:
        raise FaceAttachmentError(f"{key} is a {item.item_type} item, not CAD geometry")
    wanted = revision or pinned or item.head_revision
    node_id = item.head_node_id if wanted == item.head_revision else None
    if node_id is None:
        for rev in await item_history(twin, item, include_drafts=False):
            if rev.revision == wanted:
                node_id = rev.node_id
                break
    if node_id is None:
        raise FaceAttachmentError(f"{key} has no revision @{wanted}")
    node = await twin.get_work_product(node_id)
    if node is None:
        raise FaceAttachmentError(f"{key}@{wanted} could not be read")
    return item, int(wanted), node


def _validated(node: Any) -> tuple[bool, str | None]:
    from twin_core.geometry_state import geometry_state_of, validated_states

    state = geometry_state_of(node.type.value, node.metadata)
    return state in validated_states(), state.value if state else None


async def _faces_of(
    twin: Any, node: Any, ref: str
) -> tuple[list[PlanarFace], dict[str, Any], bool]:
    """``(tagged faces, raw tool result, from cache)`` for one validated revision."""
    from twin_core.geometry_state import EXACT_SOURCE_KEY, set_node_metadata

    meta = dict(node.metadata or {})
    cached = meta.get(FACE_INDEX_KEY)
    raw: dict[str, Any] | None = None
    hit = False
    if isinstance(cached, dict) and cached.get("content_hash") == node.content_hash:
        raw, hit = cached, True
    if raw is None:
        service = _exact_service()
        step = service.read_step(node)
        if not step:
            raise FaceCatalogUnavailableError(f"{ref} has no stored STEP to read faces from")
        started = time.monotonic()
        try:
            result = await service.invoke_tool(
                DESCRIBE_FACES_TOOL, {"step_base64": base64.b64encode(step).decode("ascii")}
            )
        except Exception as exc:  # noqa: BLE001 - reported to the caller as unavailable
            raise FaceCatalogUnavailableError(
                f"the faces of {ref} could not be read: {exc}"[:400]
            ) from exc
        _record("record_cad_artifact_duration", "describe_faces", time.monotonic() - started)
        raw = {
            "content_hash": node.content_hash,
            "faces": list(result.get("faces") or []),
            "bounding_box": result.get("bounding_box"),
            "solid_count": int(result.get("solid_count") or 0),
            "face_count": int(result.get("face_count") or 0),
            "mesh_source": str(result.get("mesh_source") or ""),
            "described_at": _now(),
        }
        try:
            await set_node_metadata(twin, node, **{FACE_INDEX_KEY: raw})
        except Exception as exc:  # noqa: BLE001 - the faces are still returned
            logger.info("face_index_not_cached", ref=ref, error=str(exc))
    faces = [PlanarFace.model_validate(f) for f in raw.get("faces") or []]
    size = part_size_of(faces, raw.get("bounding_box"))
    faces = tag_faces(faces, meta.get(EXACT_SOURCE_KEY), size)
    # C3D slice 2 (SYNC-002): a face says which component made it.
    component = meta.get(COMPONENT_ID_KEY)
    if isinstance(component, str) and component:
        faces = [
            f.model_copy(update={"semantic_tag": tag_with_component(component, f.semantic_tag)})
            if f.semantic_tag
            else f
            for f in faces
        ]
    return faces, raw, hit


def _face_view(face: PlanarFace, size: float) -> FaceView:
    return FaceView(
        **face.model_dump(),
        signature_summary=signature_of(face, size).summary(),
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def attachable_revisions(twin: Any, project_id: str) -> AttachableResponse:
    """The CAD items whose head is validated exact geometry (a face can be picked)."""
    from twin_core.items import list_items

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.face_attachment.attachable") as span:
        span.set_attribute("project_id", str(pid))
        out: list[AttachableRevision] = []
        seen = 0
        for item_type in CAD_ITEM_TYPES:
            for item in await list_items(twin, project_id=pid, item_type=item_type):
                if item.head_node_id is None:
                    continue
                seen += 1
                node = await twin.get_work_product(item.head_node_id)
                if node is None:
                    continue
                ok, state = _validated(node)
                if not ok:
                    continue
                out.append(
                    AttachableRevision(
                        item_key=item.key,
                        name=item.name,
                        revision=item.head_revision,
                        item_ref=f"{item.key}@{item.head_revision}",
                        node_id=str(node.id),
                        geometry_state=state,
                    )
                )
        reason = None
        if not out:
            reason = (
                "There is no validated exact geometry in this project yet. Build and validate "
                "a part first, then pick one of its faces."
                if seen
                else "There is no CAD geometry in this project yet to put a sketch on."
            )
        span.set_attribute("face_attachment.attachable", len(out))
        return AttachableResponse(project_id=str(pid), items=out, reason=reason)


async def list_faces(
    twin: Any, project_id: str, item_key: str, revision: int | None = None
) -> FaceListResponse:
    """The planar faces of a validated CAD revision, with the GLB mesh map."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.face_attachment.list_faces") as span:
        span.set_attribute("project_id", str(pid))
        item, rev, node = await _cad_revision(twin, pid, item_key, revision)
        ref = f"{item.key}@{rev}"
        span.set_attribute("item.ref", ref)
        ok, state = _validated(node)
        if not ok:
            raise FaceAttachmentError(
                f"{ref} is {state or 'not geometry'}; a sketch can be put on a face only of "
                "validated exact geometry. Build or validate it first."
            )
        faces, raw, hit = await _faces_of(twin, node, ref)
        size = part_size_of(faces, raw.get("bounding_box"))
        span.set_attribute("faces.planar", len(faces))
        span.set_attribute("faces.cached", hit)
        logger.info(
            "face_catalogue_read",
            project_id=str(pid),
            item_ref=ref,
            faces=len(faces),
            tagged=sum(1 for f in faces if f.semantic_tag),
            cached=hit,
        )
        return FaceListResponse(
            project_id=str(pid),
            item_key=item.key,
            revision=rev,
            item_ref=ref,
            node_id=str(node.id),
            geometry_state=state,
            part_size=round(size, 6),
            solid_count=int(raw.get("solid_count") or 0),
            face_count=int(raw.get("face_count") or 0),
            faces=[_face_view(f, size) for f in faces],
            mesh_map={f.face_id: f.mesh for f in faces if f.mesh},
            mesh_source=str(raw.get("mesh_source") or ""),
            cached=hit,
        )


# ---------------------------------------------------------------------------
# Writing: attach and resolve
# ---------------------------------------------------------------------------


def _face_plane(
    plane_id: str,
    face: PlanarFace,
    *,
    item_key: str,
    revision: int,
    size: float,
    current: SketchPlane | None,
    source_revision: str,
    proposed_by: ProposedBy = ProposedBy.USER,
    confidence: float = 1.0,
    remapped_from: str | None = None,
    signature: FaceSignature | None = None,
) -> SketchPlane:
    frame = frame_from_face(face)
    sig = signature or signature_of(face, size)
    # CAD geometry is in millimetres; a sketch scale already set is kept.
    scale = current.scale if current is not None and current.unit is PlaneUnit.MM else None
    return SketchPlane(
        plane_id=plane_id,
        owner_component=item_key,
        kind=PlaneKind.FACE,
        origin=frame.origin,
        u_axis=frame.u_axis,
        v_axis=frame.v_axis,
        normal=frame.normal,
        unit=PlaneUnit.MM,
        scale=scale,
        source_revision=source_revision,
        attachment=FaceAttachment(
            item_key=item_key,
            revision=revision,
            face_ref=face.face_id,
            signature=sig.model_dump(mode="json"),
            remapped_from=remapped_from,
        ),
        state=PlaneState.ATTACHED,
        proposed_by=proposed_by,
        confidence=confidence,
    )


def _same_attachment(a: SketchPlane, b: SketchPlane) -> bool:
    if a.state != b.state or a.kind != b.kind or a.attachment is None or b.attachment is None:
        return False
    return (
        a.attachment.item_key == b.attachment.item_key
        and a.attachment.revision == b.attachment.revision
        and a.attachment.face_ref == b.attachment.face_ref
    )


def _frame_moved(a: SketchPlane, b: SketchPlane) -> bool:
    return any(
        abs(x - y) > FRAME_TOLERANCE
        for va, vb in (
            (a.origin, b.origin),
            (a.u_axis, b.u_axis),
            (a.v_axis, b.v_axis),
            (a.normal, b.normal),
        )
        for x, y in zip(va, vb, strict=True)
    )


async def _pick(
    twin: Any, pid: UUID, item_key: str, revision: int | None, face_id: str
) -> tuple[Any, int, PlanarFace, float]:
    item, rev, node = await _cad_revision(twin, pid, item_key, revision)
    ref = f"{item.key}@{rev}"
    ok, state = _validated(node)
    if not ok:
        raise FaceAttachmentError(
            f"{ref} is {state or 'not geometry'}; attach a sketch only to validated exact geometry"
        )
    faces, raw, _hit = await _faces_of(twin, node, ref)
    face = next((f for f in faces if f.face_id == face_id), None)
    if face is None:
        raise FaceAttachmentError(
            f"{ref} has no planar face {face_id!r}; read its faces again and pick one"
        )
    return item, rev, face, part_size_of(faces, raw.get("bounding_box"))


async def attach_to_face(
    twin: Any, project_id: str, sketch_key: str, request: AttachFaceRequest
) -> PlaneChangeResult:
    """Put the sketch on a planar face of a validated CAD revision (ATTACHED)."""
    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.face_attachment.attach") as span:
                span.set_attribute("project_id", str(pid))
                span.set_attribute("face.id", request.face_id)
                sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                current = _plane_of(head) if head is not None else None
                item, rev, face, size = await _pick(
                    twin, pid, request.item_key, request.revision, request.face_id
                )
                span.set_attribute("item.ref", f"{item.key}@{rev}")
                plane = _face_plane(
                    plane_item_key(sk_item.key),
                    face,
                    item_key=item.key,
                    revision=rev,
                    size=size,
                    current=current,
                    source_revision=sketch_ref,
                )
                if head is not None and current is not None and _same_attachment(current, plane):
                    return _result(sk_item.key, sketch_ref, head, request.op_id, unchanged=True)
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    plane,
                    op_id=request.op_id,
                    action="attached",
                    base_revision=request.base_revision,
                    undo_to=plane_item.head_revision if plane_item is not None else 0,
                    extra={
                        "attached_face": {"item_ref": f"{item.key}@{rev}", "face_id": face.face_id}
                    },
                )
                _record("record_face_attachment", "attached")
                logger.info(
                    "sketch_plane_attached_to_face",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    item_ref=f"{item.key}@{rev}",
                    face_id=face.face_id,
                    semantic_tag=face.semantic_tag,
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(sk_item.key, sketch_ref, node, request.op_id)

    return await _with_operation(
        project_id,
        request.op_id,
        "Placing sketch on a model surface",
        run,
        lambda r: "Sketch placed on a model surface",
    )


async def resolve_reattachment(
    twin: Any, project_id: str, sketch_key: str, request: ResolveReattachmentRequest
) -> PlaneChangeResult:
    """A person's answer to ``REATTACHMENT_REQUIRED``: a face, or detach."""
    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.face_attachment.resolve") as span:
                span.set_attribute("project_id", str(pid))
                span.set_attribute("face_attachment.action", request.action)
                sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                if plane_item is None or head is None:
                    raise FaceAttachmentError("this sketch has no plane to resolve")
                current = _plane_of(head)
                if current.state not in BLOCKING_STATES:
                    raise FaceAttachmentError(
                        f"this sketch's plane is {current.state.value}; there is nothing to resolve"
                    )
                last = current.attachment
                plane: SketchPlane
                extra: dict[str, Any] = {"resolved_from": _ref(head.metadata or {})}
                if request.action == "pick_face":
                    if not request.face_id or last is None:
                        raise FaceAttachmentError("pick_face needs a face_id")
                    item, rev, face, size = await _pick(
                        twin, pid, last.item_key, request.revision, request.face_id
                    )
                    plane = _face_plane(
                        plane_item_key(sk_item.key),
                        face,
                        item_key=item.key,
                        revision=rev,
                        size=size,
                        current=current,
                        source_revision=sketch_ref,
                        remapped_from=f"{last.item_key}@{last.revision}#{last.face_ref}",
                    )
                    extra["attached_face"] = {
                        "item_ref": f"{item.key}@{rev}",
                        "face_id": face.face_id,
                    }
                elif request.action == "detach_principal":
                    if request.principal is None:
                        raise FaceAttachmentError("detach_principal needs a principal plane")
                    plane = principal_plane(
                        request.principal,
                        plane_id=plane_item_key(sk_item.key),
                        origin=current.origin,
                        unit=current.unit,
                        scale=current.scale,
                        owner_component=current.owner_component,
                        source_revision=sketch_ref,
                    )
                else:
                    plane = unassigned_plane(
                        plane_id=plane_item_key(sk_item.key),
                        owner_component=current.owner_component,
                        source_revision=sketch_ref,
                    )
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    plane,
                    op_id=request.op_id,
                    action=f"reattachment_{request.action}",
                    base_revision=request.base_revision,
                    undo_to=plane_item.head_revision,
                    extra=extra,
                )
                _record("record_face_attachment", f"resolved_{request.action}")
                logger.info(
                    "sketch_plane_reattachment_resolved",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    action=request.action,
                    face_id=request.face_id,
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(sk_item.key, sketch_ref, node, request.op_id)

    titles = {
        "pick_face": "Sketch placed on the chosen surface",
        "detach_principal": "Sketch moved off the model onto a plane",
        "detach_unassigned": "Sketch detached and kept as a concept",
    }
    return await _with_operation(
        project_id,
        request.op_id,
        "Resolving where the sketch goes",
        run,
        lambda r: titles[request.action],
    )


# ---------------------------------------------------------------------------
# Remap when a CAD item's head moves
# ---------------------------------------------------------------------------


def _op_slug(*parts: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", "-".join(str(p) for p in parts))[:90]


async def _attached_planes(twin: Any, pid: UUID | None, item_key: str) -> list[tuple[Any, Any]]:
    """``(plane item, head node)`` for every face plane attached to ``item_key``."""
    from twin_core.items import list_items

    out: list[tuple[Any, Any]] = []
    for plane_item in await list_items(twin, project_id=pid, item_type=ITEM_TYPE):
        if plane_item.head_node_id is None:
            continue
        node = await twin.get_work_product(plane_item.head_node_id)
        if node is None:
            continue
        try:
            plane = _plane_of(node)
        except Exception:  # noqa: BLE001 - a malformed revision is skipped
            continue
        if plane.kind is not PlaneKind.FACE or plane.attachment is None:
            continue
        if plane.attachment.item_key != item_key:
            continue
        if plane.state not in (PlaneState.ATTACHED, PlaneState.REATTACHMENT_REQUIRED):
            continue
        out.append((plane_item, node))
    return out


async def remap_on_head_moved(
    twin: Any, item: Any, head_revision: int, head_node_id: UUID | None
) -> None:
    """Twin head-moved listener: re-find every plane attached to ``item``. Never raises."""
    if str(getattr(item, "item_type", "") or "") not in CAD_ITEM_TYPES:
        return
    pid = getattr(item, "project_id", None)
    try:
        planes = await _attached_planes(twin, pid, item.key)
    except Exception as exc:  # noqa: BLE001 - never undo the head move
        logger.warning("face_remap_scan_failed", item_key=item.key, error=str(exc))
        return
    for plane_item, node in planes:
        try:
            await _remap_one(twin, pid, item, head_revision, head_node_id, plane_item, node)
        except Exception as exc:  # noqa: BLE001 - one plane never stops the others
            _record("record_face_remap", "error")
            logger.warning(
                "face_remap_failed",
                item_key=item.key,
                plane_key=plane_item.key,
                error=str(exc),
            )


async def _remap_one(
    twin: Any,
    pid: UUID | None,
    item: Any,
    head_revision: int,
    head_node_id: UUID | None,
    plane_item: Any,
    plane_node: Any,
) -> None:
    plane = _plane_of(plane_node)
    attachment = plane.attachment
    assert attachment is not None
    meta = plane_node.metadata or {}
    pending = attachment.pending or {}
    if attachment.revision == head_revision and plane.state is PlaneState.ATTACHED:
        return
    if plane.state is PlaneState.REATTACHMENT_REQUIRED and pending.get("revision") == head_revision:
        return
    sketch_key = str(meta.get("sketch_item_key") or "")
    project_id = str(pid) if pid else ""
    op_id = _op_slug("remap", plane_item.key, item.key, f"r{head_revision}")
    new_ref = f"{item.key}@{head_revision}"
    with tracer.start_as_current_span("twin.face_attachment.remap") as span:
        span.set_attribute("item.ref", new_ref)
        span.set_attribute("plane.key", plane_item.key)
        _publish(
            project_id,
            op_id,
            "accepted",
            "running",
            title="Checking the surface a sketch sits on",
            message=f"{item.key} changed; finding the face {sketch_key} sits on in {new_ref}.",
            target_key=plane_item.key,
        )
        sig = FaceSignature.model_validate(attachment.signature or {})
        node = await twin.get_work_product(head_node_id) if head_node_id else None
        outcome: str
        reason: str
        faces: list[PlanarFace] = []
        size = sig.part_size
        result = None
        if node is None:
            outcome, reason = "missing", f"{new_ref} could not be read"
        else:
            ok, state = _validated(node)
            if not ok:
                outcome = "missing"
                reason = (
                    f"{new_ref} is {state or 'not geometry'}, not validated exact geometry, "
                    "so its faces cannot be trusted yet"
                )
            else:
                try:
                    faces, raw, _hit = await _faces_of(twin, node, new_ref)
                except FaceCatalogUnavailableError as exc:
                    faces, raw = [], {}
                    reason = str(exc)
                    outcome = "unavailable"
                else:
                    size = part_size_of(faces, raw.get("bounding_box"))
                    result = remap(sig, faces, part_size=size)
                    outcome, reason = result.outcome.value, result.reason
        span.set_attribute("face_remap.outcome", outcome)
        _record("record_face_remap", outcome)
        sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)  # type: ignore[arg-type]
        sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
        last = f"{attachment.item_key}@{attachment.revision}#{attachment.face_ref}"

        if result is not None and result.outcome is RemapOutcome.UNIQUE and result.face:
            moved_to = _face_plane(
                plane.plane_id,
                result.face,
                item_key=item.key,
                revision=head_revision,
                size=size,
                current=plane,
                source_revision=plane.source_revision or sketch_ref,
                proposed_by=plane.proposed_by or ProposedBy.USER,
                confidence=plane.confidence,
                remapped_from=last,
            )
            if plane.state is PlaneState.ATTACHED and not _frame_moved(plane, moved_to):
                # The face did not move: carry the attachment forward in place,
                # so nothing built on the plane goes stale for no reason.
                from twin_core.geometry_state import set_node_metadata

                await set_node_metadata(
                    twin,
                    plane_node,
                    plane=moved_to.model_dump(mode="json"),
                    plane_remap={"from": last, "to": new_ref, "frame_moved": False, "at": _now()},
                )
                written_ref = _ref(meta)
                moved = False
            else:
                async with _lock_for(project_id):
                    written = await _write_revision(
                        twin,
                        pid,  # type: ignore[arg-type]
                        sk_item,
                        sk_node,
                        moved_to,
                        op_id=op_id,
                        action="remapped",
                        base_revision=None,
                        undo_to=plane_item.head_revision,
                        extra={"plane_remap": {**result.as_dict(), "from": last, "to": new_ref}},
                    )
                written_ref = _ref(written.metadata or {})
                moved = True
            logger.info(
                "sketch_plane_remapped",
                plane_key=plane_item.key,
                item_ref=new_ref,
                face_id=result.face.face_id,
                score=result.score,
                frame_moved=moved,
                plane_ref=written_ref,
            )
            _publish(
                project_id,
                op_id,
                "completed",
                "completed",
                title="Sketch kept on its surface",
                message=(
                    f"{sketch_key} is on {result.face.face_id} of {new_ref}"
                    + (" (the surface moved, so the sketch moved with it)." if moved else ".")
                ),
                target_key=plane_item.key,
                target_revision=written_ref,
            )
            return

        # Ambiguous, missing or unreadable: keep the last valid attachment and
        # frame, mark it, and ask a person (PLN-010). Never pick silently.
        candidates = result.as_dict()["candidates"] if result is not None else []
        kept = plane.model_copy(
            update={
                "state": PlaneState.REATTACHMENT_REQUIRED,
                "attachment": attachment.model_copy(
                    update={
                        "pending": {
                            "outcome": outcome,
                            "revision": head_revision,
                            "item_ref": new_ref,
                            "node_id": str(head_node_id) if head_node_id else None,
                            "candidates": candidates,
                            "reason": reason,
                            "at": _now(),
                        }
                    }
                ),
            }
        )
        kept = SketchPlane.model_validate(kept.model_dump())
        async with _lock_for(project_id):
            written = await _write_revision(
                twin,
                pid,  # type: ignore[arg-type]
                sk_item,
                sk_node,
                kept,
                op_id=op_id,
                action="reattachment_required",
                base_revision=None,
                undo_to=plane_item.head_revision,
                extra={
                    "plane_remap": {
                        "outcome": outcome,
                        "from": last,
                        "to": new_ref,
                        "reason": reason,
                    }
                },
            )
        written_ref = _ref(written.metadata or {})
        logger.warning(
            "sketch_plane_reattachment_required",
            plane_key=plane_item.key,
            item_ref=new_ref,
            outcome=outcome,
            reason=reason,
            candidates=len(candidates),
            plane_ref=written_ref,
        )
        _publish(
            project_id,
            op_id,
            "review_required",
            "waiting_for_user",
            title=MSG_REATTACH,
            stage_id="reattach",
            stage_title="Choose a surface",
            message=(
                f"{MSG_REATTACH}. {sketch_key} was on {last}; in {new_ref} "
                + (
                    "more than one face could be it."
                    if outcome == RemapOutcome.AMBIGUOUS.value
                    else "that face could not be found."
                )
            ),
            severity="warning",
            target_key=plane_item.key,
            target_revision=written_ref,
            requires_action=True,
            action_refs=[
                {
                    # Opens the Engineering viewer on the sketch, where its
                    # plane controls offer the choices.
                    "kind": "open",
                    "id": f"/twin?node={sk_node.id}&tab=model",
                    "label": "Choose where the sketch goes",
                }
            ],
        )


# ---------------------------------------------------------------------------
# Blocking dependent commits (PLN-010, PLN-011)
# ---------------------------------------------------------------------------


async def plane_block_reason(twin: Any, project_id: Any, plane_ref: str | None) -> str | None:
    """Why an authoritative commit on ``plane_ref``'s plane is refused, or ``None``."""
    from twin_core.items import find_item, parse_item_ref

    if not plane_ref:
        return None
    try:
        key, _rev = parse_item_ref(str(plane_ref))
    except ValueError:
        return None
    item = await find_item(twin, key, project_id)
    if item is None or item.head_node_id is None:
        return None
    node = await twin.get_work_product(item.head_node_id)
    if node is None:
        return None
    try:
        plane = _plane_of(node)
    except Exception:  # noqa: BLE001 - an unreadable plane does not block
        return None
    if plane.state not in BLOCKING_STATES:
        return None
    where = ""
    if plane.attachment is not None:
        where = f" (it was on {plane.attachment.item_key}@{plane.attachment.revision})"
    return (
        f"the sketch's plane {item.key}@{item.head_revision} is {plane.state.value}{where}: "
        f"{MSG_REATTACH.lower()} before this can be committed"
    )


def record_blocked(commit: str) -> None:
    _record("record_plane_blocked_commit", commit)


async def resolve_build_plane(
    twin: Any, plane_ref: str, project_id: Any
) -> tuple[dict[str, Any], str]:
    """``(design-IR fields, plane ref used)`` for an exact build on ``plane_ref``.

    A principal plane is used as pinned. A face plane follows its item's head
    (a remap moves it with its face), which must be attached. Raises
    ``ValueError`` when the plane cannot be used.
    """
    from twin_core.items import find_item, resolve_item_ref

    _item, rev = await resolve_item_ref(twin, plane_ref, project_id)
    node = await twin.graph.get_node(rev.node_id)
    plane = SketchPlane.model_validate((node.metadata or {})["plane"])
    used = f"{_item.key}@{rev.revision}"
    if plane.kind is PlaneKind.FACE:
        head_item = await find_item(twin, _item.key, project_id)
        if head_item is None or head_item.head_node_id is None:
            raise ValueError(f"{_item.key} has no current revision")
        head = await twin.graph.get_node(head_item.head_node_id)
        plane = SketchPlane.model_validate((head.metadata or {})["plane"])
        used = f"{head_item.key}@{head_item.head_revision}"
    return plane.design_ir_fields(), used


async def pin_build_to_plane(twin: Any, node_id: Any, plane_ref: str, project_id: Any) -> None:
    """Pin an exact revision to the plane revision it was built on. Never raises.

    The pin is the same ``revision_pin`` every record uses, so the plane's
    next revision (a remap, or ``REATTACHMENT_REQUIRED``) marks the revision
    stale, and with it its STEP export (PLN-011).
    """
    from twin_core.consistency.record_pins import PIN_EDGE_KIND, PINS_KEY, STALENESS_KEY, ItemPin
    from twin_core.geometry_state import set_node_metadata
    from twin_core.items import resolve_item_ref
    from twin_core.models.enums import EdgeType

    try:
        item, rev = await resolve_item_ref(twin, plane_ref, project_id)
        node = await twin.graph.get_node(UUID(str(node_id)))
        if node is None:
            return
        pin = ItemPin(item.key, rev.revision, rev.node_id, ITEM_TYPE)
        pins = [p for p in (node.metadata or {}).get(PINS_KEY) or [] if isinstance(p, dict)]
        if not any(p.get("item_ref") == pin.ref for p in pins):
            pins.append(pin.to_json())
        await set_node_metadata(twin, node, **{PINS_KEY: pins, STALENESS_KEY: "current"})
        await twin.add_edge(
            node.id,
            rev.node_id,
            EdgeType.DEPENDS_ON,
            metadata={
                "kind": PIN_EDGE_KIND,
                "item_key": item.key,
                "revision": rev.revision,
                "item_ref": pin.ref,
            },
        )
        logger.info("exact_build_pinned_to_plane", node_id=str(node_id), plane_ref=pin.ref)
    except Exception as exc:  # noqa: BLE001 - the revision is saved; the pin is reported
        logger.warning(
            "exact_build_plane_pin_failed",
            node_id=str(node_id),
            plane_ref=plane_ref,
            error=str(exc),
        )


def register(service: Any = None) -> None:
    """Listen for CAD head moves (idempotent); ``service`` reads faces where no
    global exact CAD service is set (the MCP sidecar)."""
    global _service_override  # noqa: PLW0603
    from twin_core.consistency.record_pins import register_head_moved_listener

    if service is not None:
        _service_override = service
    register_head_moved_listener(remap_on_head_moved)


__all__ = [
    "AttachFaceRequest",
    "AttachableResponse",
    "FaceAttachmentError",
    "FaceCatalogUnavailableError",
    "FaceListResponse",
    "ResolveReattachmentRequest",
    "attach_to_face",
    "attachable_revisions",
    "list_faces",
    "pin_build_to_plane",
    "plane_block_reason",
    "record_blocked",
    "register",
    "remap_on_head_moved",
    "resolve_build_plane",
    "resolve_reattachment",
]
