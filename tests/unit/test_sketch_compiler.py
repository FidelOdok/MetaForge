"""The sketch-to-parametric compiler's records and routes (FORGE-791).

Covers the design IR extensions (and that existing IR still validates and
lowers), the deterministic constraint re-check, storing an interpretation as
a proposal that depends on its strokes revision, and promoting it to a
parametric sketch that is held for approval: refused for nothing accepted, a
failing accepted constraint or a stale sketch, idempotent per promote id,
linked to both sources, carrying a placed plane's ref and unit, and reported
as one sketch operation.
"""

from __future__ import annotations

import math
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
from api_gateway.twin.sketch_compiler_recorder import (
    InterpretationSaveRequest,
    PromoteRequest,
    SketchCompileError,
    approve_parametric_sketch,
    interpretation_item_key,
    promote_interpretation,
    save_interpretation,
)
from api_gateway.twin.sketch_compiler_routes import router as compiler_router
from api_gateway.twin.sketch_strokes_recorder import SketchSaveRequest, make_sketch_strokes_recorder
from domain_agents.shared.cadquery_lowering import LoweringError, lower_design_ir_cadquery
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.design_ir import DesignIR, SketchConstraint, SketchEntity, validate_design_ir
from twin_core.design_ir.sketch_check import check_accepted_constraints
from twin_core.design_ir.sketch_compiler import ParametricSketch, SketchInterpretation, promote
from twin_core.models.enums import EdgeType, WorkProductType

PROJECT = "11111111-2222-3333-4444-555555555555"


# ── fixtures ────────────────────────────────────────────────────────────


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
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


async def _sketch(
    twin: InMemoryTwinAPI, save_id: str = "save-0001", item_key: str | None = None
) -> str:
    save = make_sketch_strokes_recorder(twin)
    out = await save(
        PROJECT,
        SketchSaveRequest(
            save_id=save_id,
            item_key=item_key,
            strokes=[{"id": "box", "points": [[-0.3, -0.2], [0.3, -0.2], [0.3, 0.2], [-0.3, 0.2]]}],
        ),
    )
    assert out.item_key
    return out.item_key


def _line(pid: str, a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "line",
        "start": a,
        "end": b,
        "estimated": True,
        "confidence": 0.9,
        "residual": 0.001,
        "source_stroke_ids": ["box"],
        "source_ranges": [{"stroke_id": "box", "start": 0, "end": 10}],
    }


def _rule(
    cid: str, typ: str, refs: list[str], status: str = "accepted", **kw: Any
) -> dict[str, Any]:
    return {"id": cid, "type": typ, "refs": refs, "status": status, "source": "inferred", **kw}


def _interp(
    interp_id: str = "interp-0001",
    *,
    sketch_ref: str | None = None,
    crooked: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    y = 0.21 if crooked else 0.2
    prims = [
        _line("t", (-0.3, -0.2), (0.3, -0.2)),
        _line("r", (0.3, -0.2), (0.3, 0.2)),
        _line("b", (0.3, 0.2), (-0.3, y)),
        _line("l", (-0.3, y), (-0.3, -0.2)),
    ]
    rules = [
        _rule("c1", "coincident", ["t.end", "r.start"]),
        _rule("c2", "coincident", ["r.end", "b.start"]),
        _rule("c3", "coincident", ["b.end", "l.start"]),
        _rule("c4", "coincident", ["l.end", "t.start"]),
        _rule("h1", "horizontal", ["t"]),
        _rule("h2", "horizontal", ["b"]),
        _rule("v1", "vertical", ["r"]),
        _rule("p1", "parallel", ["t", "b"], status="proposed"),
        _rule(
            "len", "length", ["t"], status="proposed", source="estimated", value=0.6, unit="unknown"
        ),
    ]
    return {
        "interpretation_id": interp_id,
        "sketch_ref": sketch_ref,
        "primitives": prims,
        "constraints": rules,
        "profiles": [{"primitive_ids": ["t", "r", "b", "l"]}],
        "fit": {"extent": 0.72, "tolerance": 0.0072, "unit_px": 600, "stroke_count": 1},
        "solve": {"status": "under", "dof": 5, "shape_dof": 2, "converged": True},
        **kw,
    }


# ── design IR extensions and back-compat ───────────────────────────────


def test_existing_sketch_ir_still_validates_unchanged() -> None:
    doc = DesignIR(
        entities=[
            {"id": "b", "op": "create_body"},
            {
                "id": "s",
                "op": "sketch",
                "body_ref": "b",
                "elements": [
                    {"type": "rectangle", "origin": (0, 0), "width": 40, "height": 20},
                    {"type": "circle", "center": (5, 5), "radius": 2},
                    {"type": "line", "start": (0, 0), "end": (1, 1)},
                ],
            },
            {"id": "p", "op": "pad", "body_ref": "b", "sketch_ref": "s", "depth": 5},
        ]
    )
    assert validate_design_ir(doc) == []
    sketch = doc.entities[1]
    assert isinstance(sketch, SketchEntity)
    assert sketch.constraints == [] and sketch.plane_id is None
    assert all(el.id is None and el.estimated is False for el in sketch.elements)


def test_new_elements_and_constraints_validate() -> None:
    e = SketchEntity(
        id="s",
        body_ref="b",
        elements=[
            {
                "type": "ellipse",
                "id": "e",
                "center": (0, 0),
                "major_radius": 2,
                "minor_radius": 1,
                "rotation": 30,
            },
            {"type": "spline", "id": "k", "points": [(0, 0), (1, 2), (2, 0)], "closed": False},
        ],
        constraints=[{"id": "c", "type": "concentric", "refs": ["e", "e"]}],
    )
    assert e.elements[0].type == "ellipse" and e.elements[1].type == "spline"
    with pytest.raises(ValidationError, match="minor_radius"):
        SketchEntity(
            id="s",
            body_ref="b",
            elements=[{"type": "ellipse", "center": (0, 0), "major_radius": 1, "minor_radius": 2}],
        )
    with pytest.raises(ValidationError, match="takes 2 refs"):
        SketchConstraint(id="c", type="parallel", refs=["a"])
    with pytest.raises(ValidationError, match="positive value"):
        SketchConstraint(id="c", type="length", refs=["a"])
    with pytest.raises(ValidationError, match="only a dimension can be estimated"):
        SketchConstraint(id="c", type="horizontal", refs=["a"], source="estimated")


async def test_a_promoted_sketch_in_mm_lowers_and_skips_construction() -> None:
    interp = SketchInterpretation.model_validate(_interp())
    interp.primitives.append(
        SketchInterpretation.model_validate(
            {
                **_interp(),
                "primitives": [
                    {
                        **_line("axis-v", (0, -0.3), (0, 0.3)),
                        "construction": True,
                        "source_stroke_ids": [],
                    }
                ],
                "constraints": [],
                "profiles": [],
            }
        ).primitives[0]
    )
    parametric = promote(interp, interpretation_ref="SKI-SKETCH@1")
    with pytest.raises(ValueError, match="millimetres"):
        parametric.to_sketch_entity("s", "b")
    mm = parametric.model_copy(update={"unit": "mm"})
    entity = mm.to_sketch_entity("s", "b", plane="XZ", offset=3.0)
    # Sketch space has y down; on the plane v is up.
    assert entity.elements[0].start == (-0.3, 0.2)
    assert entity.plane == "XZ" and entity.offset == 3.0
    doc = DesignIR(
        entities=[
            {"id": "b", "op": "create_body"},
            entity.model_dump(),
            {"id": "p", "op": "pad", "body_ref": "b", "sketch_ref": "s", "depth": 5},
        ]
    )
    mcp = InMemoryMcpBridge()
    mcp.register_tool("cadquery.execute_script", capability="cad_script")
    mcp.register_tool_response(
        "cadquery.execute_script",
        {"step_base64": "SVNP", "volume_mm3": 1.0, "surface_area_mm2": 1.0, "bounding_box": {}},
    )
    result = await lower_design_ir_cadquery(mcp, doc)
    assert result.terminal_entity_id == "p"
    # The four profile lines lower; the construction axis does not.
    assert result.script_text.count("lineTo") == 4


async def test_cadquery_refuses_an_ellipse_with_a_clear_reason() -> None:
    mcp = InMemoryMcpBridge()
    mcp.register_tool("cadquery.execute_script", capability="cad_script")
    doc = DesignIR(
        entities=[
            {"id": "b", "op": "create_body"},
            {
                "id": "s",
                "op": "sketch",
                "body_ref": "b",
                "elements": [
                    {"type": "ellipse", "center": (0, 0), "major_radius": 2, "minor_radius": 1}
                ],
            },
            {"id": "p", "op": "pad", "body_ref": "b", "sketch_ref": "s", "depth": 5},
        ]
    )
    with pytest.raises(LoweringError, match="ellipse"):
        await lower_design_ir_cadquery(mcp, doc)


def test_arcs_mirror_into_plane_space() -> None:
    interp = SketchInterpretation.model_validate(
        {
            **_interp(),
            "primitives": [
                {
                    "id": "a",
                    "type": "arc",
                    "center": (1, 1),
                    "radius": 1,
                    "start_angle": 0,
                    "end_angle": 90,
                    "estimated": True,
                    "source_stroke_ids": ["s"],
                }
            ],
            "constraints": [],
            "profiles": [],
            "unit": "mm",
        }
    )
    entity = promote(interp, interpretation_ref=None).to_sketch_entity("s", "b")
    arc = entity.elements[0]
    assert arc.center == (1, -1)
    assert (arc.start_angle, arc.end_angle) == (270, 0)


# ── deterministic re-check ──────────────────────────────────────────────


def test_check_accepts_a_square_box_and_names_what_fails_on_a_crooked_one() -> None:
    good = SketchInterpretation.model_validate(_interp())
    assert all(c.holds for c in check_accepted_constraints(good.primitives, good.constraints))
    bad = SketchInterpretation.model_validate(_interp(crooked=True))
    failing = [
        c.constraint_id
        for c in check_accepted_constraints(bad.primitives, bad.constraints)
        if not c.holds
    ]
    assert failing == ["h2"]


def test_check_reports_a_ref_to_the_wrong_kind_of_element() -> None:
    interp = SketchInterpretation.model_validate(_interp())
    wrong = SketchConstraint(
        id="x", type="radius", refs=["t"], value=1.0, status="accepted", source="human_confirmed"
    )
    out = check_accepted_constraints(interp.primitives, [wrong])
    assert not out[0].holds and "not a circle or arc" in out[0].reason
    assert math.isinf(out[0].residual)


def test_interpretation_refs_must_resolve() -> None:
    raw = _interp()
    raw["constraints"].append(_rule("zz", "horizontal", ["nope"]))
    with pytest.raises(ValidationError, match="refers to nope"):
        SketchInterpretation.model_validate(raw)
    raw = _interp()
    raw["constraints"].append(_rule("zz", "coincident", ["t.center", "r.start"]))
    with pytest.raises(ValidationError, match="not a point of a line"):
        SketchInterpretation.model_validate(raw)
    raw = _interp()
    raw["primitives"][0]["estimated"] = False
    with pytest.raises(ValidationError, match="must be marked estimated"):
        SketchInterpretation.model_validate(raw)


# ── interpretation ──────────────────────────────────────────────────────


async def test_an_interpretation_is_a_proposal_that_depends_on_its_strokes(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    out = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp())
    )
    assert out.item_ref == f"{interpretation_item_key(key)}@1"
    assert out.sketch_ref == f"{key}@1"
    assert out.authority == "proposal" and out.unit == "unknown" and out.plane_id is None
    assert out.accepted_count == 7 and out.unsatisfied == []
    node = await twin.get_work_product(UUID(out.node_id))
    assert node is not None and node.type == WorkProductType.SKETCH_INTERPRETATION
    assert node.metadata["estimated"] is True
    edges = await twin.get_edges(UUID(out.node_id), direction="outgoing")
    deps = [e for e in edges if e.edge_type == EdgeType.DEPENDS_ON]
    assert len(deps) == 1 and deps[0].metadata["item_ref"] == f"{key}@1"
    # The SVG is generated here.
    assert any(k.endswith("sketch.svg") for k in blobs)

    snap = publisher.store.get(PROJECT, out.operation_id)
    assert snap is not None and snap.domain == "sketch" and snap.state == "waiting_for_user"
    assert snap.stages[0].stage_id == "interpretation_proposed"
    assert [r.id for r in snap.artifact_refs] == [out.node_id]

    again = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp())
    )
    assert again.duplicate and again.node_id == out.node_id


async def test_an_interpretation_of_an_old_sketch_revision_is_refused(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    await _sketch(twin, "save-0002", key)
    with pytest.raises(SketchCompileError, match="make it editable again") as err:
        await save_interpretation(
            twin,
            PROJECT,
            key,
            InterpretationSaveRequest(interpretation=_interp(sketch_ref=f"{key}@1")),
        )
    assert err.value.status == 409


async def test_a_degenerate_line_is_refused(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    raw = _interp()
    raw["primitives"][0]["end"] = raw["primitives"][0]["start"]
    with pytest.raises(SketchCompileError, match="no length") as err:
        await save_interpretation(twin, PROJECT, key, InterpretationSaveRequest(interpretation=raw))
    assert err.value.status == 422


async def test_a_placed_plane_carries_its_ref_and_unit(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    await sp.confirm_plane(
        twin, PROJECT, key, sp.PlaneConfirmRequest(op_id="op-plane-0001", principal="XY", unit="mm")
    )
    # The payload's plane id and unit are never used over the plane item's.
    out = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp(plane_id="PLN-FAKE"))
    )
    assert out.plane_id is not None and out.plane_id.startswith("PLN-")
    assert out.plane_id != "PLN-FAKE" and out.unit == "mm"
    promoted = await promote_interpretation(
        twin, PROJECT, out.item_ref or "", PromoteRequest(promote_id="promote-plane1")
    )
    assert promoted.plane_id == out.plane_id and promoted.unit == "mm"


async def test_without_a_plane_the_payload_plane_id_is_carried_and_the_unit_stays_unknown(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    out = await save_interpretation(
        twin,
        PROJECT,
        key,
        InterpretationSaveRequest(interpretation=_interp(plane_id="PLN-DRAFT", unit="mm")),
    )
    assert out.plane_id == "PLN-DRAFT" and out.unit == "unknown"


# ── promotion ───────────────────────────────────────────────────────────


async def test_promotion_keeps_accepted_rules_links_both_and_waits_for_approval(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    saved = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp())
    )
    out = await promote_interpretation(
        twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-0001")
    )
    assert out.item_ref and out.item_ref.startswith("PSK-")
    assert out.interpretation_ref == saved.item_ref
    assert out.accepted_count == 7 and out.dropped_count == 2
    assert out.approved is False and out.approval_id == f"parametric_sketch:{out.node_id}"
    # SKP-005: nothing invented. Depth and material are required input.
    extrude = out.feature_plan[0]
    assert extrude.op == "extrude"
    assert (
        extrude.parameters["depth"].status == "required_input"
        and extrude.parameters["depth"].value is None
    )
    assert extrude.parameters["material"].status == "required_input"

    node = await twin.get_work_product(UUID(out.node_id))
    assert node is not None and node.type == WorkProductType.PARAMETRIC_SKETCH
    content = ParametricSketch.model_validate(
        __import__("json").loads(blobs[node.metadata["sketch_files"]["parametric_sketch.json"]])
    )
    assert {c.id for c in content.constraints} == {"c1", "c2", "c3", "c4", "h1", "h2", "v1"}
    edges = await twin.get_edges(UUID(out.node_id), direction="outgoing")
    deps = sorted(e.metadata["item_ref"] for e in edges if e.edge_type == EdgeType.DEPENDS_ON)
    assert deps == sorted([saved.item_ref, f"{key}@1"])

    snap = publisher.store.get(PROJECT, saved.operation_id)
    assert snap is not None and snap.state == "waiting_for_user" and snap.requires_action
    assert snap.action_refs[0].id == out.approval_id

    # Idempotent per promote id.
    again = await promote_interpretation(
        twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-0001")
    )
    assert again.duplicate and again.node_id == out.node_id

    approved = await approve_parametric_sketch(twin, UUID(out.node_id), "person")
    assert approved["approved"] is True
    snap = publisher.store.get(PROJECT, saved.operation_id)
    assert snap is not None and snap.state == "completed"
    events = [
        str(e.type)
        for e in publisher.store.events_after(PROJECT, 0)
        if e.operation_id == saved.operation_id
    ]
    assert events == ["preview_ready", "review_required", "completed"]
    with pytest.raises(SketchCompileError, match="already approved"):
        await approve_parametric_sketch(twin, UUID(out.node_id), "person")


async def test_promotion_refuses_nothing_accepted(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    raw = _interp()
    for c in raw["constraints"]:
        c["status"] = "proposed"
    saved = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=raw)
    )
    with pytest.raises(SketchCompileError, match="nothing is accepted") as err:
        await promote_interpretation(
            twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-0002")
        )
    assert err.value.status == 409


async def test_promotion_refuses_accepted_rules_that_do_not_hold(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    saved = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp(crooked=True))
    )
    assert saved.unsatisfied == ["h2"]
    with pytest.raises(SketchCompileError) as err:
        await promote_interpretation(
            twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-0003")
        )
    assert err.value.status == 409
    assert err.value.detail["failing"][0]["id"] == "h2"
    assert await twin.list_work_products(work_product_type=WorkProductType.PARAMETRIC_SKETCH) == []


async def test_promotion_refuses_when_the_sketch_moved_on(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], publisher: ops.OperationPublisher
) -> None:
    key = await _sketch(twin)
    saved = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp())
    )
    await _sketch(twin, "save-0003", key)
    with pytest.raises(SketchCompileError, match="make it editable again"):
        await promote_interpretation(
            twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-0004")
        )


# ── routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(compiler_router)
    return TestClient(app)


async def test_routes_store_promote_and_approve(client: TestClient, twin: InMemoryTwinAPI) -> None:
    key = await _sketch(twin)
    res = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches/{key}/interpretations",
        json={"interpretation": _interp()},
    )
    assert res.status_code == 200, res.text
    saved = res.json()
    bad = client.post(
        f"/v1/twin/projects/{PROJECT}/interpretations/{saved['item_key']}/promote",
        json={"promote_id": "x"},
    )
    assert bad.status_code == 422
    res = client.post(
        f"/v1/twin/projects/{PROJECT}/interpretations/{saved['item_ref']}/promote",
        json={"promote_id": "route-promote-1"},
    )
    assert res.status_code == 200, res.text
    promoted = res.json()
    assert promoted["approved"] is False
    res = client.post(
        f"/v1/twin/nodes/{promoted['node_id']}/approve-parametric-sketch",
        json={"approved_by": "me"},
    )
    assert res.status_code == 200 and res.json()["approved"] is True
    again = client.post(f"/v1/twin/nodes/{promoted['node_id']}/approve-parametric-sketch", json={})
    assert again.status_code == 409
    missing = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches/SKT-NOPE/interpretations",
        json={"interpretation": _interp("interp-9999")},
    )
    assert missing.status_code == 404
    crooked = client.post(
        f"/v1/twin/projects/{PROJECT}/sketches/{key}/interpretations",
        json={"interpretation": _interp("interp-crooked", crooked=True)},
    )
    refused = client.post(
        f"/v1/twin/projects/{PROJECT}/interpretations/{crooked.json()['item_ref']}/promote",
        json={"promote_id": "route-promote-2"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["failing"][0]["id"] == "h2"


async def test_parametric_sketch_is_listed_and_decided_through_approvals(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.approvals import service

    monkeypatch.setattr(twin_routes, "_twin", twin)
    monkeypatch.setattr(service, "_twin_of", lambda _app: twin)
    key = await _sketch(twin)
    saved = await save_interpretation(
        twin, PROJECT, key, InterpretationSaveRequest(interpretation=_interp())
    )
    out = await promote_interpretation(
        twin, PROJECT, saved.item_ref or "", PromoteRequest(promote_id="promote-appr1")
    )
    item = await service.get_item(None, out.approval_id)
    assert item.kind == "parametric_sketch" and item.status == "pending"
    assert item.allowed_decisions == ["approve"]
    assert item.kind in service.HUMAN_AUTHORITY_KINDS
