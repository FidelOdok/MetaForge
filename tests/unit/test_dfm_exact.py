"""Exact DFM, verification, the DFM report and the release checklist (C3D slice 6).

* The exact FDM checks against measured B-Rep values: wall thickness, pins,
  holes, overhang faces (the build plate excluded) and the build volume, with
  face anchors; ``analysis_passed`` only when every required check ran and
  found nothing, ``analysis_pending`` (incomplete) with a reason otherwise
  (DFM-007, DFM-008, DFM-013).
* The adapter's measures on real CadQuery solids when this environment has a
  kernel (skipped otherwise), and its pure helpers always.
* In the twin: an exact set supersedes the heuristic set and keeps it in
  history; a heuristic recompute of the same geometry never replaces it;
  validation schedules the job; a failed measurement stores an incomplete set.
* Physical verification needs a person's evidence and a reviewer's approval.
* The report (DFM-016) and the release checklist (3D-RC-016): each missing
  item blocks the release, in both gate engines and the release package.
"""

from __future__ import annotations

import base64
import hashlib
import math
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_exact, dfm_report
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.dfm_routes import router as dfm_router
from api_gateway.twin.engineering_entity_approval import make_engineering_entity_approver
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from tool_registry.tools.cadquery.dfm_measure import (
    downward_tilt_deg,
    group_cylinders,
    sample_fractions,
    samples_per_face,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.gates import GateCheckStatus, evaluate_g8_release
from twin_core.consistency.record_pins import record_staleness, unregister_head_moved_listener
from twin_core.dfm import PROFILES, MachineCapability, MachineContext, ProcessContext
from twin_core.dfm.exact import evaluate_exact, verification_item_id
from twin_core.dfm.heuristics import DfmFindingSet, VerificationItem, product_status
from twin_core.dfm.release_checklist import manufacturing_checklist, readiness
from twin_core.items import commit_revision, plan_revision
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.manufacturing import DfmStatus
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7a1c0a52-1b5e-4b7a-9a43-3f1f2b9c0d07"
PID = UUID(PROJECT)
FDM = PROFILES["fdm"]
STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\n"

ASSEMBLY: dict[str, Any] = {"parts": [{"name": "Enclosure"}], "connections": []}

MINI = {
    "layer": "instance",
    "kind": "fdm_printer",
    "name": "Workshop Mini",
    "processes": ["fdm"],
    "capabilities": {
        "build_volume_mm": {
            "value": [180, 180, 180],
            "source_kind": "manufacturer_spec",
            "source_ref": "https://example.com/mini",
        }
    },
}


# ── measured values, as cadquery.dfm_measure returns them ────────────────


def _face(fid: str, normal: list[float], area: float, *, plate: bool = False) -> dict[str, Any]:
    return {
        "face_id": fid,
        "surface": "plane",
        "area": area,
        "normal": normal,
        "most_downward_normal": normal,
        "on_build_plate": plate,
        "signature": f"plane {fid}",
    }


def measured(
    *,
    wall: float = 2.0,
    holes: tuple[float, ...] = (),
    bosses: tuple[float, ...] = (),
    extents: tuple[float, float, float] = (80.0, 60.0, 40.0),
    overhang: bool = False,
    wall_status: str = "measured",
) -> dict[str, Any]:
    faces = [
        _face("s0f0", [0, 0, -1], 4800, plate=True),
        _face("s0f1", [0, 0, 1], 4800),
        _face("s0f2", [1, 0, 0], 3200),
    ]
    if overhang:
        faces.append(_face("s0f9", [0, 0, -1], 120.0))
    return {
        "build_direction": [0, 0, 1],
        "wall_thickness": {
            "status": wall_status,
            "method": "ray cast along the inward surface normal",
            "limits": ["only sampled points are measured"],
            "samples": 120,
            "min_mm": wall if wall_status == "measured" else None,
            "thinnest": [{"face_id": "s0f1", "thickness_mm": wall}],
            "per_face_min_mm": {"s0f1": wall, "s0f2": max(wall, 3.0)},
            **({} if wall_status == "measured" else {"reason": "no sampled ray found an exit"}),
        },
        "faces": faces,
        "planar_faces": [],
        "holes": [
            {
                "face_ids": [f"s0f{10 + i}"],
                "diameter_mm": d,
                "kind": "hole",
                "signature": f"hole {d}",
            }
            for i, d in enumerate(holes)
        ],
        "bosses": [
            {
                "face_ids": [f"s0f{20 + i}"],
                "diameter_mm": d,
                "kind": "boss",
                "signature": f"pin {d}",
            }
            for i, d in enumerate(bosses)
        ],
        "build_frame": {"extents_mm": list(extents), "height_mm": extents[2]},
        "bounding_box": {"min_x": 0, "min_y": 0, "min_z": 0, "max_x": 80, "max_y": 60, "max_z": 40},
        "hole_limits": ["complete cylindrical faces only"],
        "kernel": "OCCT via CadQuery",
    }


def _machine(volume: list[float] | None = None) -> MachineContext:
    return MachineContext(
        id="m1",
        name="Workshop Mini",
        capabilities={
            "build_volume_mm": MachineCapability(
                value=volume or [180, 180, 180], source="Workshop Mini build_volume_mm (spec)"
            )
        },
    )


def _ctx(machine: MachineContext | None = None) -> ProcessContext:
    return ProcessContext(
        process="FDM",
        process_authority="user_stated",
        material="PETG",
        machine=machine,
        context_ref="MFC-PROJECT@1",
    )


def _exact(
    m: dict[str, Any] | None, machine: MachineContext | None = None, **kw: Any
) -> DfmFindingSet:
    return evaluate_exact(
        component_id="CMP-enclosure",
        component_name="Enclosure",
        geometry_ref="CAD-ENCLOSURE@2",
        context=_ctx(machine),
        profile=FDM,
        measured=m,
        **kw,
    )


# ── the exact checks (pure) ──────────────────────────────────────────────


def test_passes_only_when_every_required_check_ran_and_found_nothing() -> None:
    fs = _exact(measured(holes=(3.0,), bosses=(4.0,)), _machine())
    assert fs.kind == "exact" and fs.heuristic_only is False
    assert fs.status is DfmStatus.ANALYSIS_PASSED and fs.complete
    assert sorted(fs.rules_run) == sorted(FDM.exact_checks)
    assert fs.guidance_only_rules == ["fdm.bridge", "fdm.support_access"]
    assert "not a guarantee of build success" in fs.note
    assert fs.exact_evidence["tool"] == "cadquery.dfm_measure"
    assert fs.exact_evidence["build_direction_source"].startswith("default +Z")


def test_no_machine_leaves_the_analysis_incomplete_never_passed() -> None:
    fs = _exact(measured())
    assert fs.status is DfmStatus.ANALYSIS_PENDING and not fs.complete
    gap = next(n for n in fs.not_assessed if n.rule_id == "fdm.build_volume")
    assert gap.reason == "no machine is selected in the manufacturing context"
    assert fs.note.startswith("Manufacturing analysis incomplete")


def test_a_wall_that_could_not_be_measured_is_incomplete() -> None:
    fs = _exact(measured(wall_status="not_measured"), _machine())
    assert fs.status is DfmStatus.ANALYSIS_PENDING
    assert any(n.rule_id == "fdm.min_wall" and "exit" in n.reason for n in fs.not_assessed)


def test_a_thin_wall_is_found_and_anchored_to_its_faces() -> None:
    fs = _exact(measured(wall=0.5), _machine(), signatures={"s0f1": "normal (0, 0, 1) area 4800"})
    assert fs.status is DfmStatus.ISSUES_FOUND
    f = next(f for f in fs.findings if f.rule_id == "fdm.min_wall")
    assert f.measured == 0.5 and f.limit == 0.8 and f.comparison == ">="
    assert f.confidence == "exact" and f.severity == "advisory"  # general guidance
    assert f.anchor.face_signature == "normal (0, 0, 1) area 4800"
    assert f.anchor.faces == ["s0f1"] and f.anchor.feature == "s0f1"
    assert f.evidence_refs == ["CAD-ENCLOSURE@2"]


def test_small_holes_and_pins_are_found() -> None:
    fs = _exact(measured(holes=(1.5, 3.0), bosses=(1.2,)), _machine())
    rules = sorted(f.rule_id for f in fs.findings)
    assert rules == ["fdm.min_feature", "fdm.min_hole"]
    hole = next(f for f in fs.findings if f.rule_id == "fdm.min_hole")
    assert hole.measured == 1.5 and hole.anchor.faces == ["s0f10"]
    assert hole.anchor.face_signature == "hole 1.5"


def test_overhangs_use_outward_normals_and_skip_the_build_plate() -> None:
    clean = _exact(measured(), _machine())
    assert not [f for f in clean.findings if f.rule_id == "fdm.overhang"]
    assert clean.exact_evidence["measured"]["on_build_plate"] == ["s0f0"]
    fs = _exact(measured(overhang=True), _machine())
    f = next(f for f in fs.findings if f.rule_id == "fdm.overhang")
    assert f.anchor.faces == ["s0f9"] and f.measured == 90.0
    assert fs.exact_evidence["measured"]["overhang_area_mm2"] == 120.0


def test_build_volume_against_the_selected_machine() -> None:
    fs = _exact(measured(extents=(80, 60, 200)), _machine([180, 180, 180]))
    f = next(f for f in fs.findings if f.rule_id == "fdm.build_volume")
    assert f.severity == "blocking" and f.rule_kind.value == "machine_limit"
    assert f.anchor.feature == "height along the build direction"
    assert "Workshop Mini" in f.limit_source
    # The footprint may turn on the bed: 170 x 60 fits a 180 square.
    assert not _exact(measured(extents=(60, 170, 40)), _machine()).findings


def test_a_failed_measurement_assesses_nothing() -> None:
    fs = _exact(None, _machine(), measure_error="adapter offline")
    assert fs.status is DfmStatus.ANALYSIS_PENDING and not fs.rules_run
    assert all("could not run (adapter offline)" in n.reason for n in fs.not_assessed)
    assert fs.exact_evidence["measure_error"] == "adapter offline"


def test_the_set_refuses_a_pass_it_cannot_back() -> None:
    fs = _exact(measured(wall=0.5), _machine())
    with pytest.raises(ValueError):
        DfmFindingSet.model_validate({**fs.model_dump(), "status": "analysis_passed"})
    incomplete = _exact(measured())
    with pytest.raises(ValueError):
        DfmFindingSet.model_validate({**incomplete.model_dump(), "status": "analysis_passed"})
    with pytest.raises(ValueError):
        DfmFindingSet(component_id="x", status=DfmStatus.ANALYSIS_PASSED)  # heuristic
    with pytest.raises(ValueError):
        DfmFindingSet.model_validate({**fs.model_dump(), "status": "released"})


def test_a_product_is_only_as_far_along_as_its_least_assessed_component() -> None:
    passed = DfmStatus.ANALYSIS_PASSED
    assert product_status([passed, passed]) is passed
    assert product_status([passed, DfmStatus.NOT_ASSESSED]) is DfmStatus.NOT_ASSESSED
    assert product_status([passed, DfmStatus.ISSUES_FOUND]) is DfmStatus.ISSUES_FOUND


def test_verification_items_follow_the_context_and_never_satisfy_themselves() -> None:
    none = _exact(measured(), _machine())
    assert none.verification_items == []
    fs = _exact(measured(), _machine(), tolerances_stated=True, production_goal="production")
    assert [v.rule_id for v in fs.verification_items] == [
        "fdm.fit_test",
        "fdm.layer_strength",
        "fdm.first_article",
    ]
    assert all(v.status == "open" for v in fs.verification_items)
    proto = _exact(measured(), _machine(), production_goal="functional_prototype")
    assert [v.rule_id for v in proto.verification_items] == ["fdm.layer_strength"]


def test_approved_evidence_is_carried_only_for_the_same_geometry() -> None:
    vid = verification_item_id("CMP-enclosure", "fdm.layer_strength")
    before = VerificationItem(
        id=vid,
        rule_id="fdm.layer_strength",
        component_id="CMP-enclosure",
        title="Strength across the layers",
        kind="physical_test",
        needed="a test",
        reason="r",
        status="satisfied",
        evidence=[{"id": "e1", "state": "approved", "geometry_ref": "CAD-ENCLOSURE@2"}],
        satisfied_by={"by": "rev", "geometry_ref": "CAD-ENCLOSURE@2"},
    )
    same = _exact(measured(), _machine(), production_goal="pilot", previous_items=[before])
    item = next(v for v in same.verification_items if v.id == vid)
    assert item.status == "satisfied"
    moved = evaluate_exact(
        component_id="CMP-enclosure",
        component_name="Enclosure",
        geometry_ref="CAD-ENCLOSURE@3",
        context=_ctx(_machine()),
        profile=FDM,
        measured=measured(),
        production_goal="pilot",
        previous_items=[before],
    )
    item = next(v for v in moved.verification_items if v.id == vid)
    assert item.status == "open" and item.evidence  # kept on record, no longer settles it


# ── the adapter's pure helpers ───────────────────────────────────────────


def test_sampling_stays_inside_the_budget() -> None:
    assert sample_fractions(2) == [0.25, 0.75]
    assert samples_per_face(10, 5, 1500) == 5
    side = samples_per_face(400, 5, 1500)
    assert 400 * side * side <= 1500 and side >= 1
    assert samples_per_face(10_000, 5, 1500) == 1


def test_cylinders_merge_seam_halves_and_skip_partial_arcs() -> None:
    half = {
        "radius": 1.5,
        "axis_point": (5.0, 5.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "kind": "hole",
        "span_rad": math.pi,
    }
    fillet = {**half, "radius": 2.0, "span_rad": math.pi / 2, "face_id": "s0f9"}
    out = group_cylinders(
        [
            {**half, "face_id": "s0f1"},
            {**half, "face_id": "s0f2", "axis_direction": (0.0, 0.0, -1.0)},
            fillet,
        ]
    )
    full = [c for c in out if c["complete"]]
    assert len(full) == 1 and full[0]["face_ids"] == ["s0f1", "s0f2"]
    assert full[0]["diameter_mm"] == 3.0
    assert any(not c["complete"] and c["radius"] == 2.0 for c in out)


def test_downward_tilt() -> None:
    assert downward_tilt_deg((0, 0, -1), (0, 0, 1)) == pytest.approx(90.0)
    assert downward_tilt_deg((1, 0, 0), (0, 0, 1)) == pytest.approx(0.0)


# ── the real kernel, when this environment has one ───────────────────────


class TestRealKernel:
    """``cadquery.dfm_measure`` on real CadQuery solids, then the exact checks."""

    @pytest.fixture(autouse=True)
    def _kernel(self) -> None:
        pytest.importorskip("cadquery")

    @staticmethod
    def _measure(shape: Any, tmp_path: Any, **kw: Any) -> dict[str, Any]:
        import cadquery as cq

        from tool_registry.tools.cadquery.dfm_measure import dfm_measure
        from tool_registry.tools.cadquery.validity import write_step

        path = tmp_path / "part.step"
        write_step(cq, shape, str(path))
        step = base64.b64encode(path.read_bytes()).decode()
        return dfm_measure(step, work_dir=str(tmp_path), **kw)

    def test_a_shelled_box_reads_its_wall(self, tmp_path: Any) -> None:
        import cadquery as cq

        out = self._measure(cq.Workplane("XY").box(40, 40, 20).faces(">Z").shell(-0.6), tmp_path)
        wall = out["wall_thickness"]
        assert wall["status"] == "measured" and wall["min_mm"] == pytest.approx(0.6, abs=1e-3)
        assert wall["samples"] > 0 and wall["limits"]
        fs = _exact(out, _machine())
        f = next(f for f in fs.findings if f.rule_id == "fdm.min_wall")
        assert f.measured == pytest.approx(0.6, abs=1e-3) and f.anchor.faces

    def test_holes_and_pins_by_diameter(self, tmp_path: Any) -> None:
        import cadquery as cq

        plate = (
            cq.Workplane("XY")
            .box(30, 30, 3)
            .faces(">Z")
            .workplane()
            .pushPoints([(-8, 0)])
            .hole(1.5)
            .faces(">Z")
            .workplane()
            .pushPoints([(8, 0)])
            .circle(0.6)
            .extrude(5)
        )
        out = self._measure(plate, tmp_path)
        assert [h["diameter_mm"] for h in out["holes"]] == [pytest.approx(1.5)]
        assert [b["diameter_mm"] for b in out["bosses"]] == [pytest.approx(1.2)]
        fs = _exact(out, _machine())
        assert sorted(f.rule_id for f in fs.findings) == [
            "fdm.min_feature",
            "fdm.min_hole",
        ]  # the 1.2 mm pin reads 1.2 mm through: no wall under 0.8 mm

    def test_overhangs_skip_the_bed_and_find_a_pocket_ceiling(self, tmp_path: Any) -> None:
        import cadquery as cq

        box = cq.Workplane("XY").box(20, 20, 10)
        clean = self._measure(box, tmp_path)
        assert [f["face_id"] for f in clean["faces"] if f["on_build_plate"]] == ["s0f4"]
        assert not [f for f in _exact(clean, _machine()).findings]
        pocket = box.faces("<Z").workplane().rect(14, 14).cutBlind(-3)
        fs = _exact(self._measure(pocket, tmp_path), _machine())
        f = next(f for f in fs.findings if f.rule_id == "fdm.overhang")
        assert len(f.anchor.faces) == 1 and f.measured == pytest.approx(90.0)

    def test_extents_follow_the_build_direction(self, tmp_path: Any) -> None:
        import cadquery as cq

        out = self._measure(cq.Workplane("XY").box(20, 10, 5), tmp_path, build_direction=(1, 0, 0))
        assert out["build_frame"]["height_mm"] == pytest.approx(20.0)
        assert sorted(out["build_frame"]["extents_mm"]) == pytest.approx([5.0, 10.0, 20.0])


# ── in the twin ──────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    # The slicer cross-check has its own tests (test_dfm_context_tools.py).
    monkeypatch.setenv("METAFORGE_DFM_SLICER_CHECK", "off")

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


class FakeCad:
    """The exact CAD service, stubbed: a STEP per revision and set measures."""

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def read_step(self, wp: Any) -> bytes:
        return STEP

    async def invoke_tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        assert tool_id == "cadquery.dfm_measure"
        self.calls.append(params)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeWorkflow:
    def __init__(self) -> None:
        self.proposals: list[dict[str, Any]] = []

    async def propose_change(self, **kw: Any) -> Any:
        self.proposals.append(kw)

        class _C:
            change_id = uuid4()

        return _C()


VALIDATED = {
    "geometry_state": "validated",
    "step_export": {"state": "ready", "round_trip": {"outcome": "pass"}},
}


async def _cad(
    twin: Any, name: str, meta: dict[str, Any], key: str | None = None
) -> tuple[str, int, UUID]:
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


async def _project(
    twin: Any,
    machine_library: SqliteMachineLibrary,
    *,
    process: str = "FDM",
    meta: dict[str, Any] | None = None,
    machine: bool = True,
    goal: str | None = None,
) -> tuple[str, UUID]:
    await make_decision_recorder(twin)(
        title="Concept A", rationale="chosen", assembly=ASSEMBLY, project_id=PROJECT
    )
    key, _rev, node_id = await _cad(twin, "Enclosure", meta if meta is not None else VALIDATED)
    fields: dict[str, Any] = {
        "selected_process": mc.stated(process, source="api"),
        "selected_material": mc.stated("PETG", source="api"),
    }
    if machine:
        machine_library.register(MINI)
        fields["machine_or_supplier_profile"] = mc.stated(
            {"machine": "Workshop Mini"}, source="api"
        )
    if goal:
        fields["production_goal"] = mc.stated(goal, source="api")
    await mc.write_context(twin, PROJECT, fields, source="api")
    return key, node_id


async def _sets(twin: Any) -> list[Any]:
    return await twin.list_work_products(
        work_product_type=WorkProductType.DFM_FINDING_SET, project_id=PID
    )


async def _run(twin: Any, node_id: UUID, result: dict[str, Any] | Exception) -> dict[str, Any]:
    return await dfm_exact.run_exact(twin, PROJECT, node_id=str(node_id), service=FakeCad(result))


async def test_an_exact_set_supersedes_the_heuristic_one_and_keeps_it(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await dfm.evaluate_project(twin, PROJECT)
    heuristic = await _sets(twin)
    assert len(heuristic) == 1

    out = await _run(twin, node_id, measured())
    assert out["status"] == "analysis_passed" and out["complete"]
    view = await dfm.dfm_view(twin, PROJECT, include_history=True)
    (comp,) = view.components
    assert comp.kind == "exact" and comp.status == "analysis_passed"
    assert view.heuristic_only is False and "not a guarantee" in view.disclaimer
    assert [h.kind for h in view.history] == ["heuristic"]
    old = await twin.get_work_product(heuristic[0].id)
    assert record_staleness(old.metadata) == "superseded"
    node = next(n for n in await _sets(twin) if n.id == UUID(out["finding_set_id"]))
    assert node.metadata["result"] == {"verdict": "pass", "heuristic_only": False}
    assert node.metadata["authority"] == "exact"
    cad = await twin.get_work_product(node_id)
    assert cad.metadata["dfm_status"] == "analysis_passed"

    # A heuristic recompute of the same geometry never replaces it.
    again = await dfm.evaluate_project(twin, PROJECT)
    assert [fs.kind for _g, _n, fs, _c in again] == ["exact"]
    assert len(await _sets(twin)) == 2


async def test_validation_schedules_the_exact_job(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)

    class Runner:
        started: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

        async def start(self, tool: str, args: Any, work: Any, **kw: Any) -> Any:
            self.started.append((tool, dict(args), kw))
            job = type("J", (), {"job_id": "job-1"})()
            return job, False

        def reply(self, job: Any, reused: bool) -> dict[str, Any]:
            return {"job_id": job.job_id, "status": "running"}

    runner = Runner()
    out = await dfm_exact.schedule(twin, PROJECT, node_id=str(node_id), job_runner=runner)
    assert out["scheduled"] and out["job_id"] == "job-1" and out["profile"] == "fdm@1.1.0"
    tool, args, kw = runner.started[0]
    assert tool == "dfm.evaluate_exact" and args["build_direction"] == [0.0, 0.0, 1.0]
    assert kw["target_key"].startswith("DFM:CAD-") and kw["project_id"] == PROJECT
    # Until the job ends the component reads analysis pending.
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    assert comp.exact_checks_scheduled and comp.status == "analysis_pending"


async def test_only_a_validated_revision_under_an_exact_process_is_checked(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library, meta={"geometry_state": "exact_ready"})
    with pytest.raises(dfm_exact.ExactDfmError) as err:
        await dfm_exact.schedule(twin, PROJECT, node_id=str(node_id), wait=True)
    assert err.value.status == 409

    other = InMemoryTwinAPI.create()
    _key, node_id = await _project(
        other, SqliteMachineLibrary(), process="CNC milling", machine=False
    )
    out = await dfm_exact.schedule(other, PROJECT, node_id=str(node_id), wait=True)
    assert out["scheduled"] is False and "no exact checks exist for CNC" in out["reason"]


async def test_a_failed_measurement_stores_an_incomplete_set_and_fails_the_job(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    with pytest.raises(dfm_exact.ExactMeasureFailedError):
        await _run(twin, node_id, RuntimeError("tool 'cadquery.dfm_measure' is not served"))
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    assert comp.kind == "exact" and comp.status == "analysis_pending" and not comp.complete
    assert all("could not run" in n["reason"] for n in comp.not_assessed)


# ── physical verification ────────────────────────────────────────────────


async def test_verification_needs_evidence_and_a_reviewers_approval(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library, goal="functional_prototype")
    await _run(twin, node_id, measured())
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    (item,) = comp.verification_items
    assert item["status"] == "open" and item["rule_id"] == "fdm.layer_strength"

    with pytest.raises(dfm_exact.ExactDfmError):
        await dfm_exact.record_verification_evidence(twin, PROJECT, item["id"], note="  ")
    workflow = FakeWorkflow()
    out = await dfm_exact.record_verification_evidence(
        twin,
        PROJECT,
        item["id"],
        note="Printed upright, held 20 N for 1 h",
        attachment_name="photo.jpg",
        attachment_base64=base64.b64encode(b"jpeg").decode(),
        actor="ana",
        workflow=workflow,
    )
    assert out["requires_approval"] and out["approval_id"].startswith("change:")
    assert workflow.proposals[0]["diff"]["action"] == "accept_dfm_verification"
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    assert comp.verification_items[0]["status"] == "awaiting_approval"
    evidence = comp.verification_items[0]["evidence"][0]
    assert evidence["attachment"]["sha256"] == hashlib.sha256(b"jpeg").hexdigest()
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.item("verification").status == "fail"

    done = await dfm_exact.accept_verification(
        twin, workflow.proposals[0]["diff"], reviewer="lead", approval_id="change:x"
    )
    assert done["applied"]
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    assert comp.verification_items[0]["status"] == "satisfied"
    assert comp.verification_items[0]["satisfied_by"]["by"] == "lead"
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.item("verification").status == "pass"


async def test_the_approval_executor_applies_verification(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.assistant.apply import make_apply_executor
    from api_gateway.assistant.schemas import DesignChangeProposal

    _key, node_id = await _project(twin, machine_library, goal="functional_prototype")
    await _run(twin, node_id, measured())
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    workflow = FakeWorkflow()
    await dfm_exact.record_verification_evidence(
        twin, PROJECT, comp.verification_items[0]["id"], note="tested", workflow=workflow
    )
    monkeypatch.setattr(twin_routes, "get_twin", lambda: twin)
    proposal = DesignChangeProposal(
        change_id=uuid4(),
        agent_code="dfm",
        description="d",
        diff=workflow.proposals[0]["diff"],
        work_products_affected=[],
        session_id=uuid4(),
        project_id=PROJECT,
    )
    proposal.reviewer = "lead"
    out = await make_apply_executor(None)(proposal)
    assert out["applied"] and out["action"] == "accept_dfm_verification"


# ── the report ───────────────────────────────────────────────────────────


async def test_the_report_holds_the_revision_findings_and_what_is_unresolved(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    key, node_id = await _project(twin, machine_library, goal="pilot")
    await _run(twin, node_id, measured(wall=0.5, holes=(1.5,)))
    out = await dfm_report.generate_report(twin, PROJECT, actor="ana")
    report = out["report"]
    assert out["reused"] is False and out["staleness"]["status"] == "current"
    assert report["process"] == "FDM" and report["material"] == "PETG"
    assert report["context_ref"].startswith("MFC-PROJECT@")
    assert report["profiles"] == [{"id": "fdm", "version": "1.1.0", "process": "FDM"}]
    assert f"{key}@1" in report["product_revision"]["refs"]
    (comp,) = report["components"]
    assert comp["maturity"] == "Exact" and comp["required"]
    rows = {f["rule_id"]: f for f in comp["dfm"]["findings"]}
    assert rows["fdm.min_wall"]["measured"] == 0.5 and rows["fdm.min_wall"]["limit"] == 0.8
    assert rows["fdm.min_hole"]["anchor"]["faces"] == ["s0f10"]
    assert comp["dfm"]["measurement"]["wall_limits"]
    assert {u["rule_id"] for u in report["unresolved"] if u["kind"] == "finding"} == {
        "fdm.min_wall",
        "fdm.min_hole",
    }
    assert {v["rule_id"] for v in report["verification_items"]} == {
        "fdm.layer_strength",
        "fdm.first_article",
    }
    assert report["status"] == "issues_found" and report["stale"]["any"] is False
    assert report["checklist"]["releasable"] is False
    md = out["markdown"]
    assert "# DFM report" in md and "fdm.min_wall" in md and "## Release checklist" in md
    assert "—" not in md

    again = await dfm_report.generate_report(twin, PROJECT)
    assert again["reused"] and again["report_id"] == out["report_id"]

    # The geometry moves on: the report reads stale and stays browsable.
    await _cad(twin, "Enclosure", VALIDATED, key=key)
    old = await dfm_report.report_by_id(twin, PROJECT, out["report_id"])
    assert old["staleness"]["status"] == "stale"


# ── the release checklist and both gate engines ──────────────────────────


async def _resolved_project(twin: Any, machine_library: SqliteMachineLibrary) -> tuple[str, UUID]:
    key, node_id = await _project(twin, machine_library)
    await _run(twin, node_id, measured())
    recorded = await make_engineering_entity_recorder(twin)(
        entity_type="release_approval", statement="Approved.", project_id=PROJECT
    )
    await make_engineering_entity_approver(twin)(entity_id=recorded["node_id"], approved_by="lead")
    return key, node_id


async def test_a_resolved_checklist_allows_production(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    await _resolved_project(twin, machine_library)
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.releasable, checklist.blockers
    assert readiness(checklist)["level"] == "production"
    g8 = await evaluate_g8_release(twin, PID)
    check = next(c for c in g8.checks if c.id == "manufacturing_release_checklist")
    assert check.status is GateCheckStatus.PASS


async def test_no_checklist_without_an_exact_process(twin: InMemoryTwinAPI) -> None:
    await _cad(twin, "Bracket", VALIDATED)
    assert await manufacturing_checklist(twin, PID) is None
    assert readiness(None)["label"] == "Manufacturing not assessed"
    g8 = await evaluate_g8_release(twin, PID)
    assert all(c.id != "manufacturing_release_checklist" for c in g8.checks)


@pytest.mark.parametrize(
    "missing", ["components_exact", "dfm", "verification", "approvals", "step_round_trip"]
)
async def test_each_missing_item_blocks_the_release(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary, missing: str
) -> None:
    key, node_id = await _project(
        twin,
        machine_library,
        goal="functional_prototype" if missing == "verification" else None,
        meta={"geometry_state": "validated"} if missing == "step_round_trip" else None,
    )
    if missing == "components_exact":
        # A second required component with no exact model (and so no exact DFM).
        await make_decision_recorder(twin)(
            title="Concept B",
            rationale="adds a lid",
            assembly={"parts": [{"name": "Enclosure"}, {"name": "Lid"}], "connections": []},
            project_id=PROJECT,
        )
    await _run(twin, node_id, measured(wall=0.5) if missing == "dfm" else measured())
    if missing != "approvals":
        recorded = await make_engineering_entity_recorder(twin)(
            entity_type="release_approval", statement="Approved.", project_id=PROJECT
        )
        await make_engineering_entity_approver(twin)(entity_id=recorded["node_id"])

    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and not checklist.releasable
    failing = [i.id for i in checklist.items if i.status == "fail"]
    expected = ["components_exact", "dfm"] if missing == "components_exact" else [missing]
    assert failing == expected, checklist.blockers
    ready = readiness(checklist)
    assert ready["production"] is False and ready["next_steps"]
    assert ready["prototype"] is (missing in ("verification", "approvals"))

    # The twin's own G8 engine: the checklist fails (approvals are G8's own check).
    g8 = await evaluate_g8_release(twin, PID)
    check = next(c for c in g8.checks if c.id == "manufacturing_release_checklist")
    if missing == "approvals":
        assert check.status is GateCheckStatus.PASS
        assert next(c for c in g8.checks if c.id == "release_approved").status.value == "fail"
    else:
        assert check.status is GateCheckStatus.FAIL
        assert checklist.item(missing).blockers[0] in check.detail

    # The flow runner's G8 gate engine.
    from api_gateway.runs.gate_eval import ProjectGateEvaluator
    from api_gateway.runs.routes import _GateCheckers

    class _Backend:
        async def get_project(self, project_id: str) -> Any:
            return None

    class _Constraints:
        async def check(self, project_id: str) -> Any:
            from orchestrator.design_flow.executor import ConstraintReport

            return ConstraintReport(checked=False)

    checkers = _GateCheckers(
        ProjectGateEvaluator(_Backend(), twin=twin), _Constraints(), consistency=None
    )
    g8_flow = {"id": "manufacturing", "required_deliverables": [], "gate": {"gate_id": "G8"}}
    report = await checkers.evaluate(g8_flow, PROJECT)
    if missing == "approvals":
        assert not any("manufacturing release checklist" in m for m in report.missing)
    else:
        assert report.ready is False
        assert any("manufacturing release checklist" in m for m in report.missing)


async def test_accepted_issues_resolve_the_dfm_item(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await _run(twin, node_id, measured(wall=0.5))
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.item("dfm").status == "fail"
    (comp,) = (await dfm.dfm_view(twin, PROJECT)).components
    await dfm.decide(twin, PROJECT, comp.findings[0]["id"], "keep_design", actor="lead")
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.item("dfm").status == "pass"
    assert "issues accepted" in checklist.item("dfm").detail
    report = (await dfm_report.generate_report(twin, PROJECT))["report"]
    assert report["status"] == "analysis_passed"  # accepted issues count as resolved
    assert report["unresolved"] == []


async def test_the_release_package_is_refused_with_the_blockers(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    from api_gateway.twin.release_package import make_release_package_creator

    key, node_id = await _project(twin, machine_library)
    await _run(twin, node_id, measured(wall=0.5))
    create = make_release_package_creator(
        twin, engineering_entity_recorder=make_engineering_entity_recorder(twin)
    )
    with pytest.raises(ValueError) as err:
        await create(project_id=PROJECT)
    assert "Manufacturing release checklist" in str(err.value)
    assert "manufacturing issue(s) open" in str(err.value)
    cad = await twin.get_work_product(node_id)
    assert cad.metadata["geometry_state"] == "validated"  # never released


# ── routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(twin_routes, "get_twin", lambda: twin)
    app = FastAPI()
    app.include_router(dfm_router)
    return TestClient(app)


async def test_routes(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary, client: TestClient
) -> None:
    base = f"/v1/twin/projects/{PROJECT}/dfm"
    assert client.get(f"{base}/checklist").json() == {
        "project_id": PROJECT,
        "applies": False,
        "readiness": readiness(None),
    }
    assert client.get(f"{base}/report").status_code == 404
    key, node_id = await _project(twin, machine_library, meta={"geometry_state": "exact_ready"})
    assert client.post(f"{base}/exact", json={"item_key": key, "wait": True}).status_code == 409
    assert client.post(f"{base}/exact", json={}).status_code == 422

    _key2, _rev2, node2 = await _cad(twin, "Enclosure", VALIDATED, key=key)
    await _run(twin, node2, measured())
    body = client.get(f"{base}/checklist").json()
    assert body["applies"] and [i["id"] for i in body["items"]] == [
        "components_exact",
        "dfm",
        "verification",
        "approvals",
        "step_round_trip",
        "eda_checks",
        "assembly_dfa",
        "outputs_current",
    ]
    assert body["readiness"]["level"] == "prototype"
    made = client.post(f"{base}/report").json()
    assert made["report"]["status"] == "analysis_passed"
    md = client.get(f"{base}/report", params={"format": "markdown"})
    assert md.status_code == 200 and md.text.startswith("# DFM report")
    assert (
        client.get(f"{base}/reports/{made['report_id']}").json()["report_id"] == made["report_id"]
    )
    assert (
        client.post(f"{base}/verification/dfmv-missing/evidence", json={"note": "x"}).status_code
        == 404
    )
