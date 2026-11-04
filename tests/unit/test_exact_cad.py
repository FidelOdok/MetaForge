"""Exact CAD on commit, B-Rep validity and revision-linked STEP (CDE slice 6).

Covers FRS GEO-003 (only a passing exact build moves the head; the last valid
revision is kept on failure), GEO-007/008 (STEP only from validated geometry,
cached per revision, stale when the head moves, re-import round trip),
DEF-007 (the release gate needs validated, exported CAD), DEF-009 (the
artifact state model), DEF-RC-04 (a late exact result never replaces a newer
head) and CHG-005 (no evidence is never a pass), as
``docs/twin_schema.md`` and ``docs/operations.md`` state them.

The CadQuery adapter is stubbed (CI has no CAD kernel); the kernel-side
checks are exercised for real in ``TestRealKernel`` when ``cadquery`` is
importable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.operations.heads import make_item_head_resolver
from api_gateway.twin import exact_cad as exact_cad_module
from api_gateway.twin.exact_cad import (
    EXACT_BUILD_TOOL,
    STEP_EXPORT_TOOL,
    ExactCadError,
    ExactCadService,
    GeometryValidityError,
    StepExportError,
    StepExportRefusedError,
    compare_round_trip,
    geometry_state_view,
    set_exact_cad_service,
)
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from tool_registry import jobs as jobs_module
from tool_registry.jobs import JobRunner, JobStore, current_job_id, set_job_head_resolver
from tool_registry.tools.cadquery.validity import (
    classify,
    configured_schema,
    schema_from_step_text,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.gates import GateCheckStatus, GateStatus, evaluate_g8_release
from twin_core.geometry_state import (
    GeometryState,
    GeometryStateError,
    StepExportState,
    authority,
    badge,
    check_transition,
    exact_cad_readiness,
    geometry_state_of,
    mark_released,
    step_export_view,
)
from twin_core.items import ItemRevisionConflictError, find_item

PROJECT = "66666666-6666-6666-6666-666666666666"
BOX_BBOX = {"min_x": 0.0, "min_y": 0.0, "min_z": 0.0, "max_x": 20.0, "max_y": 10.0, "max_z": 5.0}


def _step(body: str) -> bytes:
    return f"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n{body}\nENDSEC;\n".encode()


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[None]:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )
    monkeypatch.setenv("METAFORGE_CAD_CACHE_DIR", str(tmp_path / "cad_cache"))
    yield
    set_job_head_resolver(None)
    set_exact_cad_service(None)
    for tool in (EXACT_BUILD_TOOL, STEP_EXPORT_TOOL):
        jobs_module._retry_factories.pop(tool, None)  # noqa: SLF001


class FakeCad:
    """The CadQuery adapter, stubbed: scripts become STEP text, verdicts are set per test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.validity = "pass"
        self.validate_raises = False
        self.reimport_volume: float | None = None
        self.on_execute: Any = None

    def _measures(self) -> dict[str, Any]:
        return {"solid_count": 1, "volume_mm3": 1000.0, "bounding_box": dict(BOX_BBOX)}

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append(tool_id)
        if tool_id == "cadquery.execute_script":
            if self.on_execute is not None:
                await self.on_execute()
            return {
                "step_base64": _b64(_step(params["script"])),
                "volume_mm3": 1000.0,
                "bounding_box": dict(BOX_BBOX),
            }
        if tool_id == "cadquery.validate_shape":
            if self.validate_raises:
                raise RuntimeError("tool 'cadquery.validate_shape' is not served by this adapter")
            text = base64.b64decode(params["step_base64"]).decode()
            out = {
                "outcome": self.validity,
                "findings": [
                    {
                        "check": "free_edges",
                        "outcome": self.validity,
                        "detail": "2 free edge(s) on a solid (open boundary)"
                        if self.validity == "fail"
                        else "no free edges on any solid",
                    }
                ],
                **self._measures(),
            }
            if "EXPORTED" in text and self.reimport_volume is not None:
                out["volume_mm3"] = self.reimport_volume
            return out
        if tool_id == "cadquery.export_step":
            body = base64.b64decode(params["step_base64"]).decode()
            exported = body.replace("ENDSEC;", "EXPORTED;\nENDSEC;")
            return {
                "step_base64": _b64(exported.encode()),
                "schema_requested": "AP214",
                "schema": schema_from_step_text(exported),
                "source": self._measures(),
                "kernel": "OCCT via CadQuery",
            }
        raise RuntimeError(f"unexpected tool {tool_id}")


class Harness:
    def __init__(self) -> None:
        self.twin = InMemoryTwinAPI.create()
        self.record = make_geometry_recorder(self.twin, None)
        self.cad = FakeCad()
        self.events: list[dict[str, Any]] = []
        self.runner = JobRunner(JobStore(":memory:"), publisher=self._collect)
        self.service = ExactCadService(
            self.twin,
            bridge=self.cad,
            recorder=self.record,
            blob_writer=lambda node_id, filename, content: None,
            publisher=self._collect,
            job_runner=self.runner,
        )
        self.service.register_retries()
        set_job_head_resolver(make_item_head_resolver(lambda: self.twin))

    async def _collect(self, project_id: str, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def part(self, body: str = "v1", name: str = "Shelf Bracket") -> dict[str, Any]:
        content = _step(body)
        exact_cad_module._cache_put(content)  # noqa: SLF001 - the revision's stored STEP
        return dict(await self.record(step_base64=_b64(content), name=name, project_id=PROJECT))

    async def item(self, key: str) -> Any:
        return await find_item(self.twin, key, PROJECT)

    async def node(self, node_id: str) -> Any:
        return await self.twin.get_work_product(UUID(node_id))

    async def run_build(self, key: str, **kwargs: Any) -> Any:
        reply = await self.service.start_build(
            project_id=PROJECT, item_key=key, session_id="s", **kwargs
        )
        job = await self.runner.wait(reply["job_id"], 5.0)
        await self.runner.flush_events()
        return job

    def op_events(self, job_id: str) -> list[dict[str, Any]]:
        return sorted(
            (e for e in self.events if e.get("operation_id") == f"job:{job_id}"),
            key=lambda e: e["sequence"],
        )


@pytest.fixture
def h() -> Harness:
    return Harness()


# ── the state model (DEF-009, UI-004) ────────────────────────────────────


def test_released_is_only_set_by_the_release_path() -> None:
    with pytest.raises(GeometryStateError):
        check_transition(GeometryState.VALIDATED, GeometryState.RELEASED)
    assert (
        check_transition(GeometryState.VALIDATED, GeometryState.RELEASED, via_release=True)
        is GeometryState.RELEASED
    )
    with pytest.raises(GeometryStateError):
        check_transition(GeometryState.EXACT_READY, GeometryState.RELEASED, via_release=True)


def test_concept_and_illustrative_geometry_is_never_validated() -> None:
    for target in (GeometryState.VALIDATED, GeometryState.RELEASED, GeometryState.EXACT_READY):
        with pytest.raises(GeometryStateError):
            check_transition(
                GeometryState.CONCEPT, target, illustrative=True, validity="pass", via_release=True
            )
    assert geometry_state_of("render", {"authority": "illustrative"}) is GeometryState.CONCEPT
    # A label beats a stored state: an illustrative node never reads validated.
    assert (
        geometry_state_of("cad_model", {"authority": "illustrative", "geometry_state": "validated"})
        is GeometryState.CONCEPT
    )
    assert authority(GeometryState.PREVIEW) == "preview"
    assert authority(GeometryState.CONCEPT) == "illustrative"


def test_validated_needs_a_passing_verdict() -> None:
    for verdict in (None, "warn", "fail", "not_evaluated"):
        with pytest.raises(GeometryStateError):
            check_transition(GeometryState.EXACT_READY, GeometryState.VALIDATED, validity=verdict)
    assert (
        check_transition(GeometryState.EXACT_READY, GeometryState.VALIDATED, validity="pass")
        is GeometryState.VALIDATED
    )


def test_states_badges_and_legacy_cad() -> None:
    assert geometry_state_of("cad_model", {}) is GeometryState.EXACT_READY
    assert geometry_state_of("bom", {}) is None
    assert badge(GeometryState.EXACT_READY) == "Exact"
    assert badge(GeometryState.VALIDATED) == "Validated"
    assert badge(GeometryState.CONCEPT) == "Concept"
    assert badge(GeometryState.PREVIEW) == "Preview"
    view = geometry_state_view("cad_model", {"item_key": "CAD-X", "item_revision": 3})
    assert view == {
        "state": "exact_ready",
        "badge": "Exact",
        "authority": "exact",
        "itemKey": "CAD-X",
        "sourceRevision": 3,
    }


def test_a_ready_export_of_an_older_revision_reads_stale() -> None:
    meta = {"step_export": {"state": "ready", "content_hash": "h"}}
    assert step_export_view(meta, revision=2, head_revision=2, content_hash="h")["state"] == "ready"
    stale = step_export_view(meta, revision=1, head_revision=2, content_hash="h")
    assert stale is not None and stale["state"] == "stale"
    changed = step_export_view(meta, revision=2, head_revision=2, content_hash="other")
    assert changed is not None and changed["state"] == "stale"


# ── the adapter's verdict rules (GEO-003, CHG-005) ───────────────────────


def _measures(**over: Any) -> dict[str, Any]:
    base = {
        "is_valid": True,
        "solid_count": 1,
        "closed_solid_count": 1,
        "solid_volumes_mm3": [964.6],
        "free_edge_count": 0,
    }
    base.update(over)
    return base


def test_classify_pass_fail_warn_and_not_evaluated() -> None:
    assert classify(_measures())[0] == "pass"
    assert classify(_measures(is_valid=False))[0] == "fail"
    assert classify(_measures(solid_count=0, closed_solid_count=0, solid_volumes_mm3=[]))[0] == "fail"
    assert classify(_measures(closed_solid_count=0))[0] == "fail"
    assert classify(_measures(solid_volumes_mm3=[0.0]))[0] == "fail"
    assert classify(_measures(free_edge_count=3))[0] == "fail"
    assert (
        classify(
            _measures(solid_count=2, closed_solid_count=2, solid_volumes_mm3=[1, 2]),
            expect_single_solid=True,
        )[0]
        == "warn"
    )
    # Lack of evidence is never a pass.
    assert classify(_measures(is_valid=None))[0] == "not_evaluated"
    assert classify(_measures(free_edge_count=None))[0] == "not_evaluated"
    outcome, findings = classify(_measures(free_edge_count=2))
    assert outcome == "fail"
    assert any(f["check"] == "free_edges" and f["outcome"] == "fail" for f in findings)


def test_the_written_schema_is_read_from_the_file() -> None:
    ap214 = "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 3 1 1 }'));"
    ap242 = "FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }'));"
    assert schema_from_step_text(ap214) == "AP214"
    assert schema_from_step_text(ap242) == "AP242"
    assert schema_from_step_text("FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));") == "AP203"
    assert schema_from_step_text("no header") is None
    assert configured_schema({}) == "AP214"
    assert configured_schema({"CADQUERY_STEP_SCHEMA": "ap242"}) == "AP242"
    assert configured_schema({"CADQUERY_STEP_SCHEMA": "AP999"}) == "AP214"


def test_round_trip_compare() -> None:
    src = {"solid_count": 1, "volume_mm3": 1000.0, "bounding_box": dict(BOX_BBOX)}
    assert compare_round_trip(src, dict(src))[0] == "pass"
    assert compare_round_trip(src, {**src, "volume_mm3": 990.0})[0] == "fail"
    assert compare_round_trip(src, {**src, "solid_count": 2})[0] == "fail"
    moved = {**src, "bounding_box": {**BOX_BBOX, "max_x": 20.5}}
    assert compare_round_trip(src, moved)[0] == "fail"
    assert compare_round_trip(src, {**src, "bounding_box": None})[0] == "not_evaluated"
    assert compare_round_trip(None, src)[0] == "not_evaluated"


# ── exact on commit: only a pass moves the head (GEO-003, DEF-002) ───────


async def test_a_passing_build_becomes_the_validated_head(h: Harness) -> None:
    v1 = await h.part()
    key = v1["item_key"]
    job = await h.run_build(key, script="result = box(20, 10, 5)")

    assert job.status == "succeeded", job.error
    item = await h.item(key)
    assert item.head_revision == 2
    head = await h.node(str(item.head_node_id))
    assert head.metadata["geometry_state"] == "validated"
    assert head.metadata["validity"]["outcome"] == "pass"
    assert head.metadata["validity"]["base_revision"] == f"{key}@1"
    assert head.metadata["exact_source"]["kind"] == "cadquery_script"
    assert job.result["item_ref"] == f"{key}@2"

    events = h.op_events(job.job_id)
    types = [e["type"] for e in events]
    assert types == ["queued", "started", "milestone", "partial_result", "completed"]
    milestone, artifact, done = events[2], events[3], events[4]
    assert milestone["stage_id"] == "exact_geometry_ready"
    assert milestone["domain"] == "mechanical"
    assert milestone["artifact_refs"][0]["authority"] == "exact"
    assert artifact["stage_id"] == "artifact_ready"
    assert artifact["artifact_refs"][0]["authority"] == "validated"
    assert done["message"] == "Geometry validated"
    assert done["domain"] == "mechanical"
    assert done["artifact_refs"][0]["authority"] == "validated"


async def test_a_failed_check_keeps_the_last_valid_head(h: Harness) -> None:
    v1 = await h.part()
    key = v1["item_key"]
    ok = await h.run_build(key, script="result = box(20, 10, 5)")
    assert ok.status == "succeeded"
    validated_head = (await h.item(key)).head_node_id

    h.cad.validity = "fail"
    job = await h.run_build(key, script="result = broken()")

    assert job.status == "failed"
    assert "GeometryValidityError" in (job.error or "")
    item = await h.item(key)
    assert item.head_revision == 2 and item.head_node_id == validated_head
    head = await h.node(str(validated_head))
    assert head.metadata["geometry_state"] == "validated"
    attempt = head.metadata["exact_attempts"][-1]
    assert attempt["state"] == "failed" and attempt["outcome"] == "fail"
    assert attempt["findings"][0]["check"] == "free_edges"
    failed = h.op_events(job.job_id)[-1]
    assert failed["type"] == "failed"
    assert failed["message"] == "Geometry check failed; the last valid revision was kept"
    assert failed["action_refs"] == [{"kind": "retry", "id": f"job:{job.job_id}", "label": "Retry"}]


async def test_a_check_that_could_not_run_is_never_a_pass(h: Harness) -> None:
    v1 = await h.part()
    h.cad.validate_raises = True
    job = await h.run_build(v1["item_key"], script="result = box(1, 1, 1)")

    assert job.status == "failed"
    assert "GeometryCheckUnavailableError" in (job.error or "")
    item = await h.item(v1["item_key"])
    assert item.head_revision == 1
    head = await h.node(str(item.head_node_id))
    assert geometry_state_of("cad_model", head.metadata) is GeometryState.EXACT_READY
    assert head.metadata["exact_attempts"][-1]["outcome"] == "not_evaluated"


async def test_warnings_do_not_validate_or_move_the_head(h: Harness) -> None:
    v1 = await h.part()
    h.cad.validity = "warn"
    job = await h.run_build(v1["item_key"], script="result = two_boxes()")
    assert job.status == "succeeded"
    assert job.result["validated"] is False
    assert (await h.item(v1["item_key"])).head_revision == 1


async def test_without_a_source_the_head_is_validated_in_place(h: Harness) -> None:
    v1 = await h.part()
    job = await h.run_build(v1["item_key"])
    assert job.status == "succeeded", job.error
    item = await h.item(v1["item_key"])
    assert item.head_revision == 1
    head = await h.node(v1["node_id"])
    assert head.metadata["geometry_state"] == "validated"
    assert job.result["validated_in_place"] is True
    # A second request does no work: it is already validated.
    calls = len(h.cad.calls)
    again = await h.service.build(project_id=PROJECT, item_key=v1["item_key"])
    assert again["already_validated"] is True
    assert len(h.cad.calls) == calls


async def test_a_build_behind_the_head_ends_stale_before_any_work(h: Harness) -> None:
    v1 = await h.part()
    key = v1["item_key"]
    await h.part("v2")
    # Asked for directly, it is refused at once.
    with pytest.raises(ItemRevisionConflictError):
        await h.service.start_build(project_id=PROJECT, item_key=key, base_revision="1", session_id="s")
    # A queued job whose base moved before it ran ends stale and pays for nothing.
    args = {"project_id": PROJECT, "item_key": key, "base_revision": "1", "script": "x"}
    job, _ = await h.runner.start(
        EXACT_BUILD_TOOL,
        args,
        lambda: h.service.build(**args),
        session_id="s",
        project_id=PROJECT,
        target_key=key,
        base_revision="1",
    )
    done = await h.runner.wait(job.job_id, 5.0)
    assert done.status == "stale"
    assert h.cad.calls == []


async def test_a_late_exact_result_never_replaces_a_newer_head(h: Harness) -> None:
    """DEF-RC-04: the design changes while the exact build runs."""
    v1 = await h.part()
    key = v1["item_key"]
    newer: dict[str, Any] = {}

    async def person_commits_meanwhile() -> None:
        newer.update(await h.part("edited by a person"))

    h.cad.on_execute = person_commits_meanwhile
    job = await h.run_build(key, script="result = box(1, 1, 1)")

    assert job.status == "stale"
    item = await h.item(key)
    assert item.head_revision == 2
    assert str(item.head_node_id) == newer["node_id"]
    assert h.op_events(job.job_id)[-1]["type"] == "stale"


async def test_bad_requests_fail_at_once(h: Harness) -> None:
    v1 = await h.part()
    with pytest.raises(ExactCadError):
        await h.service.start_build(project_id=PROJECT, item_key="CAD-NOPE", session_id="s")
    with pytest.raises(ExactCadError):
        await h.service.start_build(
            project_id=PROJECT, item_key=v1["item_key"], design_ir={"bad": 1}, session_id="s"
        )
    with pytest.raises(ExactCadError):
        await h.service.start_build(
            project_id=PROJECT, item_key=v1["item_key"], script="x", design_ir={}, session_id="s"
        )


async def test_the_build_never_exports_step(h: Harness) -> None:
    """A commit builds and validates; STEP comes only on request (DEF-RC-02)."""
    v1 = await h.part()
    await h.run_build(v1["item_key"], script="result = box(20, 10, 5)")
    await h.run_build(v1["item_key"], script="result = box(21, 10, 5)")
    assert "cadquery.export_step" not in h.cad.calls
    item = await h.item(v1["item_key"])
    head = await h.node(str(item.head_node_id))
    assert "step_export" not in head.metadata


async def test_preview_and_concept_updates_trigger_no_step(h: Harness) -> None:
    """An illustrative update is a concept, has no STEP and cannot be exported."""
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    concept = await h.twin.create_work_product(
        WorkProduct(
            name="Reference model",
            type=WorkProductType.RENDER,
            domain="mechanical",
            file_path="",
            content_hash="c" * 64,
            format="glb",
            metadata={"authority": "illustrative"},
            created_by="twin.generate_concept_mesh",
            project_id=UUID(PROJECT),
        )
    )
    assert geometry_state_of("render", concept.metadata) is GeometryState.CONCEPT
    with pytest.raises(ExactCadError):
        await h.service.start_export(node_id=str(concept.id), session_id="s")
    assert h.cad.calls == []


# ── STEP from validated revisions only, cached, stale, round trip ────────


async def _validated_part(h: Harness) -> tuple[str, str]:
    v1 = await h.part()
    job = await h.run_build(v1["item_key"], script="result = box(20, 10, 5)")
    assert job.status == "succeeded", job.error
    return v1["item_key"], job.result["node_id"]


async def test_step_is_refused_for_a_revision_that_is_not_validated(h: Harness) -> None:
    v1 = await h.part()
    with pytest.raises(StepExportRefusedError):
        await h.service.start_export(node_id=v1["node_id"], session_id="s")
    assert "cadquery.export_step" not in h.cad.calls


async def test_step_export_is_cached_per_revision(h: Harness) -> None:
    key, node_id = await _validated_part(h)
    reply = await h.service.start_export(node_id=node_id, session_id="s")
    node = await h.node(node_id)
    assert node.metadata["step_export"]["state"] == "queued"
    job = await h.runner.wait(reply["job_id"], 5.0)
    await h.runner.flush_events()
    assert job.status == "succeeded", job.error

    export = (await h.node(node_id)).metadata["step_export"]
    assert export["state"] == "ready"
    assert export["item_ref"] == f"{key}@2"
    assert export["schema"] == "AP214"
    assert export["round_trip"]["outcome"] == "pass"
    assert export["content_hash"] == (await h.node(node_id)).content_hash
    events = h.op_events(job.job_id)
    assert [e["type"] for e in events] == ["queued", "started", "partial_result", "completed"]
    assert events[2]["artifact_refs"][0]["authority"] == "validated"
    assert events[2]["artifact_refs"][0]["kind"] == "step"

    # A repeat request returns the recorded file, with no adapter work.
    exports_before = h.cad.calls.count("cadquery.export_step")
    again = await h.service.start_export(node_id=node_id, session_id="s")
    assert again["cache_hit"] is True and again["status"] == "succeeded"
    assert h.cad.calls.count("cadquery.export_step") == exports_before
    content = exact_cad_module.read_export_file(export)
    assert content is not None and b"EXPORTED" in content


async def test_a_head_move_marks_older_exports_stale(h: Harness) -> None:
    key, node_id = await _validated_part(h)
    export_job = await h.service.start_export(node_id=node_id, session_id="s")
    done = await h.runner.wait(export_job["job_id"], 5.0)
    assert done.status == "succeeded"

    job = await h.run_build(key, script="result = box(30, 10, 5)")
    assert job.status == "succeeded"

    old = await h.node(node_id)
    assert old.metadata["step_export"]["state"] == "stale"
    view = await h.service.artifact_view(node_id)
    step = next(e for e in view["exports"] if e["format"] == "step")
    assert step["state"] == "STALE"
    assert step["url"] is not None  # still browsable, tied to its revision
    assert view["is_head"] is False
    stale = h.op_events(export_job["job_id"])[-1]
    assert stale["type"] == "stale" and stale["sequence"] == 95
    assert stale["message"] == "This STEP belongs to an older revision"


async def test_a_round_trip_mismatch_is_recorded_and_blocks_critical_use(h: Harness) -> None:
    _key, node_id = await _validated_part(h)
    h.cad.reimport_volume = 900.0
    out = await h.service.export_step(node_id=node_id)
    assert out["step_export"]["round_trip"]["outcome"] == "fail"
    assert out["step_export"]["state"] == "ready"
    with pytest.raises(StepExportError):
        await h.service.export_step(node_id=node_id, critical=True)


async def test_a_critical_export_fails_without_a_passing_round_trip(h: Harness) -> None:
    _key, node_id = await _validated_part(h)
    h.cad.reimport_volume = 900.0
    with pytest.raises(StepExportError):
        await h.service.export_step(node_id=node_id, critical=True)
    assert (await h.node(node_id)).metadata["step_export"]["state"] == "failed"


# ── the gates (DEF-007) ──────────────────────────────────────────────────


async def test_g8_is_incomplete_until_cad_is_validated_and_exported(h: Harness) -> None:
    pid = UUID(PROJECT)
    v1 = await h.part()
    evaluation = await evaluate_g8_release(h.twin, pid)
    check = next(c for c in evaluation.checks if c.id == "exact_cad_validated")
    assert check.status is GateCheckStatus.INCOMPLETE
    assert "not validated" in check.detail and "no STEP" in check.detail
    assert evaluation.status is GateStatus.FAILED or evaluation.status is GateStatus.NOT_READY

    job = await h.run_build(v1["item_key"], script="result = box(20, 10, 5)")
    readiness = await exact_cad_readiness(h.twin, pid)
    assert [r.ready for r in readiness] == [False]
    await h.service.export_step(node_id=job.result["node_id"], critical=True)

    evaluation = await evaluate_g8_release(h.twin, pid)
    check = next(c for c in evaluation.checks if c.id == "exact_cad_validated")
    assert check.status is GateCheckStatus.PASS
    assert f"{v1['item_key']}@2" in check.detail


async def test_g8_without_cad_items_has_no_exact_cad_check(h: Harness) -> None:
    evaluation = await evaluate_g8_release(h.twin, UUID(PROJECT))
    assert all(c.id != "exact_cad_validated" for c in evaluation.checks)


def test_an_incomplete_check_leaves_the_gate_not_ready() -> None:
    from twin_core.consistency.gates import GateCheck, _status_from_checks

    def chk(status: GateCheckStatus) -> GateCheck:
        return GateCheck(id="x", label="x", status=status, detail="")

    assert _status_from_checks([chk(GateCheckStatus.PASS), chk(GateCheckStatus.INCOMPLETE)]) is (
        GateStatus.NOT_READY
    )
    assert _status_from_checks([chk(GateCheckStatus.FAIL), chk(GateCheckStatus.INCOMPLETE)]) is (
        GateStatus.FAILED
    )
    assert _status_from_checks(
        [chk(GateCheckStatus.INCOMPLETE), chk(GateCheckStatus.NOT_EVALUATED)]
    ) is GateStatus.NOT_READY


async def test_the_flow_release_gate_reports_incomplete_cad(h: Harness) -> None:
    from api_gateway.runs.gate_eval import ProjectGateEvaluator
    from api_gateway.runs.routes import _GateCheckers

    v1 = await h.part()

    class _Backend:
        async def get_project(self, project_id: str) -> Any:
            return None

    class _Constraints:
        async def check(self, project_id: str) -> Any:
            from orchestrator.design_flow.executor import ConstraintReport

            return ConstraintReport(checked=False)

    checkers = _GateCheckers(
        ProjectGateEvaluator(_Backend(), twin=h.twin), _Constraints(), consistency=None
    )
    g8 = {"id": "manufacturing", "required_deliverables": [], "gate": {"gate_id": "G8"}}
    report = await checkers.evaluate(g8, PROJECT)
    assert report.ready is False
    assert any("exact CAD incomplete" in m and v1["item_key"] in m for m in report.missing)

    # Any other gate is untouched.
    g6 = {"id": "design", "required_deliverables": [], "gate": {"gate_id": "G6"}}
    assert (await checkers.evaluate(g6, PROJECT)).missing == []

    job = await h.run_build(v1["item_key"], script="result = box(20, 10, 5)")
    await h.service.export_step(node_id=job.result["node_id"])
    report = await checkers.evaluate(g8, PROJECT)
    assert report.ready is True and report.missing == []
    assert "validated with round-tripped STEP" in report.reason


async def test_only_the_release_path_marks_released(h: Harness) -> None:
    v1 = await h.part()
    head = await h.node(v1["node_id"])
    assert await mark_released(h.twin, head) is False  # not validated
    job = await h.run_build(v1["item_key"], script="result = box(20, 10, 5)")
    node = await h.node(job.result["node_id"])
    assert await mark_released(h.twin, node) is True
    assert (await h.node(job.result["node_id"])).metadata["geometry_state"] == "released"


# ── the job runner seam this slice adds ──────────────────────────────────


async def test_the_runner_names_the_running_job_and_uses_result_phrases() -> None:
    sink: list[dict[str, Any]] = []

    async def publish(project_id: str, event: dict[str, Any]) -> None:
        sink.append(event)

    runner = JobRunner(JobStore(":memory:"), publisher=publish)
    seen: dict[str, Any] = {}

    async def work() -> dict[str, Any]:
        seen["job"] = current_job_id()
        return {
            "operation_event": {
                "message": "Geometry validated",
                "artifact_refs": [{"kind": "step", "id": "e1", "label": "STEP", "authority": "validated"}],
            }
        }

    job, _ = await runner.start("twin.build_exact_cad", {"a": 1}, work, session_id="s", project_id=PROJECT)
    await runner.wait(job.job_id, 2.0)
    await runner.flush_events()
    assert seen["job"] == job.job_id
    assert current_job_id() is None
    done = next(e for e in sink if e["type"] == "completed")
    assert done["message"] == "Geometry validated"
    assert done["artifact_refs"][0]["authority"] == "validated"
    assert done["domain"] == "mechanical"


# ── routes ───────────────────────────────────────────────────────────────


async def test_routes_map_the_service(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from api_gateway.twin import routes as twin_routes
    from api_gateway.twin.exact_cad_routes import router

    monkeypatch.setattr(twin_routes, "get_twin", lambda: h.twin)
    set_exact_cad_service(h.service)
    app = FastAPI()
    app.include_router(router)
    v1 = await h.part()
    key = v1["item_key"]

    def call(method: str, url: str, **kw: Any) -> Any:
        async def go() -> Any:
            import httpx

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return await client.request(method, url, **kw)

        return go()

    r = await call("GET", f"/v1/twin/nodes/{v1['node_id']}/exports")
    assert r.status_code == 200
    body = r.json()
    assert body["geometry_state"] == "exact_ready" and body["badge"] == "Exact"
    assert {e["format"]: e["state"] for e in body["exports"]}["step"] == "NONE"
    assert body["can_export_step"] is False

    r = await call("POST", f"/v1/twin/nodes/{v1['node_id']}/exports/step", json={})
    assert r.status_code == 409

    r = await call(
        "POST", f"/v1/twin/items/{key}/exact-build", json={"project_id": PROJECT, "base_revision": "7"}
    )
    assert r.status_code == 409

    r = await call(
        "POST",
        f"/v1/twin/items/{key}/exact-build",
        json={"project_id": PROJECT, "script": "result = box(20, 10, 5)"},
    )
    assert r.status_code == 202
    job = await h.runner.wait(r.json()["job_id"], 5.0)
    assert job.status == "succeeded"
    node_id = job.result["node_id"]

    r = await call("POST", f"/v1/twin/nodes/{node_id}/exports/step", json={"critical": True})
    assert r.status_code == 202
    await h.runner.wait(r.json()["job_id"], 5.0)
    r = await call("POST", f"/v1/twin/nodes/{node_id}/exports/step", json={})
    assert r.status_code == 200 and r.json()["cache_hit"] is True

    r = await call("GET", f"/v1/twin/nodes/{node_id}/exports")
    states = {e["format"]: e for e in r.json()["exports"]}
    assert states["step"]["state"] == "READY" and states["step"]["schema"] == "AP214"
    assert states["native"]["state"] == "READY"
    assert r.json()["last_validated"]["item_ref"] == f"{key}@2"

    r = await call("GET", f"/v1/twin/nodes/{node_id}/exports/step/file")
    assert r.status_code == 200 and b"EXPORTED" in r.content
    assert r.headers["content-type"].startswith("application/step")


async def test_the_twin_adapter_registers_both_tools_with_a_service(h: Harness) -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    server = TwinServer(twin=h.twin, exact_cad_service=h.service)
    tools = set(server.tool_ids)
    assert {"twin.build_exact_cad", "twin.export_cad_step", "twin.job_status"} <= tools
    v1 = await h.part()
    out = await server.build_exact_cad(
        {"item_key": v1["item_key"], "project_id": PROJECT, "wait": True}
    )
    assert out["validated"] is True
    step = await server.export_cad_step({"node_id": v1["node_id"], "wait": True})
    assert step["step_export"]["round_trip"]["outcome"] == "pass"
    with pytest.raises(ValueError):
        await server.export_cad_step({"node_id": ""})


# ── the real kernel, when this environment has one ───────────────────────


class TestRealKernel:
    """``cadquery.validate_shape`` and ``cadquery.export_step`` against OCCT."""

    @pytest.fixture(autouse=True)
    def _kernel(self) -> None:
        pytest.importorskip("cadquery")

    @staticmethod
    def _step_of(shape: Any, tmp_path: Any) -> str:
        import cadquery as cq

        path = tmp_path / "in.step"
        cq.exporters.export(shape, str(path))
        return base64.b64encode(path.read_bytes()).decode()

    def test_a_solid_passes(self, tmp_path: Any) -> None:
        import cadquery as cq

        from tool_registry.tools.cadquery.validity import validate_step

        box = cq.Workplane("XY").box(20, 10, 5).faces(">Z").hole(3)
        out = validate_step(self._step_of(box, tmp_path), work_dir=str(tmp_path))
        assert out["outcome"] == "pass", out["findings"]
        assert out["solid_count"] == 1 and out["free_edge_count"] == 0
        assert out["volume_mm3"] > 0

    def test_an_open_shell_fails(self, tmp_path: Any) -> None:
        import cadquery as cq

        from tool_registry.tools.cadquery.validity import validate_step

        faces = cq.Workplane("XY").box(10, 10, 10).val().Faces()[:-1]
        shell = cq.Workplane().add(cq.Shell.makeShell(faces))
        out = validate_step(self._step_of(shell, tmp_path), work_dir=str(tmp_path))
        assert out["outcome"] == "fail"

    @pytest.mark.parametrize("schema", ["AP214", "AP242"])
    def test_export_declares_the_schema_it_wrote_and_round_trips(
        self, tmp_path: Any, schema: str
    ) -> None:
        import cadquery as cq

        from tool_registry.tools.cadquery.validity import export_step, validate_step

        box = cq.Workplane("XY").box(20, 10, 5)
        out = export_step(self._step_of(box, tmp_path), work_dir=str(tmp_path), schema=schema)
        assert out["schema"] == schema
        back = validate_step(out["step_base64"], work_dir=str(tmp_path))
        assert compare_round_trip(out["source"], back)[0] == "pass"


def test_a_stale_cache_entry_is_never_trusted(tmp_path: Any) -> None:
    content = b"ISO-10303-21;"
    digest = exact_cad_module._cache_put(content)  # noqa: SLF001
    assert digest == hashlib.sha256(content).hexdigest()
    assert exact_cad_module._cache_get(digest) == content  # noqa: SLF001
    assert exact_cad_module._cache_get("../../etc/passwd") is None  # noqa: SLF001


async def test_retry_runs_a_failed_build_again(h: Harness) -> None:
    v1 = await h.part()
    h.cad.validity = "fail"
    failed = await h.run_build(v1["item_key"], script="result = box(1, 1, 1)")
    assert failed.status == "failed"
    h.cad.validity = "pass"
    retried = await h.runner.retry(failed.job_id)
    done = await h.runner.wait(retried.job_id, 5.0)
    assert done.status == "succeeded" and done.retry_of == failed.job_id
    assert (await h.item(v1["item_key"])).head_revision == 2
    await asyncio.sleep(0)


def test_errors_carry_fixed_operation_phrases() -> None:
    assert GeometryValidityError.operation_message.startswith("Geometry check failed")
    assert StepExportError.operation_message == "STEP export failed"
    assert issubclass(ItemRevisionConflictError, ValueError)
    assert StepExportState.STALE.value == "stale"
