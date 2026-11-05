"""DFM finding sets in the twin: evaluate, store, recompute, decide (C3D slice 4).

Against the in-memory twin: a finding set per component pinned to the
geometry and manufacturing context revisions it read; the outputs it was
computed on carry its truthful status; a wall change stales and recomputes
only that component (3D-RC-014); a context change no rule reads leaves the
findings valid, a process change recomputes them under the new profile and
leaves the old process's findings stale without touching what a person stated
(3D-RC-013); findings appear in the impact walk (DFM-012); a fix that trades
away a protected requirement goes to approval (DFM-015); the
manufacturability variant records the rules that shaped it (DFM-003, DFM-005);
and the API and MCP tools.

Every provider here is a fake: nothing calls a paid API.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.dfm_routes import router as dfm_router
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import record_staleness, unregister_head_moved_listener
from twin_core.items import commit_revision, plan_revision
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

pytestmark = pytest.mark.asyncio

PROJECT = "5d1c0a52-1b5e-4b7a-9a43-3f1f2b9c0d05"
PID = UUID(PROJECT)

ASSEMBLY: dict[str, Any] = {
    "parts": [{"name": "Enclosure"}, {"name": "Lid"}],
    "connections": [{"parts": ["Enclosure", "Lid"], "joint": "snap fit"}],
}

#: Words no DFM note may use from heuristics.
FORBIDDEN = ("verified", "production-ready", "analysis passed", "released")


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def listener() -> Iterator[None]:
    dfm.register()
    yield
    unregister_head_moved_listener(dfm.on_head_moved)


@pytest.fixture(autouse=True)
def machine_library() -> Iterator[SqliteMachineLibrary]:
    library = SqliteMachineLibrary()
    library_routes.init_machine_library(library)
    yield library
    library_routes.init_machine_library(None)


def _features(
    wall: float, *, hole: float = 3.0, dims: tuple[float, ...] = (80, 60, 40)
) -> dict[str, Any]:
    return {
        "geometry_features": {
            "parameters": {"wall_thickness": wall, "hole_diameter": hole},
            "properties": {
                "bounding_box": {
                    "min_x": 0,
                    "min_y": 0,
                    "min_z": 0,
                    "max_x": dims[0],
                    "max_y": dims[1],
                    "max_z": dims[2],
                }
            },
        },
        "geometry_state": "validated",
    }


async def _cad(
    twin: Any, name: str, meta: dict[str, Any], *, key: str | None = None
) -> tuple[str, int, UUID]:
    """A CAD revision (the item's next one when ``key`` names an existing item)."""
    plan = await plan_revision(
        twin, item_type="cad_model", name=name, project_id=PID, author="test", item_key=key
    )
    assert plan is not None
    wp = WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash=hashlib.sha256(f"{name}{uuid4()}".encode()).hexdigest(),
        format="step",
        metadata={**meta, **plan.stamp()},
        created_by="test",
        project_id=PID,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key, plan.revision, wp.id


async def _project(twin: Any, *, wall: float = 1.6, process: str | None = "FDM") -> dict[str, str]:
    """Two components with exact CAD, and an FDM context a person stated."""
    await make_decision_recorder(twin)(
        title="Concept A", rationale="chosen", assembly=ASSEMBLY, project_id=PROJECT
    )
    enclosure, _, _ = await _cad(twin, "Enclosure", _features(wall))
    lid, _, _ = await _cad(twin, "Lid", _features(2.0, dims=(80, 60, 3)))
    if process:
        await mc.write_context(
            twin,
            PROJECT,
            {
                "selected_process": mc.stated(process, source="api"),
                "selected_material": mc.stated("PETG", source="api"),
                "finish": mc.stated("matte", source="api"),
            },
            source="api",
        )
    return {"enclosure": enclosure, "lid": lid}


async def _sets(twin: Any) -> list[Any]:
    return await twin.list_work_products(
        work_product_type=WorkProductType.DFM_FINDING_SET, project_id=PID
    )


async def _current(twin: Any) -> dict[str, Any]:
    view = await dfm.dfm_view(twin, PROJECT)
    return {c.component_id: c for c in view.components}


# ── evaluate and store ──────────────────────────────────────────────────


async def test_a_finding_set_per_component_pinned_to_geometry_and_context(
    twin: InMemoryTwinAPI,
) -> None:
    keys = await _project(twin, wall=0.5)
    out = await dfm.evaluate_project(twin, PROJECT)
    assert sorted(g.component_id for g, *_ in out) == ["CMP-enclosure", "CMP-lid"]
    assert all(created for *_, created in out)

    current = await _current(twin)
    enclosure = current["CMP-enclosure"]
    assert enclosure.status == "issues_found"
    assert enclosure.profile == {"id": "fdm", "version": "1.1.0"}
    assert enclosure.geometry_level == "exact"
    assert enclosure.context_ref == "MFC-PROJECT@1"
    [wall] = [f for f in enclosure.findings if f["rule_id"] == "fdm.min_wall"]
    assert wall["anchor"] == {
        "component_id": "CMP-enclosure",
        "feature": "wall_thickness",
        "face_signature": None,
        "faces": [],
    }
    assert (
        wall["measured"] == 0.5
        and wall["limit"] == 0.8
        and wall["confidence"] == "design_parameter"
    )
    assert wall["evidence_refs"] == [f"{keys['enclosure']}@1"]
    # Pinned to the geometry revision and the context revision it read.
    node = next(n for n in await _sets(twin) if n.metadata["component_id"] == "CMP-enclosure")
    refs = {p["item_ref"] for p in node.metadata["depends_on_items"]}
    assert refs == {f"{keys['enclosure']}@1", "MFC-PROJECT@1"}
    assert node.metadata["result"] == {"verdict": "incomplete", "heuristic_only": True}

    lid = current["CMP-lid"]
    assert lid.status == "preliminary_guidance" and lid.findings == []
    # The build volume is not assessed: no machine is selected.
    assert any(n["rule_id"] == "fdm.build_volume" for n in lid.not_assessed)


async def test_outputs_carry_the_truthful_status(twin: InMemoryTwinAPI) -> None:
    await _project(twin, wall=0.5)
    out = await dfm.evaluate_project(twin, PROJECT)
    for g, node, fs, _ in out:
        cad = await twin.get_work_product(UUID(g.output_nodes[0]))
        assert cad is not None
        assert cad.metadata["dfm_status"] == fs.status.value
        assert cad.metadata["manufacturing_note"] == fs.note
        assert cad.metadata["dfm_finding_set_id"] == str(node.id)
        assert not any(w in fs.note.lower() for w in FORBIDDEN)
    view = await dfm.dfm_view(twin, PROJECT)
    assert view.status == "issues_found" and view.suggestions == 1 and view.blocking == 0
    assert view.profiles == [{"id": "fdm", "version": "1.1.0", "process": "FDM"}]


async def test_an_unknown_process_assesses_nothing_and_tags_nothing(twin: InMemoryTwinAPI) -> None:
    await _project(twin, process=None)
    out = await dfm.evaluate_project(twin, PROJECT)
    for g, _, fs, _ in out:
        assert fs.status.value == "not_assessed"
        assert fs.not_assessed[0].reason == "the process is unknown"
        cad = await twin.get_work_product(UUID(g.output_nodes[0]))
        assert "dfm_status" not in (cad.metadata or {})
    view = await dfm.dfm_view(twin, PROJECT)
    assert view.status == "not_assessed" and view.suggestions == 0


async def test_the_same_inputs_reuse_the_current_set(twin: InMemoryTwinAPI) -> None:
    await _project(twin)
    await dfm.evaluate_project(twin, PROJECT)
    again = await dfm.evaluate_project(twin, PROJECT)
    assert not any(created for *_, created in again)
    assert len(await _sets(twin)) == 2


async def test_a_selected_machine_brings_its_build_volume(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    machine_library.register(
        {
            "layer": "instance",
            "kind": "fdm_printer",
            "name": "Workshop Mini",
            "processes": ["fdm"],
            "capabilities": {
                "build_volume_mm": {
                    "value": [70, 70, 70],
                    "source_kind": "manufacturer_spec",
                    "source_ref": "https://example.com/mini",
                }
            },
        }
    )
    await _project(twin)
    await mc.write_context(
        twin,
        PROJECT,
        {"machine_or_supplier_profile": mc.stated({"machine": "Workshop Mini"}, source="api")},
        source="api",
    )
    await dfm.evaluate_project(twin, PROJECT)
    enclosure = (await _current(twin))["CMP-enclosure"]
    [finding] = [f for f in enclosure.findings if f["rule_id"] == "fdm.build_volume"]
    assert finding["severity"] == "blocking" and finding["rule_kind"] == "machine_limit"
    assert (
        "Workshop Mini build_volume_mm (manufacturer_spec: https://example.com/mini)"
        in (finding["limit_source"])
    )
    assert "blocking" in enclosure.note


async def test_the_concept_layout_gives_estimated_l1_measures(twin: InMemoryTwinAPI) -> None:
    from api_gateway.twin import structured_mesh

    written: dict[str, bytes] = {}

    def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        written[node_id] = content
        return f"work-products/{node_id}/{filename}"

    structured_mesh.set_blob_writer(write)
    try:
        layout = {
            "concept_name": "Box",
            "parts": [
                {"name": "Enclosure", "size_mm": [80, 60, 40], "provenance": "intended"},
                {"name": "Lid", "size_mm": [80, 60, 0.5], "position_mm": [0, 0, 40],
                 "provenance": "assumption"},
            ],
        }  # fmt: skip
        sketch = WorkProduct(
            name="Concept sketch: Box",
            type=WorkProductType.DESIGN_SKETCH,
            domain="mechanical",
            file_path="",
            content_hash=uuid4().hex,
            format="svg",
            metadata={"concept_layout": layout, "source_tool": "twin.render_concept_sketch"},
            created_by="twin.render_concept_sketch",
            project_id=PID,
        )
        await twin.create_work_product(sketch)
        await make_decision_recorder(twin)(
            title="Concept A", rationale="chosen", assembly=ASSEMBLY, project_id=PROJECT
        )
        await structured_mesh.make_structured_mesh_recorder(twin)(project_id=PROJECT)
    finally:
        structured_mesh.set_blob_writer(None)
    await mc.write_context(
        twin, PROJECT, {"selected_process": mc.stated("FDM", source="api")}, source="api"
    )
    await dfm.evaluate_project(twin, PROJECT)
    lid = (await _current(twin))["CMP-lid"]
    assert lid.geometry_level == "L1"
    [wall] = lid.findings
    assert wall["rule_id"] == "fdm.min_wall" and wall["confidence"] == "estimated"
    assert wall["severity"] == "advisory"
    assert lid.geometry_refs[0].startswith("node:")


# ── incremental recompute ───────────────────────────────────────────────


async def test_a_wall_change_stales_and_recomputes_only_that_component(
    twin: InMemoryTwinAPI,
) -> None:
    keys = await _project(twin)
    await dfm.evaluate_project(twin, PROJECT)
    before = await _current(twin)
    assert before["CMP-enclosure"].findings == []
    lid_set = before["CMP-lid"].finding_set_id

    await _cad(twin, "Enclosure", _features(0.5), key=keys["enclosure"])

    after = await _current(twin)
    # The enclosure was recomputed on its new revision; the old set is history.
    enclosure = after["CMP-enclosure"]
    assert enclosure.finding_set_id != before["CMP-enclosure"].finding_set_id
    assert enclosure.geometry_refs == [f"{keys['enclosure']}@2"]
    assert [f["rule_id"] for f in enclosure.findings] == ["fdm.min_wall"]
    old = await twin.get_work_product(UUID(before["CMP-enclosure"].finding_set_id))
    assert record_staleness(old.metadata) == "superseded"
    # The lid's findings are untouched and still valid.
    assert after["CMP-lid"].finding_set_id == lid_set
    assert after["CMP-lid"].staleness["status"] == "current"


async def test_findings_appear_in_the_impact_walk(twin: InMemoryTwinAPI) -> None:
    from twin_core.impact.cross_domain import list_impacts
    from twin_core.impact.interfaces import list_interfaces
    from twin_core.models.interface import InterfaceType

    keys = await _project(twin)
    await dfm.evaluate_project(twin, PROJECT)
    edges = [
        e for e in await list_interfaces(twin, PROJECT) if e.type is InterfaceType.DFM_CONSTRAINS
    ]
    targets = sorted(e.target.ref for e in edges)
    assert targets == sorted([keys["enclosure"], keys["lid"], "MFC-PROJECT", "MFC-PROJECT"])
    assert all(e.source.ref.startswith("record:") and e.symmetric for e in edges)
    context_edge = next(e for e in edges if e.target.ref == "MFC-PROJECT")
    assert "selected_process" in context_edge.coupled_parameters
    assert "finish" not in context_edge.coupled_parameters

    await _cad(twin, "Enclosure", _features(0.5), key=keys["enclosure"])
    [impact] = await list_impacts(twin, PROJECT)
    assert [s.check_kind for s in impact.stale_results] == ["dfm"]
    assert any(e.edge_type is InterfaceType.DFM_CONSTRAINS for e in impact.impacted)
    # The recompute happens on its own: no tool rerun is proposed for it.
    assert impact.proposed_reruns == []


async def test_a_context_change_no_rule_reads_leaves_findings_valid(
    twin: InMemoryTwinAPI,
) -> None:
    await _project(twin)
    await dfm.evaluate_project(twin, PROJECT)
    before = {c: v.finding_set_id for c, v in (await _current(twin)).items()}
    await mc.write_context(
        twin, PROJECT, {"finish": mc.stated("gloss", source="api")}, source="api"
    )
    after = await _current(twin)
    assert {c: v.finding_set_id for c, v in after.items()} == before
    assert all(v.staleness["status"] == "revalidated" for v in after.values())
    assert len(await _sets(twin)) == 2


async def test_a_process_change_recomputes_under_the_new_profile(twin: InMemoryTwinAPI) -> None:
    await _project(twin, wall=6.0)
    await dfm.evaluate_project(twin, PROJECT)
    fdm_sets = {c: v.finding_set_id for c, v in (await _current(twin)).items()}

    out = await mc.write_context(
        twin,
        PROJECT,
        {"selected_process": mc.stated("Injection moulding", source="api")},
        source="api",
    )
    assert out.process_change == ("FDM", "Injection moulding")
    after = await _current(twin)
    enclosure = after["CMP-enclosure"]
    assert enclosure.profile == {"id": "injection_moulding", "version": "1.0.0"}
    assert enclosure.context_ref == "MFC-PROJECT@2"
    assert "injection_moulding.max_wall" in {f["rule_id"] for f in enclosure.findings}
    # The FDM findings are kept as stale history, not superseded or deleted.
    for set_id in fdm_sets.values():
        old = await twin.get_work_product(UUID(set_id))
        assert record_staleness(old.metadata) == "stale"
    history = await dfm.dfm_view(twin, PROJECT, include_history=True)
    assert {h.profile["id"] for h in history.history if h.profile} == {"fdm"}
    # What the person stated is untouched.
    stored = await mc.read_context(twin, PROJECT)
    assert stored is not None and stored.record.finish.value == "matte"
    assert stored.record.selected_material.value == "PETG"


# ── decisions ───────────────────────────────────────────────────────────


class _Workflow:
    def __init__(self) -> None:
        self.proposals: list[dict[str, Any]] = []

    async def propose_change(self, **kw: Any) -> Any:
        self.proposals.append(kw)

        class _P:
            change_id = UUID("00000000-0000-4000-8000-0000000000aa")

        return _P()


async def _wall_finding(twin: Any) -> str:
    await _project(twin, wall=0.5)
    await dfm.evaluate_project(twin, PROJECT)
    enclosure = (await _current(twin))["CMP-enclosure"]
    return str(enclosure.findings[0]["id"])


async def test_keep_design_is_recorded_and_carried_to_a_recompute(twin: InMemoryTwinAPI) -> None:
    fid = await _wall_finding(twin)
    out = await dfm.decide(twin, PROJECT, fid, "keep_design", actor="ann", reason="thin on purpose")
    assert out["decision"] == "keep_design" and not out["applied"]
    view = await dfm.dfm_view(twin, PROJECT)
    assert view.suggestions == 0
    finding = view.components[0].findings[0]
    assert finding["decision"]["by"] == "ann"
    # A recompute keeps the person's decision on the same finding.
    await mc.write_context(twin, PROJECT, {"quantity": mc.stated(10, source="api")}, source="api")
    again = await dfm.dfm_view(twin, PROJECT)
    assert again.components[0].context_ref == "MFC-PROJECT@2"
    assert again.components[0].findings[0]["id"] == fid
    assert again.components[0].findings[0]["decision"]["decision"] == "keep_design"


async def test_try_fix_is_a_proposal_unless_it_trades_a_protected_requirement(
    twin: InMemoryTwinAPI,
) -> None:
    from api_gateway.create import routes as create_routes
    from api_gateway.create.models import Creation, ReferenceRecord
    from api_gateway.create.store import SqliteCreationStore

    fid = await _wall_finding(twin)
    workflow = _Workflow()
    store = SqliteCreationStore()
    create_routes.init_create_store(store)
    try:
        plain = await dfm.decide(twin, PROJECT, fid, "try_fix", workflow=workflow)
        assert plain["requires_approval"] is False and workflow.proposals == []
        assert plain["proposal"]["changes"] == ["wall_thickness"]
        # The design itself is unchanged.
        enclosure = (await _current(twin))["CMP-enclosure"]
        assert enclosure.findings[0]["measured"] == 0.5

        creation = Creation(project_id=PROJECT)
        creation.brief.reference_details.append(
            ReferenceRecord(work_product_id="ref-1", protected_features=["the thin walls"])
        )
        store.save(creation, expected_version=None)
        held = await dfm.decide(twin, PROJECT, fid, "try_fix", workflow=workflow)
        assert held["requires_approval"] is True
        assert held["approval_id"] == "change:00000000-0000-4000-8000-0000000000aa"
        assert held["protected"] == ["it would change a protected feature: the thin walls"]
        [proposal] = workflow.proposals
        assert proposal["diff"]["action"] == "record_decision"
        assert proposal["project_id"] == PROJECT
    finally:
        create_routes.init_create_store(None)


async def test_a_fix_changing_a_stated_context_field_needs_approval(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    machine_library.register(
        {
            "layer": "instance",
            "kind": "fdm_printer",
            "name": "Workshop Mini",
            "capabilities": {
                "build_volume_mm": {
                    "value": [70, 70, 70],
                    "source_kind": "manufacturer_spec",
                    "source_ref": "https://example.com/mini",
                }
            },
        }
    )
    await _project(twin)
    await mc.write_context(
        twin,
        PROJECT,
        {"machine_or_supplier_profile": mc.stated({"machine": "Workshop Mini"}, source="api")},
        source="api",
    )
    await dfm.evaluate_project(twin, PROJECT)
    enclosure = (await _current(twin))["CMP-enclosure"]
    fid = next(f["id"] for f in enclosure.findings if f["rule_id"] == "fdm.build_volume")
    workflow = _Workflow()
    out = await dfm.decide(twin, PROJECT, fid, "try_fix", workflow=workflow)
    assert out["requires_approval"] is True
    assert any("machine or supplier profile was stated by a person" in p for p in out["protected"])


async def test_explore_process_previews_and_writes_nothing(twin: InMemoryTwinAPI) -> None:
    fid = await _wall_finding(twin)
    count = len(await _sets(twin))
    out = await dfm.decide(twin, PROJECT, fid, "explore_process")
    processes = [a["process"] for a in out["alternatives"]]
    assert "FDM" not in processes and "Injection moulding" in processes
    assert all(a["preview"] for a in out["alternatives"])
    assert len(await _sets(twin)) == count


async def test_an_unknown_finding_is_404(twin: InMemoryTwinAPI) -> None:
    await _project(twin)
    with pytest.raises(dfm.DfmError) as err:
        await dfm.decide(twin, PROJECT, "dfmf-nope", "keep_design")
    assert err.value.status == 404


# ── the manufacturability variant (DFM-003, DFM-005) ────────────────────


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), (250, 240, 220)).save(buf, format="PNG")
    return buf.getvalue()


async def test_the_manufacturability_variant_records_the_rules_that_shaped_it(
    twin: InMemoryTwinAPI,
) -> None:
    from api_gateway.twin.image_generation import GeneratedImage, ImageRequest
    from api_gateway.twin.sketch_generation import GenerateSketchRequest, SketchGenerationService

    class Gen:
        provider, model = "fake", "fake-1"

        def __init__(self) -> None:
            self.calls: list[ImageRequest] = []

        async def generate_request(self, request: ImageRequest) -> GeneratedImage:
            self.calls.append(request)
            return GeneratedImage(image_bytes=_png(), mime="image/png", provider="fake", model="x")

    def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        return f"work-products/{node_id}/{filename}"

    async def refs(_project: str) -> list[dict[str, Any]]:
        return []

    gen = Gen()
    service = SketchGenerationService(
        twin, None, generator=gen, blob_writer=write, reference_records=refs, timeout_s=5
    )
    request = GenerateSketchRequest(
        intent="a desk lamp", n_variants=2, manufacturability_variant=True
    )
    skipped = await service.start(await service.prepare(PROJECT, request), wait=True)
    assert skipped["manufacturability_variant"] == {
        "requested": True,
        "generated": False,
        "skipped_reason": "the process is not specified yet",
    }

    await mc.write_context(
        twin, PROJECT, {"selected_process": mc.stated("FDM", source="api")}, source="api"
    )
    out = await service.start(await service.prepare(PROJECT, request), wait=True)
    assert out["manufacturability_variant"]["generated"] is True
    plain, shaped = gen.calls[-2:]
    assert "Manufacturability-oriented variant for FDM" in shaped.prompt
    assert "Manufacturability-oriented" not in plain.prompt
    first, second = out["proposals"]
    assert first["variant_kind"] == "standard" and first["dfm_rules_applied"] == []
    assert second["variant_kind"] == "manufacturability"
    assert second["dfm_profile"] == "fdm@1.1.0"
    assert "fdm.overhang" in {r["rule_id"] for r in second["dfm_rules_applied"]}
    # A proposal shaped by guidance is still not assessed: nothing checked it.
    assert second["dfm_status"] == "not_assessed"


# ── routes and tools ────────────────────────────────────────────────────


@pytest.fixture
def client(twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(dfm_router)
    return TestClient(app)


async def test_routes_round_trip(twin: InMemoryTwinAPI, client: TestClient) -> None:
    await _project(twin, wall=0.5)
    base = f"/v1/twin/projects/{PROJECT}/dfm"
    empty = client.get(base)
    assert empty.status_code == 200, empty.text
    assert empty.json()["status"] == "not_assessed" and empty.json()["components"] == []

    evaluated = client.post(f"{base}/evaluate", json={})
    assert evaluated.status_code == 200, evaluated.text
    body = evaluated.json()
    assert body["status"] == "issues_found" and body["suggestions"] == 1
    assert body["heuristic_only"] is True and "Not an analysis pass" in body["disclaimer"]
    fid = body["components"][0]["findings"][0]["id"]

    decided = client.post(f"{base}/findings/{fid}/decision", json={"decision": "keep_design"})
    assert decided.status_code == 200, decided.text
    assert client.get(base).json()["suggestions"] == 0

    assert (
        client.post(
            f"{base}/findings/dfmf-x/decision", json={"decision": "keep_design"}
        ).status_code
        == 404
    )
    assert (
        client.post(f"{base}/findings/{fid}/decision", json={"decision": "ship_it"}).status_code
        == 422
    )
    assert client.get("/v1/twin/projects/not-a-uuid/dfm").status_code == 400
    history = client.get(f"{base}?history=true")
    assert history.status_code == 200 and "history" in history.json()


async def test_the_mcp_tools_evaluate_and_read(twin: InMemoryTwinAPI) -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    server = TwinServer(twin=twin, dfm_service=dfm.make_dfm_service(twin))
    assert {"twin.evaluate_dfm", "twin.get_dfm_findings"} <= set(server.tool_ids)
    await _project(twin, wall=0.5)
    evaluated = await server.evaluate_dfm({"project_id": PROJECT})
    assert evaluated["status"] == "issues_found"
    read = await server.get_dfm_findings({"project_id": PROJECT})
    assert read["suggestions"] == evaluated["suggestions"] == 1
    with pytest.raises(ValueError, match="project_id"):
        await server.evaluate_dfm({})


async def test_an_evaluation_publishes_a_manufacturing_operation(
    twin: InMemoryTwinAPI, publisher: ops.OperationPublisher
) -> None:
    await _project(twin)
    await dfm.evaluate_project(twin, PROJECT)
    events = [
        e for e in publisher.store.events_after(PROJECT, 0) if e.operation_id.startswith("job:dfm-")
    ]
    assert [e.type.value for e in events] == ["accepted", "completed"]
    assert all(e.domain == "manufacturing" for e in events)
