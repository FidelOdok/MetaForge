"""Exact DFM on a validated revision, and physical verification (C3D slice 6).

**When it runs** (DFM-007). After the exact CAD service validates a revision
(:func:`on_validated`, registered on ``exact_cad``'s validated hook), after a
geometry or manufacturing context recompute reaches a component whose best
geometry is a validated revision, or on request: when the component's
manufacturing context selects a process whose rule profile names exact
checks (today FDM), the checks are scheduled as a background job
(``dfm.evaluate_exact``, domain ``manufacturing``). Scheduling records the
component's heuristic set with ``exact_checks_scheduled``, so its status
reads ``analysis_pending`` until the job ends.

**What the job does.** It reads the revision's STEP, calls the CadQuery
adapter's ``cadquery.dfm_measure`` over MCP (never a direct call), signs the
planar faces it anchors to with their geometry signatures, and evaluates the
profile's exact checks (:func:`twin_core.dfm.exact.evaluate_exact`). The
result is a ``dfm_finding_set`` of kind ``exact``, pinned to the geometry
revision and the manufacturing context revision; it supersedes the
component's heuristic set (kept in history), and a later heuristic recompute
of the same geometry never replaces it. A measurement that fails stores an
incomplete exact set (the release stays blocked) and ends the job failed.

**Physical verification** (DFM-013). Fit, strength or first-article items
the profile names are never satisfied automatically.
:func:`record_verification_evidence` stores a person's note or upload as
evidence awaiting approval and files a design-change approval;
:func:`accept_verification` (run when a reviewer approves it) marks the item
satisfied for that geometry revision.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.dfm import ComponentGeometry, DfmFindingSet, profile_for_process
from twin_core.dfm.exact import MEASURE_TOOL, evaluate_exact
from twin_core.dfm.heuristics import VerificationItem

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.dfm_exact")

EXACT_JOB_TOOL = "dfm.evaluate_exact"
EXACT_EXPECTED_SECONDS = 30.0
DEFAULT_BUILD_DIRECTION: tuple[float, float, float] = (0.0, 0.0, 1.0)
#: Largest upload kept as verification evidence.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024

MSG_SCHEDULED = "Checking the exact model for manufacturing"
MSG_MEASURED = "Exact model measured for manufacturing"
MSG_READY = "Manufacturing analysis ready"
MSG_FAILED = "Manufacturing analysis could not run; the release stays blocked"


class ExactDfmError(ValueError):
    """A request that cannot be honoured; ``status`` is its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ExactMeasureFailedError(RuntimeError):
    """The exact measurement could not run; an incomplete set was stored."""


def _metric(name: str, *args: str) -> None:
    from api_gateway.twin.dfm_findings import _metric as metric

    metric(name, *args)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ExactDfmError(f"project id {project_id!r} is not a UUID") from exc


def _service(service: Any = None) -> Any:
    if service is not None:
        return service
    from api_gateway.twin.exact_cad import get_exact_cad_service

    return get_exact_cad_service()


async def _emit(
    project_id: str, *, name: str, event_type: str, sequence: int, message: str
) -> None:
    """One operation event on the running job's operation (domain manufacturing)."""
    from mcp_core.operation_events import publish_operation_event
    from tool_registry.jobs import current_job_id

    job = current_job_id()
    if not job:
        return
    event = {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"metaforge:job:{job}:{name}")),
        "operation_id": f"job:{job}",
        "kind": "job",
        "sequence": sequence,
        "type": event_type,
        "state": "running",
        "domain": "manufacturing",
        "stage_id": name,
        "stage_title": message,
        "occurred_at": _now(),
        "severity": "info",
        "message": message,
        "artifact_refs": [],
    }
    try:
        await publish_operation_event(project_id, event)
    except Exception as exc:  # noqa: BLE001 - a status line never fails the work
        logger.info("dfm_exact_event_dropped", name=name, error=str(exc))


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


async def _target(twin: Any, pid: UUID, *, node_id: str | None, item_key: str | None) -> Any:
    """``(item, revision, node, component_id, name)`` for a CAD revision."""
    from twin_core.geometry_state import GeometryState, geometry_state_of
    from twin_core.items import find_item, item_for_node, parse_item_ref
    from twin_core.representation import COMPONENT_ID_KEY

    node = None
    item = None
    revision = 0
    if node_id:
        try:
            node = await twin.get_work_product(UUID(str(node_id)))
        except ValueError as exc:
            raise ExactDfmError(f"{node_id!r} is not a node id") from exc
        found = await item_for_node(twin, UUID(str(node_id))) if node is not None else None
        if found is not None:
            item, revision = found
    elif item_key:
        key, pin = parse_item_ref(item_key)
        item = await find_item(twin, key, pid)
        if item is not None and item.head_node_id is not None:
            revision = pin or item.head_revision
            node_ref = item.head_node_id
            if pin and pin != item.head_revision:
                from twin_core.items import item_history

                for rev in await item_history(twin, item, include_drafts=False):
                    if rev.revision == pin:
                        node_ref = rev.node_id
            node = await twin.get_work_product(node_ref)
    if node is None or item is None:
        raise ExactDfmError("no CAD revision to check", 404)
    meta = dict(node.metadata or {})
    state = geometry_state_of("cad_model", meta)
    if state not in (GeometryState.VALIDATED, GeometryState.RELEASED):
        raise ExactDfmError(
            f"{item.key}@{revision} is {state.value if state else 'not exact'}; exact "
            "manufacturing checks need a validated exact model",
            409,
        )
    cid = str(meta.get(COMPONENT_ID_KEY) or f"item:{item.key}")
    return item, revision, node, cid, item.name


async def _context(twin: Any, pid: UUID, name: str, cid: str) -> tuple[Any, Any]:
    from api_gateway.twin.dfm_findings import _context_for, process_context

    stored = await _context_for(twin, pid, SimpleNamespace(name=name, component_id=cid))  # type: ignore[arg-type]
    return stored, process_context(stored)


class Orientation:
    """The build direction the exact checks measure along, and where it came from."""

    __slots__ = ("direction", "rotation_deg", "source", "origin")

    def __init__(
        self,
        direction: tuple[float, float, float],
        rotation_deg: float | None,
        source: str,
        origin: str,
    ) -> None:
        self.direction = direction
        self.rotation_deg = rotation_deg
        #: In words, for the evidence: ``stated in the request``, ``manufacturing
        #: context MFC-...@n (stated by a person)``, ``default +Z ...``.
        self.source = source
        #: ``request``, ``context`` or ``default``.
        self.origin = origin


def resolve_orientation(
    stored: Any, explicit: list[float] | tuple[float, ...] | None
) -> Orientation:
    """The request's direction when it names one, else the context's, else +Z.

    The context's ``print_orientation`` is read only while it is current (an
    assumption made stale by a process change is ignored).
    """
    from twin_core.manufacturing import Authority

    if explicit is not None:
        values = tuple(float(v) for v in explicit)
        if len(values) != 3:
            raise ExactDfmError("build_direction is three numbers")
        return Orientation(
            (values[0], values[1], values[2]), None, "stated in the request", "request"
        )
    if stored is not None:
        entry = stored.record.get("print_orientation")
        if entry.known and not entry.stale and isinstance(entry.value, dict):
            d = [float(v) for v in entry.value.get("build_direction") or []]
            if len(d) == 3:
                who = (
                    "stated by a person"
                    if entry.authority is Authority.USER_STATED
                    else "assumed, may change"
                )
                rotation = entry.value.get("rotation_deg")
                return Orientation(
                    (d[0], d[1], d[2]),
                    float(rotation) if isinstance(rotation, int | float) else None,
                    f"manufacturing context {stored.ref} ({who})",
                    "context",
                )
    return Orientation(
        DEFAULT_BUILD_DIRECTION, None, "default +Z (no orientation stated)", "default"
    )


def _context_facts(stored: Any) -> tuple[bool, str | None]:
    """``(tolerances stated, production goal)`` from a stored context."""
    if stored is None:
        return False, None
    record = stored.record
    tol = record.get("tolerances")
    goal = record.get("production_goal")
    return (
        bool(tol.known and not tol.stale),
        str(goal.value) if goal.known and not goal.stale and goal.value else None,
    )


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


async def schedule(
    twin: Any,
    project_id: str | UUID,
    *,
    node_id: str | None = None,
    item_key: str | None = None,
    trigger: str = "validated",
    build_direction: list[float] | tuple[float, float, float] | None = None,
    session_id: str = "dfm",
    wait: bool = False,
    mark_pending: bool = True,
    service: Any = None,
    job_runner: Any = None,
) -> dict[str, Any]:
    """Schedule the exact checks for a validated revision, or say why not."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.dfm.exact.schedule") as span:
        span.set_attribute("dfm.trigger", trigger)
        item, revision, node, cid, name = await _target(
            twin, pid, node_id=node_id, item_key=item_key
        )
        ref = f"{item.key}@{revision}"
        span.set_attribute("item.ref", ref)
        stored, ctx = await _context(twin, pid, name, cid)
        profile = profile_for_process(ctx.process)
        if profile is None or not profile.exact_checks:
            reason = (
                "the process is not chosen"
                if not ctx.process
                else f"no exact checks exist for {profile.process if profile else ctx.process} yet"
            )
            logger.info("dfm_exact_not_scheduled", item_ref=ref, reason=reason)
            return {"scheduled": False, "item_ref": ref, "component_id": cid, "reason": reason}
        # The request's direction overrides the context's print orientation.
        orientation = resolve_orientation(stored, build_direction)
        explicit = list(orientation.direction) if orientation.origin == "request" else None
        direction = list(orientation.direction)
        span.set_attribute("dfm.orientation", orientation.origin)
        if mark_pending:
            from api_gateway.twin.dfm_findings import evaluate_project

            try:
                await evaluate_project(
                    twin,
                    pid,
                    component_ids=[cid],
                    trigger="exact_scheduled",
                    exact_checks_scheduled=True,
                )
            except Exception as exc:  # noqa: BLE001 - the exact job still runs
                logger.info("dfm_exact_pending_mark_failed", item_ref=ref, error=str(exc))

        async def work() -> dict[str, Any]:
            return await run_exact(
                twin,
                pid,
                node_id=str(node.id),
                trigger=trigger,
                build_direction=explicit,
                service=service,
            )

        if wait:
            out = await work()
            return {"scheduled": True, "status": "succeeded", "result": out}
        if job_runner is None:
            from tool_registry.jobs import default_job_runner

            job_runner = default_job_runner()
        job, reused = await job_runner.start(
            EXACT_JOB_TOOL,
            {
                "project_id": str(pid),
                "node_id": str(node.id),
                # No trigger here: a validation and a recompute of the same
                # revision are the same work, so they coalesce into one job.
                "build_direction": direction,
                # A retry re-reads the context unless the request named it.
                "build_direction_explicit": explicit is not None,
            },
            work,
            session_id=session_id,
            project_id=str(pid),
            expected_seconds=EXACT_EXPECTED_SECONDS,
            target_key=f"DFM:{item.key}",
            base_revision=str(revision),
        )
        logger.info(
            "dfm_exact_scheduled", item_ref=ref, job_id=job.job_id, reused=reused, trigger=trigger
        )
        return {
            "scheduled": True,
            "item_ref": ref,
            "component_id": cid,
            "profile": profile.ref,
            "build_direction": direction,
            "build_direction_source": orientation.source,
            **dict(job_runner.reply(job, reused)),
        }


def exact_retry(arguments: dict[str, Any]) -> Any:
    """The work of a retried exact DFM job, from its stored arguments."""
    from api_gateway.twin.routes import get_twin

    node_id = arguments.get("node_id")
    project_id = arguments.get("project_id")
    if not node_id or not project_id:
        raise ValueError("the failed exact DFM job names no revision")
    explicit = arguments.get("build_direction_explicit")
    stored_direction = arguments.get("build_direction")
    if explicit is None:
        # A job stored before the flag existed: only a non-default direction
        # can have come from the request.
        explicit = isinstance(stored_direction, list) and [
            float(v) for v in stored_direction
        ] != list(DEFAULT_BUILD_DIRECTION)
    named = stored_direction if explicit else None
    return lambda: run_exact(
        get_twin(),
        str(project_id),
        node_id=str(node_id),
        trigger="retry",
        build_direction=named,
    )


async def on_validated(twin: Any, project_id: str, result: dict[str, Any]) -> None:
    """After exact CAD validated a revision: schedule its exact checks. Never raises."""
    node_id = result.get("node_id")
    if not node_id or not project_id:
        return
    try:
        await schedule(twin, project_id, node_id=str(node_id), trigger="validated")
    except ExactDfmError as exc:
        logger.info("dfm_exact_not_scheduled", node_id=node_id, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - never undo a validation
        logger.warning("dfm_exact_schedule_failed", node_id=node_id, error=str(exc))


async def schedule_for_component(
    twin: Any, pid: UUID, gathered: Any, *, trigger: str = "recompute"
) -> dict[str, Any] | None:
    """Re-run exact checks for a component whose best geometry is a validated revision."""
    refs = [r for r in gathered.geometry.geometry_refs if "@" in r and not r.startswith("node:")]
    if not refs:
        return None
    try:
        return await schedule(twin, pid, item_key=refs[0], trigger=trigger, mark_pending=False)
    except ExactDfmError as exc:
        logger.info("dfm_exact_not_scheduled", item_ref=refs[0], reason=str(exc))
        return None


def register() -> None:
    """Hook exact DFM onto exact CAD validation and register the job retry."""
    from api_gateway.twin.exact_cad import register_validated_listener
    from tool_registry.jobs import register_retry

    register_validated_listener(on_validated)
    register_retry(EXACT_JOB_TOOL, exact_retry)


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def _signatures(measured: dict[str, Any] | None) -> dict[str, str]:
    """Face id -> geometry signature: planar faces signed as face attachment signs them."""
    from twin_core.geometry.face_signature import PlanarFace, part_size_of, signature_of

    out: dict[str, str] = {}
    if not measured:
        return out
    for face in measured.get("faces") or []:
        if isinstance(face, dict) and face.get("face_id") and face.get("signature"):
            out[str(face["face_id"])] = str(face["signature"])[:300]
    planar: list[PlanarFace] = []
    for raw in measured.get("planar_faces") or []:
        try:
            planar.append(PlanarFace.model_validate(raw))
        except ValueError:
            continue
    if planar:
        size = part_size_of(planar, measured.get("bounding_box"))
        for face in planar:
            try:
                out[face.face_id] = signature_of(face, size).summary()[:300]
            except ValueError:
                continue
    return out


async def _slicer_evidence(
    svc: Any,
    *,
    profile: Any,
    ctx: Any,
    measured: dict[str, Any] | None,
    step: bytes | None,
    ref: str,
    direction: tuple[float, float, float],
    enabled: bool | None,
) -> dict[str, Any] | None:
    """The slicer cross-check record for an FDM set, or ``None`` when it does not apply."""
    from api_gateway.twin import dfm_slicer

    if enabled is False or getattr(profile, "id", None) != "fdm":
        return None
    if measured is None or svc is None:
        return dfm_slicer.not_evaluated(
            "no_step",
            "the exact measurement did not run, so the revision was not sliced",
            geometry_ref=ref,
        )
    return await dfm_slicer.cross_check(
        svc.invoke_tool,
        step=step,
        geometry_ref=ref,
        machine=ctx.machine,
        material=ctx.material,
        build_direction=direction,
    )


def _incomplete_reason(text: str) -> str:
    lowered = text.lower()
    if "no machine" in lowered or "machine is unknown" in lowered:
        return "no_machine"
    if "could not run" in lowered:
        return "measure_failed"
    if "measured" in lowered:
        return "not_measured"
    return "other"


def _verdict(fs: DfmFindingSet) -> str:
    if fs.status.value == "analysis_passed":
        return "pass"
    if fs.findings:
        return "fail" if any(f.severity == "blocking" for f in fs.findings) else "warn"
    return "incomplete"


async def run_exact(
    twin: Any,
    project_id: str | UUID,
    *,
    node_id: str,
    trigger: str = "validated",
    build_direction: list[float] | tuple[float, float, float] | None = None,
    service: Any = None,
    slicer_check: bool | None = None,
) -> dict[str, Any]:
    """Measure, evaluate and store the exact set of one validated revision.

    ``build_direction`` given here overrides the context's print orientation.
    ``slicer_check`` ``False`` skips the slicer cross-check; ``None`` runs it
    unless ``METAFORGE_DFM_SLICER_CHECK`` switches it off.
    """
    from api_gateway.twin.dfm_findings import (
        Gathered,
        _annotate_outputs,
        _decisions_for,
        _lock,
        _sets,
        _store,
        set_of,
    )

    pid = _pid(project_id)
    started = time.monotonic()
    with tracer.start_as_current_span("twin.dfm.exact.run") as span:
        item, revision, node, cid, name = await _target(twin, pid, node_id=node_id, item_key=None)
        ref = f"{item.key}@{revision}"
        span.set_attribute("item.ref", ref)
        stored, ctx = await _context(twin, pid, name, cid)
        profile = profile_for_process(ctx.process)
        if profile is None or not profile.exact_checks:
            raise ExactDfmError(
                f"no exact checks apply to {ref} under {ctx.process or 'no process'}"
            )
        # The request's direction wins; else the context's print orientation; else +Z.
        orientation = resolve_orientation(stored, build_direction)
        direction = orientation.direction
        span.set_attribute("dfm.orientation", orientation.origin)
        svc = _service(service)
        measured: dict[str, Any] | None = None
        error: str | None = None
        step: bytes | None = None
        if svc is None:
            error = "the CAD adapter is not available"
        else:
            step = svc.read_step(node)
            if not step:
                error = f"{ref} has no stored STEP"
            else:
                try:
                    measured = await svc.invoke_tool(
                        MEASURE_TOOL,
                        {
                            "step_base64": base64.b64encode(step).decode("ascii"),
                            "build_direction": list(direction),
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - recorded as an incomplete analysis
                    error = str(exc)[:400]
                    span.record_exception(exc)
                    logger.warning("dfm_exact_measure_failed", item_ref=ref, error=error)
        await _emit(
            str(pid),
            name="exact_dfm_measured",
            event_type="milestone",
            sequence=20,
            message=MSG_MEASURED if measured is not None else MSG_FAILED,
        )
        slicer = await _slicer_evidence(
            svc,
            profile=profile,
            ctx=ctx,
            measured=measured,
            step=step,
            ref=ref,
            direction=direction,
            enabled=slicer_check,
        )
        async with _lock(pid):
            existing = await _sets(twin, pid)
            previous: list[VerificationItem] = []
            for other in reversed(existing):
                body = (other.metadata or {}).get("dfm_finding_set") or {}
                if body.get("component_id") == cid and body.get("kind") == "exact":
                    fs_prev = set_of(other)
                    if fs_prev is not None:
                        previous = list(fs_prev.verification_items)
                    break
            tolerances, goal = _context_facts(stored)
            fs = evaluate_exact(
                component_id=cid,
                component_name=name,
                geometry_ref=ref,
                context=ctx,
                profile=profile,
                measured=measured,
                measure_error=error,
                signatures=_signatures(measured),
                decisions=_decisions_for(existing, cid),
                tolerances_stated=tolerances,
                production_goal=goal,
                previous_items=previous,
                build_direction_source=orientation.source,
                build_rotation_deg=orientation.rotation_deg,
            )
            if slicer is not None:
                # Evidence only: the status above is already set and stays.
                fs.exact_evidence["slicer"] = slicer
            gathered = Gathered(
                component_id=cid,
                name=name,
                geometry=ComponentGeometry(
                    component_id=cid, name=name, level="exact", geometry_refs=[ref]
                ),
                pins=[ref],
                output_nodes=[str(node.id)],
            )
            saved, created = await _store(
                twin,
                pid,
                gathered,
                fs,
                context_ref=stored.ref if stored is not None else None,
                trigger=f"exact_{trigger}",
                existing=existing,
                extra={
                    "result": {"verdict": _verdict(fs), "heuristic_only": False},
                    "authority": "exact",
                    "exact_job": {"trigger": trigger, "evaluated_at": fs.evaluated_at},
                },
            )
            if created:
                await _annotate_outputs(twin, gathered, fs, str(saved.id))
                for f in fs.findings:
                    _metric("record_dfm_finding", f.severity, fs.profile_id or "none")
        outcome = (
            "failed"
            if error
            else ("incomplete" if fs.status.value == "analysis_pending" else fs.status.value)
        )
        _metric("record_dfm_exact_evaluation", outcome)
        for n in fs.not_assessed:
            _metric("record_dfm_exact_incomplete", n.rule_id, _incomplete_reason(n.reason))
        span.set_attribute("dfm.status", fs.status.value)
        span.set_attribute("dfm.outcome", outcome)
        logger.info(
            "dfm_exact_stored",
            item_ref=ref,
            component_id=cid,
            status=fs.status.value,
            outcome=outcome,
            findings=len(fs.findings),
            created=created,
            duration_s=round(time.monotonic() - started, 3),
        )
        result = {
            "finding_set_id": str(saved.id),
            "component_id": cid,
            "item_ref": ref,
            "status": fs.status.value,
            "status_label": fs.status_label,
            "note": fs.note,
            "complete": fs.complete,
            "findings": len(fs.findings),
            "not_assessed": [n.rule_id for n in fs.not_assessed],
            "verification_items": len(fs.verification_items),
            "created": created,
        }
        if error:
            logger.warning("dfm_exact_failed", item_ref=ref, error=error)
            raise ExactMeasureFailedError(
                f"{ref}: the exact manufacturing measurement could not run ({error}); the "
                "analysis is recorded as incomplete"
            )
        await _emit(
            str(pid),
            name="dfm_findings_ready",
            event_type="partial_result",
            sequence=40,
            message=MSG_READY,
        )
        return result


# ---------------------------------------------------------------------------
# Physical verification
# ---------------------------------------------------------------------------


async def _find_item(twin: Any, pid: UUID, item_id: str) -> tuple[Any, DfmFindingSet, int]:
    from api_gateway.twin.dfm_findings import _sets, _valid, set_of

    for node in reversed(await _sets(twin, pid)):
        if not _valid(node):
            continue
        fs = set_of(node)
        if fs is None or fs.kind != "exact":
            continue
        for i, v in enumerate(fs.verification_items):
            if v.id == item_id:
                return node, fs, i
    raise ExactDfmError(f"no open verification item {item_id} in this project", 404)


async def _save_set(twin: Any, node: Any, fs: DfmFindingSet) -> None:
    from twin_core.geometry_state import set_node_metadata

    await set_node_metadata(twin, node, dfm_finding_set=fs.model_dump(mode="json"))


def _store_attachment(node_id: str, name: str, content: bytes) -> str | None:
    try:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        return store_work_product_blob(
            node_id, name, content, content_type="application/octet-stream"
        )
    except Exception as exc:  # noqa: BLE001 - the hash still identifies it
        logger.info("dfm_verification_attachment_not_stored", error=str(exc))
        return None


async def record_verification_evidence(
    twin: Any,
    project_id: str | UUID,
    item_id: str,
    *,
    note: str,
    attachment_name: str | None = None,
    attachment_base64: str | None = None,
    actor: str = "",
    workflow: Any = None,
) -> dict[str, Any]:
    """Record a person's evidence for a verification item and ask for approval.

    The item reads ``awaiting_approval`` until a reviewer approves the
    approval this files; nothing here satisfies it.
    """
    pid = _pid(project_id)
    note = (note or "").strip()
    if not note and not attachment_base64:
        raise ExactDfmError("record a note or attach a file as evidence", 422)
    with tracer.start_as_current_span("twin.dfm.verification.record") as span:
        node, fs, index = await _find_item(twin, pid, item_id)
        item = fs.verification_items[index]
        span.set_attribute("dfm.verification", item.rule_id)
        if item.status == "satisfied":
            raise ExactDfmError(f"{item.title} is already satisfied", 409)
        geometry_ref = fs.geometry_refs[0] if fs.geometry_refs else ""
        attachment: dict[str, Any] | None = None
        if attachment_base64:
            try:
                content = base64.b64decode(attachment_base64, validate=True)
            except ValueError as exc:
                raise ExactDfmError("the attachment is not valid base64", 422) from exc
            if not content or len(content) > MAX_ATTACHMENT_BYTES:
                raise ExactDfmError("the attachment is empty or larger than 5 MB", 422)
            filename = (attachment_name or "evidence").strip()[:200] or "evidence"
            attachment = {
                "name": filename,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "object_key": _store_attachment(str(node.id), filename, content),
            }
        evidence_id = "dfme-" + uuid.uuid4().hex[:12]
        if workflow is None:
            from api_gateway.assistant.routes import workflow as default_workflow

            workflow = default_workflow
        change = await workflow.propose_change(
            agent_code="dfm",
            description=(
                f"Physical verification for {fs.component_name or fs.component_id}: "
                f"{item.title}. Evidence: {note or (attachment or {}).get('name', '')}"
            )[:2000],
            diff={
                "action": "accept_dfm_verification",
                "title": f"Accept verification {item.rule_id} for "
                f"{fs.component_name or fs.component_id}",
                "rationale": f"Needed: {item.needed} Evidence: {note}"[:2000],
                "project_id": str(pid),
                "item_id": item.id,
                "evidence_id": evidence_id,
                "geometry_ref": geometry_ref,
            },
            work_products=[node.id],
            project_id=str(pid),
        )
        approval_id = f"change:{change.change_id}"
        item.evidence.append(
            {
                "id": evidence_id,
                "note": note[:2000],
                "attachment": attachment,
                "recorded_by": actor,
                "recorded_at": _now(),
                "geometry_ref": geometry_ref,
                "state": "awaiting_approval",
                "approval_id": approval_id,
            }
        )
        item.status = "awaiting_approval"
        await _save_set(twin, node, fs)
        _metric("record_dfm_decision", "verification_evidence", "recorded")
        logger.info(
            "dfm_verification_evidence_recorded",
            project_id=str(pid),
            item_id=item.id,
            evidence_id=evidence_id,
            approval_id=approval_id,
        )
        return {
            "item": item.model_dump(mode="json"),
            "evidence_id": evidence_id,
            "approval_id": approval_id,
            "requires_approval": True,
            "message": "Evidence recorded. It counts once a reviewer approves it.",
        }


async def accept_verification(
    twin: Any, diff: dict[str, Any], *, reviewer: str, approval_id: str = ""
) -> dict[str, Any]:
    """Mark a verification item satisfied from an approved approval. Called on approval."""
    pid = _pid(str(diff.get("project_id") or ""))
    item_id = str(diff.get("item_id") or "")
    evidence_id = str(diff.get("evidence_id") or "")
    with tracer.start_as_current_span("twin.dfm.verification.accept") as span:
        node, fs, index = await _find_item(twin, pid, item_id)
        item = fs.verification_items[index]
        evidence = next((e for e in item.evidence if e.get("id") == evidence_id), None)
        if evidence is None:
            raise ExactDfmError(f"no evidence {evidence_id} on {item.title}", 404)
        current = fs.geometry_refs[0] if fs.geometry_refs else ""
        if str(evidence.get("geometry_ref") or "") != current:
            evidence["state"] = "outdated"
            await _save_set(twin, node, fs)
            span.set_attribute("dfm.verification.outdated", True)
            return {
                "applied": False,
                "reason": f"the evidence was for {evidence.get('geometry_ref')}, the model is "
                f"now {current}; record new evidence",
            }
        evidence["state"] = "approved"
        evidence["approved_by"] = reviewer
        evidence["approved_at"] = _now()
        item.status = "satisfied"
        item.satisfied_by = {
            "by": reviewer,
            "at": evidence["approved_at"],
            "approval_id": approval_id or evidence.get("approval_id"),
            "evidence_id": evidence_id,
            "geometry_ref": current,
        }
        await _save_set(twin, node, fs)
        _metric("record_dfm_decision", "verification_evidence", "approved")
        logger.info(
            "dfm_verification_satisfied", project_id=str(pid), item_id=item_id, reviewer=reviewer
        )
        return {"applied": True, "item": item.model_dump(mode="json")}


__all__ = [
    "EXACT_JOB_TOOL",
    "ExactDfmError",
    "ExactMeasureFailedError",
    "accept_verification",
    "on_validated",
    "record_verification_evidence",
    "register",
    "run_exact",
    "schedule",
    "schedule_for_component",
]
