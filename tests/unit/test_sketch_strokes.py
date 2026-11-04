"""Freehand sketches from the Create stage saved as twin revisions (FORGE-789).

The recorder makes each save the next revision of one ``sketch_strokes`` item,
with lineage to the revision it was drawn on, and returns the revision it
already made for a retried save id. Every revision says it is a human sketch
with an unknown scale and an unassigned plane. The route reports each save as
a ``sketch`` operation: accepted, then completed with the new revision as an
artifact.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api_gateway.operations import publisher as ops
from api_gateway.twin import routes as twin_routes
from api_gateway.twin import sketch_strokes_recorder as rec
from api_gateway.twin.sketch_routes import router as sketch_router
from api_gateway.twin.sketch_strokes_recorder import (
    SketchSaveRequest,
    make_sketch_strokes_recorder,
    render_svg,
    save_sketch_with_operation,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType

PROJECT = "11111111-2222-3333-4444-555555555555"


def _stroke(sid: str, *points: tuple[float, float], **kw: Any) -> dict[str, Any]:
    return {"id": sid, "tool": "pen", "points": [list(p) for p in points], **kw}


def _request(save_id: str, *strokes: dict[str, Any], **kw: Any) -> SketchSaveRequest:
    return SketchSaveRequest(save_id=save_id, strokes=list(strokes), **kw)


@pytest.fixture
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
def no_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("storage down")

    import digital_twin.storage.work_product_blobs as wpb

    monkeypatch.setattr(wpb, "store_work_product_blob", boom)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


# ── recorder ────────────────────────────────────────────────────────────


async def test_each_save_is_a_new_revision_of_the_same_item(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    save = make_sketch_strokes_recorder(twin)
    first = await save(PROJECT, _request("save-0001", _stroke("s1", (0, 0), (0.1, 0.2))))
    second = await save(
        PROJECT,
        _request(
            "save-0002",
            _stroke("s1", (0, 0), (0.1, 0.2)),
            _stroke("s2", (0.3, 0.3), (0.4, 0.1)),
            item_key=first.item_key,
            base_revision=first.item_ref,
        ),
    )
    assert first.item_key == second.item_key
    assert first.item_key is not None and first.item_key.startswith("SKT-")
    assert (first.revision, second.revision) == (1, 2)
    assert first.parent_revision is None
    assert second.parent_revision == f"{first.item_key}@1"
    assert second.node_id != first.node_id
    assert second.stroke_count == 2

    node = await twin.get_work_product(UUID(second.node_id))
    assert node is not None
    assert node.type == WorkProductType.SKETCH_STROKES
    assert node.format == "svg"
    meta = node.metadata
    assert meta["parent_node_id"] == first.node_id
    assert meta["client_base_revision"] == first.item_ref
    assert meta["item_key"] == first.item_key and meta["item_revision"] == 2


async def test_without_an_item_key_a_save_still_lands_on_the_same_item(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    save = make_sketch_strokes_recorder(twin)
    a = await save(PROJECT, _request("save-a-0001", _stroke("s1", (0, 0))))
    b = await save(PROJECT, _request("save-b-0001", _stroke("s1", (0, 0))))
    assert a.item_key == b.item_key and b.revision == 2


async def test_a_retried_save_id_returns_the_revision_it_made(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    save = make_sketch_strokes_recorder(twin)
    request = _request("save-same-1", _stroke("s1", (0, 0), (0.2, 0.2)))
    first = await save(PROJECT, request)
    again = await save(PROJECT, request)
    assert again.duplicate is True and first.duplicate is False
    assert again.node_id == first.node_id and again.revision == first.revision
    nodes = await twin.list_work_products(
        work_product_type=WorkProductType.SKETCH_STROKES,
        project_id=UUID(PROJECT),
    )
    assert len(nodes) == 1


async def test_every_revision_is_a_human_sketch_with_no_plane_or_scale(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    save = make_sketch_strokes_recorder(twin)
    out = await save(
        PROJECT,
        _request(
            "save-honest-1",
            _stroke("s1", (0, 0), (0.1, 0.1), pressure=[0.2, 0.9]),
            viewport={"width_px": 800, "height_px": 600},
        ),
    )
    assert (out.authority, out.scale, out.plane_state) == ("human_sketch", "unknown", "UNASSIGNED")
    node = await twin.get_work_product(UUID(out.node_id))
    assert node is not None
    meta = node.metadata
    assert meta["authority"] == "human_sketch"
    assert meta["scale"] == "unknown"
    assert meta["plane_state"] == "UNASSIGNED" and meta["plane"] == {"state": "UNASSIGNED"}
    assert meta["source"] == "human"
    assert meta["viewport"] == {"width_px": 800.0, "height_px": 600.0}
    # The SVG is the primary blob, the strokes a named file.
    svg = blobs[meta["minio_object_key"]].decode()
    assert svg.startswith("<svg") and 'data-stroke="s1"' in svg
    assert "<script" not in svg
    assert "strokes.json" in meta["sketch_files"]


async def test_honesty_fields_cannot_be_overridden_by_the_payload() -> None:
    with pytest.raises(ValidationError):
        SketchSaveRequest(save_id="save-bad-1", plane_state="CONFIRMED")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SketchSaveRequest(save_id="save-bad-2", scale="1:1")  # type: ignore[arg-type]


async def test_payload_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        _request("save-dup-ids", _stroke("s1", (0, 0)), _stroke("s1", (1, 1)))
    with pytest.raises(ValidationError):
        _request("save-nan-1", _stroke("s1", (float("nan"), 0)))
    with pytest.raises(ValidationError):
        _request("save-pres-1", _stroke("s1", (0, 0), (1, 1), pressure=[0.5]))
    with pytest.raises(ValidationError):
        SketchSaveRequest(save_id="short")


async def test_strokes_stay_on_the_node_when_the_blob_store_is_down(
    twin: InMemoryTwinAPI, no_blobs: None
) -> None:
    save = make_sketch_strokes_recorder(twin)
    out = await save(PROJECT, _request("save-down-1", _stroke("s1", (0, 0), (0.5, 0.5))))
    current = await rec.current_sketch(twin, PROJECT)
    assert current is not None and current.node_id == out.node_id
    assert current.strokes[0]["id"] == "s1"
    assert current.strokes[0]["points"] == [[0.0, 0.0], [0.5, 0.5]]


async def test_current_sketch_reads_the_head_with_its_strokes(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    save = make_sketch_strokes_recorder(twin)
    await save(PROJECT, _request("save-c-0001", _stroke("s1", (0, 0))))
    second = await save(PROJECT, _request("save-c-0002", _stroke("s2", (0.1, 0.1), (0.2, 0.3))))
    current = await rec.current_sketch(twin, PROJECT)
    assert current is not None
    assert current.node_id == second.node_id and current.revision == 2
    assert [s["id"] for s in current.strokes] == ["s2"]
    assert current.plane_state == "UNASSIGNED" and current.space == "sketch_unit"


def test_svg_is_deterministic_and_frames_the_strokes() -> None:
    strokes = _request("save-svg-01", _stroke("a", (-2, -1), (2, 1)), _stroke("b", (0, 0))).strokes
    one, two = render_svg(strokes), render_svg(strokes)
    assert one == two
    assert 'viewBox="-2.2 -1.2 4.4 2.4"' in one
    assert "<circle" in one  # a single-point stroke is a dot
    empty = render_svg([])
    assert "<path" not in empty and "viewBox" in empty


# ── operation events ────────────────────────────────────────────────────


async def test_a_save_reports_accepted_then_completed_with_the_revision(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    save = make_sketch_strokes_recorder(twin)
    request = _request("save-op-0001", _stroke("s1", (0, 0), (0.3, 0.1)))
    out = await save_sketch_with_operation(save, PROJECT, request)
    snap = publisher.store.get(PROJECT, "job:sketch-save-op-0001")
    assert snap is not None
    assert snap.state == "completed" and snap.domain == "sketch"
    assert snap.title == "Sketch saved"
    assert snap.target_revision == out.item_ref
    assert [r.id for r in snap.artifact_refs] == [out.node_id]
    assert snap.artifact_refs[0].kind == "work_product"
    events = publisher.store.events_after(PROJECT, 0)
    assert [str(e.type) for e in events] == ["accepted", "completed"]

    # A retry of the same save id adds no events and no revision.
    again = await save_sketch_with_operation(save, PROJECT, request)
    assert again.duplicate and again.node_id == out.node_id
    snap2 = publisher.store.get(PROJECT, "job:sketch-save-op-0001")
    assert snap2 is not None and snap2.last_sequence == snap.last_sequence


async def test_a_failed_save_reports_failed(
    publisher: ops.OperationPublisher,
) -> None:
    async def broken(_pid: str, _req: SketchSaveRequest) -> Any:
        raise RuntimeError("twin down")

    with pytest.raises(RuntimeError):
        await save_sketch_with_operation(broken, PROJECT, _request("save-fail-01"))
    snap = publisher.store.get(PROJECT, "job:sketch-save-fail-01")
    assert snap is not None and snap.state == "failed" and snap.severity == "error"


# ── route ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(sketch_router)
    return TestClient(app)


def test_route_saves_and_reads_back(client: TestClient, publisher: ops.OperationPublisher) -> None:
    body = {
        "save_id": "route-save-01",
        "strokes": [_stroke("s1", (0, 0), (0.25, 0.5))],
        "viewport": {"width_px": 640, "height_px": 480},
    }
    res = client.post(f"/v1/twin/projects/{PROJECT}/sketches", json=body)
    assert res.status_code == 200, res.text
    saved = res.json()
    assert saved["revision"] == 1 and saved["plane_state"] == "UNASSIGNED"
    assert saved["authority"] == "human_sketch" and saved["scale"] == "unknown"
    assert saved["operation_id"] == "job:sketch-route-save-01"

    again = client.post(f"/v1/twin/projects/{PROJECT}/sketches", json=body)
    assert again.status_code == 200 and again.json()["duplicate"] is True

    current = client.get(f"/v1/twin/projects/{PROJECT}/sketches/current")
    assert current.status_code == 200
    data = current.json()
    assert data["node_id"] == saved["node_id"]
    assert data["strokes"][0]["points"] == [[0.0, 0.0], [0.25, 0.5]]

    snap = publisher.store.get(PROJECT, "job:sketch-route-save-01")
    assert snap is not None and snap.state == "completed"


def test_route_refuses_bad_input(client: TestClient) -> None:
    assert client.get("/v1/twin/projects/not-a-uuid/sketches/current").status_code == 400
    assert client.get(f"/v1/twin/projects/{PROJECT}/sketches/current").status_code == 404
    bad = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches",
        json={"save_id": "route-bad-01", "plane_state": "ATTACHED"},
    )
    assert bad.status_code == 422
    unknown = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches",
        json={"save_id": "route-bad-02", "item_key": "SKT-NOPE@4"},
    )
    assert unknown.status_code == 409
