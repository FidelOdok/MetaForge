"""The sketch plane manager (FORGE-790).

The model: a plane is an orthonormal, right-handed frame with a transform
that matches it, the three principal frames follow one convention (Front=XZ,
Side=YZ, Top=XY, matching the design IR's plane names), and an unknown unit
is never converted.

The manager: one ``sketch_plane`` item per sketch, every change its next
revision pinned to the sketch revision with a ``DEPENDS_ON`` edge, an
explicit choice never overridden by inference, proposals in inference order
with ambiguity, reassign previews that write nothing in each of the three
modes, commits that make new revisions (and new sketch revisions when the
strokes move), undo, operation events, and nothing that lets the camera move
the plane.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api_gateway.operations import publisher as ops
from api_gateway.twin import routes as twin_routes
from api_gateway.twin import sketch_planes as sp
from api_gateway.twin.sketch_plane_routes import router as plane_router
from api_gateway.twin.sketch_strokes_recorder import (
    SketchSaveRequest,
    current_sketch,
    make_sketch_strokes_recorder,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.design_ir.models import SketchEntity
from twin_core.items import find_item, item_history
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.sketch_plane import (
    PRINCIPAL_FRAMES,
    VIEW_NAMES,
    PlaneKind,
    PlaneState,
    PlaneUnit,
    PrincipalPlane,
    ProposedBy,
    SketchPlane,
    UnknownUnitError,
    cross,
    frame_matrix,
    principal_plane,
    reassign_point,
    unassigned_plane,
)

PROJECT = "11111111-2222-3333-4444-555555555555"


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def blobs(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    stored: dict[str, bytes] = {}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        stored[key] = content
        return key

    def fake_fetch(key: str) -> bytes:
        return stored[key]

    import api_gateway.twin.blob_store as blob_store
    import digital_twin.storage.work_product_blobs as wpb

    monkeypatch.setattr(wpb, "store_work_product_blob", fake_store)
    monkeypatch.setattr(blob_store, "fetch_work_product_blob", fake_fetch)
    return stored


@pytest.fixture
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


def _stroke(sid: str, *points: tuple[float, float]) -> dict[str, Any]:
    return {"id": sid, "tool": "pen", "points": [list(p) for p in points]}


async def _sketch(twin: InMemoryTwinAPI, *strokes: dict[str, Any], name: str = "Sketch") -> str:
    save = make_sketch_strokes_recorder(twin)
    out = await save(
        PROJECT,
        SketchSaveRequest(
            save_id=f"save-{len(strokes)}-{name.replace(' ', '')}"[:60].ljust(8, "x"),
            name=name,
            strokes=list(strokes) or [_stroke("s1", (0, 0), (0.2, 0.1))],
        ),
    )
    assert out.item_key
    return out.item_key


def _confirm(principal: str, op: str, **kw: Any) -> sp.PlaneConfirmRequest:
    return sp.PlaneConfirmRequest(op_id=op, principal=PrincipalPlane(principal), **kw)


async def _plane_nodes(twin: InMemoryTwinAPI) -> list[Any]:
    return await twin.list_work_products(
        work_product_type=WorkProductType.SKETCH_PLANE, project_id=UUID(PROJECT)
    )


# ── the model ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("principal", list(PrincipalPlane))
def test_principal_frames_are_orthonormal_and_right_handed(principal: PrincipalPlane) -> None:
    u, v, n = PRINCIPAL_FRAMES[principal]
    assert cross(u, v) == n
    plane = principal_plane(principal, plane_id="PLN-X")
    assert plane.kind is PlaneKind.PRINCIPAL and plane.state is PlaneState.CONFIRMED
    assert plane.transform == frame_matrix((0, 0, 0), u, v, n)


def test_principal_conventions_front_side_top() -> None:
    assert VIEW_NAMES == {
        PrincipalPlane.XZ: "Front",
        PrincipalPlane.YZ: "Side",
        PrincipalPlane.XY: "Top",
    }
    assert PRINCIPAL_FRAMES[PrincipalPlane.XY] == ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert PRINCIPAL_FRAMES[PrincipalPlane.XZ] == ((1, 0, 0), (0, 0, 1), (0, -1, 0))
    assert PRINCIPAL_FRAMES[PrincipalPlane.YZ] == ((0, 1, 0), (0, 0, 1), (1, 0, 0))


def test_a_principal_plane_maps_onto_the_design_ir_sketch() -> None:
    plane = principal_plane("XZ", plane_id="PLN-X", origin=(0, -12.0, 0))
    fields = plane.design_ir_fields()
    assert fields == {"plane": "XZ", "offset": 12.0}
    entity = SketchEntity(id="sk1", body_ref="b1", **fields)
    assert entity.plane == "XZ" and entity.offset == 12.0
    with pytest.raises(ValueError):
        unassigned_plane(plane_id="PLN-X").design_ir_fields()


def test_validators_refuse_a_non_orthonormal_or_left_handed_frame() -> None:
    base: dict[str, Any] = {
        "plane_id": "PLN-X",
        "kind": "custom",
    }
    # Reserved kinds are refused outright.
    with pytest.raises(ValidationError, match="reserved"):
        SketchPlane(**base)
    good = principal_plane("XY", plane_id="PLN-X").model_dump()
    with pytest.raises(ValidationError, match="unit vector"):
        SketchPlane(**{**good, "u_axis": (2, 0, 0), "transform": None})
    with pytest.raises(ValidationError, match="orthogonal"):
        SketchPlane(
            **{**good, "v_axis": (0.6, 0.8, 0), "transform": None, "kind": "unassigned"}
            | {"principal": None, "state": "UNASSIGNED"}
        )
    # Left-handed: normal = -(u x v).
    with pytest.raises(ValidationError, match="right-handed"):
        SketchPlane(**{**good, "normal": (0, 0, -1), "transform": None})
    with pytest.raises(ValidationError, match="finite"):
        SketchPlane(**{**good, "origin": (float("nan"), 0, 0), "transform": None})


def test_a_transform_must_match_origin_and_axes() -> None:
    plane = principal_plane("YZ", plane_id="PLN-X", origin=(5, 6, 7))
    assert plane.transform is not None
    assert [row[3] for row in plane.transform] == [5, 6, 7, 1]
    again = SketchPlane.model_validate(plane.model_dump())
    assert again.transform == plane.transform
    bad = plane.model_dump()
    bad["transform"][0][3] = 99.0
    with pytest.raises(ValidationError, match="transform does not match"):
        SketchPlane.model_validate(bad)


def test_a_principal_plane_must_have_its_conventional_axes() -> None:
    xy = principal_plane("XY", plane_id="PLN-X").model_dump()
    with pytest.raises(ValidationError, match="principal plane has the axes"):
        SketchPlane.model_validate({**xy, "principal": "XZ"})


def test_lifecycle_rules() -> None:
    with pytest.raises(ValidationError):
        SketchPlane(plane_id="PLN-X", state=PlaneState.CONFIRMED)  # unassigned cannot be confirmed
    with pytest.raises(ValidationError, match="ATTACHED"):
        principal_plane("XY", plane_id="PLN-X", state=PlaneState.ATTACHED)
    with pytest.raises(ValidationError, match="attachment"):
        SketchPlane(plane_id="PLN-X", kind=PlaneKind.FACE, state=PlaneState.ATTACHED)
    face = SketchPlane(
        plane_id="PLN-X",
        kind=PlaneKind.FACE,
        state=PlaneState.ATTACHED,
        attachment={"item_key": "CAD-BOX", "revision": 2, "face_ref": "Face6"},
    )
    assert face.placed and face.attachment is not None
    assert unassigned_plane(plane_id="PLN-X").state is PlaneState.UNASSIGNED
    assert set(PlaneState) == {
        PlaneState.UNASSIGNED,
        PlaneState.PROPOSED,
        PlaneState.CONFIRMED,
        PlaneState.ATTACHED,
        PlaneState.INVALID,
        PlaneState.REATTACHMENT_REQUIRED,
        PlaneState.ARCHIVED,
    }


def test_an_unknown_unit_is_kept_and_never_converted() -> None:
    plane = principal_plane("XY", plane_id="PLN-X")
    assert plane.unit is PlaneUnit.UNKNOWN and plane.scale is None
    assert plane.convert_length(3.0, PlaneUnit.UNKNOWN) == 3.0
    with pytest.raises(UnknownUnitError):
        plane.convert_length(3.0, PlaneUnit.MM)
    inch = principal_plane("XY", plane_id="PLN-X", unit=PlaneUnit.IN)
    assert inch.convert_length(2.0, PlaneUnit.MM) == pytest.approx(50.8)
    with pytest.raises(ValidationError, match="known unit"):
        principal_plane("XY", plane_id="PLN-X", scale=2.0)


def test_world_and_local_round_trip() -> None:
    plane = principal_plane("XZ", plane_id="PLN-X", origin=(1, 2, 3))
    world = plane.to_world(0.5, 0.25)
    assert world == pytest.approx((1.5, 2.0, 3.25))
    assert plane.to_local(world) == pytest.approx((0.5, 0.25, 0.0))


def test_reassign_point_in_each_mode() -> None:
    xy = principal_plane("XY", plane_id="P")
    xz = principal_plane("XZ", plane_id="P")
    moved_xy = principal_plane("XY", plane_id="P", origin=(0.1, 0.2, 0))
    # preserve_local keeps the coordinates.
    assert reassign_point(0.3, -0.4, xy, xz, "preserve_local") == (0.3, -0.4, 0.0)
    # preserve_world on a plane in the same place: exact, coordinates shift.
    x, y, off = reassign_point(0.3, -0.4, xy, moved_xy, "preserve_world")
    assert (x, y, off) == pytest.approx((0.2, -0.2, 0.0))
    # Onto a perpendicular plane: the point is off it; projecting drops that.
    x, y, off = reassign_point(0.3, -0.4, xy, xz, "project_to_target")
    assert (x, y) == pytest.approx((0.3, 0.0)) and off == pytest.approx(0.4)


# ── reading and proposing ───────────────────────────────────────────────


async def test_an_unplaced_sketch_reads_as_unassigned(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    view = await sp.get_plane(twin, PROJECT, key)
    assert view.plane.state is PlaneState.UNASSIGNED and view.item_key is None
    assert view.plane.plane_id == "PLN-SKETCH"
    with pytest.raises(sp.PlaneError, match="no saved sketch"):
        await sp.get_plane(twin, PROJECT, "SKT-NOPE")


async def test_proposal_order_and_confidence(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    req = sp.PlaneProposalRequest(view_hint="side", owner_component="CAD-BODY")
    out = await sp.propose_plane(twin, PROJECT, key, req)
    order = [c.proposed_by for c in out.candidates]
    assert order[:2] == [ProposedBy.VIEW, ProposedBy.COMPONENT_FRAME]
    assert order[-1] is ProposedBy.MODEL
    assert out.candidate.principal is PrincipalPlane.YZ
    assert out.candidate.state is PlaneState.PROPOSED
    assert out.candidate.proposed_by is ProposedBy.VIEW
    assert out.candidate.confidence == pytest.approx(0.7)
    assert out.requires_confirmation is True and out.ambiguous is False

    # The explicit selection goes first and is never ambiguous.
    explicit = await sp.propose_plane(
        twin, PROJECT, key, sp.PlaneProposalRequest(explicit="XY", view_hint="side")
    )
    assert explicit.candidates[0].proposed_by is ProposedBy.USER
    assert explicit.candidate.principal is PrincipalPlane.XY and not explicit.ambiguous


async def test_an_existing_sketch_on_the_same_component_outranks_the_view(
    twin: InMemoryTwinAPI,
) -> None:
    first = await _sketch(twin, name="Bracket front")
    second = await _sketch(twin, _stroke("a", (0, 0), (0.1, 0.1)), name="Bracket hole")
    await sp.confirm_plane(
        twin, PROJECT, first, _confirm("XZ", "op-first-1", owner_component="CAD-BRACKET")
    )
    out = await sp.propose_plane(
        twin,
        PROJECT,
        second,
        sp.PlaneProposalRequest(view_hint="front", owner_component="CAD-BRACKET"),
    )
    assert out.candidates[0].proposed_by is ProposedBy.EXISTING_SKETCH
    assert out.candidate.principal is PrincipalPlane.XZ
    assert first in out.candidates[0].reason
    # Another component's sketch does not count.
    other = await sp.propose_plane(
        twin, PROJECT, second, sp.PlaneProposalRequest(owner_component="CAD-OTHER")
    )
    assert other.candidates[0].proposed_by is ProposedBy.COMPONENT_FRAME


async def test_close_disagreeing_candidates_are_ambiguous(twin: InMemoryTwinAPI) -> None:
    first = await _sketch(twin, name="Plate")
    second = await _sketch(twin, _stroke("a", (0, 0), (0.1, 0.1)), name="Plate slot")
    await sp.confirm_plane(twin, PROJECT, first, _confirm("XY", "op-amb-0001"))
    # Existing sketch says Top (0.8), the view says Side (0.7): too close to call.
    out = await sp.propose_plane(twin, PROJECT, second, sp.PlaneProposalRequest(view_hint="side"))
    assert out.ambiguous is True
    # Agreeing sources are not ambiguous.
    agree = await sp.propose_plane(twin, PROJECT, second, sp.PlaneProposalRequest(view_hint="top"))
    assert agree.ambiguous is False


async def test_a_weak_heuristic_guess_alone_is_ambiguous(twin: InMemoryTwinAPI) -> None:
    third = await _sketch(twin, _stroke("b", (0, 0), (0.1, 0.1)), name="Doodle")
    weak = await sp.propose_plane(twin, PROJECT, third, sp.PlaneProposalRequest())
    assert weak.candidates[0].proposed_by is ProposedBy.MODEL and weak.ambiguous is True


def test_heuristic_reads_keywords_and_shape() -> None:
    tall = [_stroke("a", (0, -0.5), (0.1, 0.5))]
    out = sp.heuristic_candidates(tall, "")
    assert out[0].principal is PrincipalPlane.XZ and out[0].proposed_by is ProposedBy.MODEL
    words = sp.heuristic_candidates([], "footprint of the base")
    assert words[0].principal is PrincipalPlane.XY and words[0].confidence == pytest.approx(0.45)


async def test_proposing_writes_nothing(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    await sp.propose_plane(twin, PROJECT, key, sp.PlaneProposalRequest(view_hint="top"))
    assert await _plane_nodes(twin) == []


# ── confirming ──────────────────────────────────────────────────────────


async def test_confirm_makes_a_revision_pinned_to_the_sketch(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    out = await sp.confirm_plane(twin, PROJECT, key, _confirm("XZ", "op-confirm-1"))
    assert out.item_key == "PLN-SKETCH" and out.revision == 1
    assert out.plane.state is PlaneState.CONFIRMED
    assert out.plane.principal is PrincipalPlane.XZ
    assert out.plane.proposed_by is ProposedBy.USER and out.plane.confidence == 1.0
    assert out.plane.source_revision == f"{key}@1"
    assert out.pinned_sketch_ref == f"{key}@1" and out.can_undo

    sketch_item = await find_item(twin, key, UUID(PROJECT))
    assert sketch_item is not None and sketch_item.head_node_id is not None
    assert out.node_id is not None
    edges = await twin.graph.get_edges(
        UUID(out.node_id), direction="outgoing", edge_type=EdgeType.DEPENDS_ON
    )
    assert [(e.target_id, e.metadata["kind"], e.metadata["item_ref"]) for e in edges] == [
        (sketch_item.head_node_id, "sketch_plane", f"{key}@1")
    ]
    node = await twin.get_work_product(UUID(out.node_id))
    assert node is not None
    assert node.metadata["depends_on_items"][0]["item_ref"] == f"{key}@1"


async def test_explicit_choice_is_never_overridden_by_inference(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin, name="Front elevation")
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-explicit-1"))
    # The words and the view both say Front; the confirmed Top plane stands.
    out = await sp.propose_plane(
        twin, PROJECT, key, sp.PlaneProposalRequest(view_hint="front", hint_text="front view")
    )
    assert out.candidate.principal is PrincipalPlane.XY
    assert out.candidate.state is PlaneState.CONFIRMED
    assert out.current is not None and not out.ambiguous
    view = await sp.get_plane(twin, PROJECT, key)
    assert view.plane.principal is PrincipalPlane.XY and view.revision == 1


async def test_confirm_is_idempotent_and_a_same_choice_is_unchanged(
    twin: InMemoryTwinAPI,
) -> None:
    key = await _sketch(twin)
    first = await sp.confirm_plane(twin, PROJECT, key, _confirm("YZ", "op-same-0001"))
    again = await sp.confirm_plane(twin, PROJECT, key, _confirm("YZ", "op-same-0001"))
    assert again.duplicate and again.node_id == first.node_id
    same = await sp.confirm_plane(twin, PROJECT, key, _confirm("YZ", "op-same-0002"))
    assert same.unchanged and same.revision == 1
    assert len(await _plane_nodes(twin)) == 1


async def test_a_stale_base_revision_is_a_conflict(twin: InMemoryTwinAPI) -> None:
    from twin_core.items import ItemRevisionConflictError

    key = await _sketch(twin)
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-base-0001"))
    await sp.confirm_plane(
        twin, PROJECT, key, _confirm("XZ", "op-base-0002", base_revision="PLN-SKETCH@1")
    )
    with pytest.raises(ItemRevisionConflictError):
        await sp.confirm_plane(
            twin, PROJECT, key, _confirm("YZ", "op-base-0003", base_revision="PLN-SKETCH@1")
        )


async def test_a_sketch_saved_after_confirmation_records_its_plane(
    twin: InMemoryTwinAPI,
) -> None:
    key = await _sketch(twin)
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XZ", "op-later-001"))
    save = make_sketch_strokes_recorder(twin)
    out = await save(
        PROJECT,
        SketchSaveRequest(
            save_id="save-after-plane", item_key=key, strokes=[_stroke("s9", (0, 0))]
        ),
    )
    assert out.plane_state == "CONFIRMED"
    node = await twin.get_work_product(UUID(out.node_id))
    assert node is not None
    assert node.metadata["plane"] == {
        "state": "CONFIRMED",
        "plane_ref": "PLN-SKETCH@1",
        "principal": "XZ",
    }


# ── reassigning ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["preserve_local", "preserve_world", "project_to_target"])
async def test_reassign_preview_writes_nothing(twin: InMemoryTwinAPI, mode: str) -> None:
    key = await _sketch(twin, _stroke("s1", (0.1, -0.2), (0.3, 0.4)))
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-prev-0001"))
    before_planes = len(await _plane_nodes(twin))
    before_sketch = await current_sketch(twin, PROJECT, key)
    req = sp.PlaneReassignRequest(op_id="op-prev-0002", target="XZ", mode=mode)  # type: ignore[arg-type]
    preview = await sp.preview_reassign(twin, PROJECT, key, req)
    assert preview.mode == mode and preview.target.principal is PrincipalPlane.XZ
    pts = preview.strokes[0].points
    if mode == "preserve_local":
        assert pts == [(0.1, -0.2), (0.3, 0.4)] and preview.exact and preview.committable
        assert not preview.rewrites_strokes
    elif mode == "preserve_world":
        # Top -> Front: the sketch is not in the Front plane, so this is refused.
        assert not preview.exact and not preview.committable and preview.warnings
    else:
        assert pts == [(0.1, 0.0), (0.3, 0.0)] and preview.committable
        assert preview.max_off_plane == pytest.approx(0.4) and preview.rewrites_strokes
    assert len(await _plane_nodes(twin)) == before_planes
    after = await current_sketch(twin, PROJECT, key)
    assert after is not None and before_sketch is not None
    assert after.node_id == before_sketch.node_id


async def test_reassign_preserve_local_commits_a_new_plane_revision(
    twin: InMemoryTwinAPI,
) -> None:
    key = await _sketch(twin)
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-rl-00001"))
    out = await sp.reassign_plane(
        twin,
        PROJECT,
        key,
        sp.PlaneReassignRequest(op_id="op-rl-00002", target="YZ", mode="preserve_local"),
    )
    assert out.revision == 2 and out.parent_revision == "PLN-SKETCH@1"
    assert out.plane.principal is PrincipalPlane.YZ and out.mode == "preserve_local"
    assert out.sketch_revision_written is None
    sketch = await current_sketch(twin, PROJECT, key)
    assert sketch is not None and sketch.revision == 1


async def test_reassign_preserve_world_within_the_same_plane(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin, _stroke("s1", (0.0, 0.0), (0.5, 0.2)))
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-pw-00001"))
    out = await sp.reassign_plane(
        twin,
        PROJECT,
        key,
        sp.PlaneReassignRequest(
            op_id="op-pw-00002", target="XY", origin=(0.25, 0.0, 0.0), mode="preserve_world"
        ),
    )
    assert out.plane.origin == (0.25, 0.0, 0.0)
    assert out.sketch_revision_written == f"{key}@2"
    sketch = await current_sketch(twin, PROJECT, key)
    assert sketch is not None and sketch.strokes[0]["points"] == [[-0.25, 0.0], [0.25, 0.2]]
    with pytest.raises(sp.PlaneError, match="world position cannot be kept"):
        await sp.reassign_plane(
            twin,
            PROJECT,
            key,
            sp.PlaneReassignRequest(op_id="op-pw-00003", target="XZ", mode="preserve_world"),
        )


async def test_reassign_then_undo_restores_plane_and_strokes(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin, _stroke("s1", (0.1, -0.2), (0.3, 0.4)))
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-undo-0001"))
    moved = await sp.reassign_plane(
        twin,
        PROJECT,
        key,
        sp.PlaneReassignRequest(op_id="op-undo-0002", target="XZ", mode="project_to_target"),
    )
    assert moved.sketch_revision_written == f"{key}@2"
    projected = await current_sketch(twin, PROJECT, key)
    assert projected is not None
    assert projected.strokes[0]["points"] == [[0.1, 0.0], [0.3, 0.0]]

    undone = await sp.undo_plane(twin, PROJECT, key, sp.PlaneSimpleRequest(op_id="op-undo-0003"))
    assert undone.revision == 3 and undone.action == "undone"
    assert undone.plane.principal is PrincipalPlane.XY
    restored = await current_sketch(twin, PROJECT, key)
    assert restored is not None and restored.revision == 3
    assert restored.strokes[0]["points"] == [[0.1, -0.2], [0.3, 0.4]]

    # Undo again goes back to before the first assignment: unassigned.
    first = await sp.undo_plane(twin, PROJECT, key, sp.PlaneSimpleRequest(op_id="op-undo-0004"))
    assert first.plane.state is PlaneState.UNASSIGNED and not first.can_undo
    with pytest.raises(sp.PlaneError, match="nothing|no plane change"):
        await sp.undo_plane(twin, PROJECT, key, sp.PlaneSimpleRequest(op_id="op-undo-0005"))

    # History is kept: four immutable revisions.
    item = await find_item(twin, "PLN-SKETCH", UUID(PROJECT))
    assert item is not None
    assert [r.revision for r in await item_history(twin, item)] == [1, 2, 3, 4]


async def test_archive_then_undo(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    await sp.confirm_plane(twin, PROJECT, key, _confirm("YZ", "op-arch-0001"))
    archived = await sp.archive_plane(
        twin, PROJECT, key, sp.PlaneSimpleRequest(op_id="op-arch-0002")
    )
    assert archived.plane.state is PlaneState.ARCHIVED and archived.revision == 2
    back = await sp.undo_plane(twin, PROJECT, key, sp.PlaneSimpleRequest(op_id="op-arch-0003"))
    assert back.plane.state is PlaneState.CONFIRMED and back.plane.principal is PrincipalPlane.YZ


async def test_reassign_needs_a_placed_plane(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    req = sp.PlaneReassignRequest(op_id="op-none-0001", target="XY", mode="preserve_local")
    with pytest.raises(sp.PlaneError, match="before reassigning"):
        await sp.preview_reassign(twin, PROJECT, key, req)


# ── operation events ────────────────────────────────────────────────────


async def test_changes_report_sketch_operations(
    twin: InMemoryTwinAPI, publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    out = await sp.confirm_plane(twin, PROJECT, key, _confirm("XZ", "op-event-001"))
    snap = publisher.store.get(PROJECT, "job:sketch-plane-op-event-001")
    assert snap is not None and snap.state == "completed" and snap.domain == "sketch"
    assert snap.title == "Sketch placed on Front (XZ)"
    assert snap.target_revision == out.item_ref
    assert [r.id for r in snap.artifact_refs] == [out.node_id]

    with pytest.raises(sp.PlaneError):
        await sp.reassign_plane(
            twin,
            PROJECT,
            key,
            sp.PlaneReassignRequest(op_id="op-event-002", target="XY", mode="preserve_world"),
        )
    failed = publisher.store.get(PROJECT, "job:sketch-plane-op-event-002")
    assert failed is not None and failed.state == "failed" and failed.severity == "error"


# ── the camera never changes the plane ──────────────────────────────────


def test_no_plane_call_takes_a_camera() -> None:
    """The only client view input is a proposal hint; no write reads one."""
    for model in (
        sp.PlaneConfirmRequest,
        sp.PlaneReassignRequest,
        sp.PlaneSimpleRequest,
    ):
        names = set(model.model_fields)
        assert not names & {"view_hint", "camera", "camera_position", "view_direction"}
    assert "camera" not in inspect.signature(sp.get_plane).parameters
    assert set(sp.PlaneProposalRequest.model_fields) >= {"view_hint"}


async def test_a_view_hint_never_changes_a_stored_plane(twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    await sp.confirm_plane(twin, PROJECT, key, _confirm("XY", "op-cam-00001"))
    for hint in ("front", "side", "top", "front"):
        await sp.propose_plane(twin, PROJECT, key, sp.PlaneProposalRequest(view_hint=hint))  # type: ignore[arg-type]
        await sp.get_plane(twin, PROJECT, key)
    view = await sp.get_plane(twin, PROJECT, key)
    assert view.revision == 1 and view.plane.principal is PrincipalPlane.XY
    assert len(await _plane_nodes(twin)) == 1


# ── routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(
    twin: InMemoryTwinAPI,
    publisher: ops.OperationPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    from api_gateway.twin.sketch_routes import router as sketch_router

    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(sketch_router)
    app.include_router(plane_router)
    return TestClient(app)


def test_routes_round_trip(client: TestClient) -> None:
    saved = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches",
        json={"save_id": "route-plane-01", "strokes": [_stroke("s1", (0, 0), (0.2, 0.2))]},
    ).json()
    key = saved["item_key"]
    base = f"/v1/twin/projects/{PROJECT}/sketches/{key}/plane"

    got = client.get(base)
    assert got.status_code == 200 and got.json()["plane"]["state"] == "UNASSIGNED"

    proposal = client.post(f"{base}/propose", json={"view_hint": "front"})
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["candidate"]["principal"] == "XZ"

    confirmed = client.post(f"{base}/confirm", json={"op_id": "route-op-0001", "principal": "XZ"})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["plane"]["state"] == "CONFIRMED" and body["item_ref"] == "PLN-SKETCH@1"
    assert len(body["plane"]["transform"]) == 4

    preview = client.post(
        f"{base}/reassign/preview",
        json={"op_id": "route-op-0002", "target": "XY", "mode": "preserve_local"},
    )
    assert preview.status_code == 200 and preview.json()["committable"] is True
    assert client.get(base).json()["revision"] == 1

    refused = client.post(
        f"{base}/reassign",
        json={"op_id": "route-op-0003", "target": "XY", "mode": "preserve_world"},
    )
    assert refused.status_code == 409

    undone = client.post(f"{base}/undo", json={"op_id": "route-op-0004"})
    assert undone.status_code == 200 and undone.json()["plane"]["state"] == "UNASSIGNED"


def test_routes_refuse_bad_input(client: TestClient) -> None:
    assert client.get("/v1/twin/projects/not-a-uuid/sketches/SKT-X/plane").status_code == 400
    assert client.get(f"/v1/twin/projects/{PROJECT}/sketches/SKT-NOPE/plane").status_code == 404
    bad = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches/SKT-NOPE/plane/confirm",
        json={"op_id": "route-op-bad1", "principal": "AB"},
    )
    assert bad.status_code == 422
