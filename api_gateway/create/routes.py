"""``/v1/create``: the Create view's brief, route, inspection plan and builds.

The project, its conversation (``/v1/chat``), its work products (``/v1/twin``)
and its decisions (``/v1/approvals``) are read where they already live; these
routes keep what the Create journey adds, and say where a creation stands
(``readiness``).

Four commitments are a person's, never an agent's: releasing a build for
making, accepting or rejecting the result, passing a check whose reviewer is
a person, and (through the project) deleting it. An agent caller
(``X-MetaForge-Surface: agent``) gets 403 for those. Releasing a build does
not start a machine: dispatch stays with ``/v1/library/jobs`` and its own
approval, so a saved machine connection is never taken as a machine ready
to run.

Every write may name ``expected_version``; when the record has moved on since,
the write is refused with 409 and the current record, so an edit made in the
Engineering view is never silently overwritten from Create.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.create.models import (
    BRIEF_KEYS,
    Brief,
    Build,
    BuildStatus,
    CheckResult,
    Creation,
    Decision,
    Estimate,
    Evidence,
    EvidenceKind,
    HistoryEntry,
    InspectionCheck,
    Release,
    ResultStatus,
    Revision,
    RouteChoice,
    now_iso,
)
from api_gateway.create.readiness import Readiness, WorkProductSummary, derive
from api_gateway.create.store import CreationConflictError, SqliteCreationStore
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.create.routes")

router = APIRouter(prefix="/v1/create", tags=["create"])

SURFACE_HEADER = "X-MetaForge-Surface"

_store: SqliteCreationStore | None = None


def get_create_store() -> SqliteCreationStore:
    """The process's store; an in-memory one until the lifespan installs the file."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SqliteCreationStore()
    return _store


def init_create_store(store: SqliteCreationStore | None) -> None:
    """Install ``store`` (the lifespan), or ``None`` to start over (tests)."""
    global _store  # noqa: PLW0603
    _store = store


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class CreationView(BaseModel):
    creation: Creation
    readiness: Readiness
    brief_keys: list[str] = Field(default_factory=lambda: list(BRIEF_KEYS))


class VersionedWrite(BaseModel):
    expected_version: int | None = Field(
        None, description="The version this edit was made against; 409 if it has moved on"
    )
    summary: str = Field("", max_length=500, description="One line for the history")


class BriefUpdate(VersionedWrite):
    brief: Brief


class RouteUpdate(VersionedWrite):
    route: RouteChoice


class PlanUpdate(VersionedWrite):
    checks: list[InspectionCheck] = Field(default_factory=list, max_length=100)


class BuildCreate(VersionedWrite):
    revision: Revision
    process: str | None = None
    machine_id: str | None = None
    material: str | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    estimates: dict[str, Estimate] = Field(default_factory=dict)


class BuildEdit(VersionedWrite):
    revision: Revision | None = None
    process: str | None = None
    machine_id: str | None = None
    material: str | None = None
    settings: dict[str, str] | None = None
    estimates: dict[str, Estimate] | None = None


class StatusUpdate(VersionedWrite):
    status: BuildStatus
    note: str = Field("", max_length=1000)


class EvidenceCreate(VersionedWrite):
    kind: EvidenceKind
    check_id: str | None = None
    work_product_id: str | None = None
    upload_id: str | None = None
    value: float | None = None
    unit: str | None = Field(None, max_length=16)
    note: str = Field("", max_length=2000)


class ResultUpdate(VersionedWrite):
    status: ResultStatus
    note: str = Field("", max_length=2000)
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class AcceptRequest(VersionedWrite):
    decision: str = Field(..., pattern="^(accept|reject)$")
    reason: str = Field("", max_length=2000)
    limitations: list[str] = Field(default_factory=list)


class DuplicateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class DuplicateResponse(BaseModel):
    project_id: str
    name: str


# Allowed build status moves. "preparing" leaves only by release (a person's
# decision) or by being cancelled.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "preparing": frozenset({"cancelled"}),
    "released": frozenset({"making", "completed", "failed", "cancelled"}),
    "making": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _surface(request: Request) -> str:
    return (request.headers.get(SURFACE_HEADER) or "").strip().lower() or "unknown"


def _require_person(request: Request, what: str) -> None:
    if _surface(request) == "agent":
        raise HTTPException(
            status_code=403, detail=f"{what} is a person's decision, not an agent's"
        )


async def _project_or_404(project_id: str) -> Any:
    from api_gateway.projects.routes import get_project_backend

    project = await get_project_backend().get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _pending_approvals(request: Request, project_id: str) -> int:
    # Best effort: readiness without the count is still right about
    # everything else, so a failing approvals source never blocks a read.
    try:
        from api_gateway.approvals.service import list_items

        items = await list_items(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_pending_approvals_unavailable", error=str(exc))
        return 0
    return sum(1 for i in items if i.status == "pending" and i.project_id == project_id)


async def _view(request: Request, creation: Creation) -> CreationView:
    project = await _project_or_404(creation.project_id)
    wps = [
        WorkProductSummary(id=w.id, name=w.name, type=w.type, status=w.status)
        for w in project.work_products
    ]
    pending = await _pending_approvals(request, creation.project_id)
    return CreationView(
        creation=creation, readiness=derive(creation, wps, pending_approvals=pending)
    )


def _log(request: Request, creation: Creation, change: str, summary: str) -> None:
    approver = approver_from_request(request)
    creation.history.append(
        HistoryEntry(
            actor=str(approver.actor_id), surface=_surface(request), change=change, summary=summary
        )
    )
    # A long-lived creation keeps its recent story, not every keystroke.
    del creation.history[:-500]


async def _write(
    request: Request,
    project_id: str,
    body: VersionedWrite,
    change: str,
    mutate: Any,
) -> CreationView:
    """Load, change a copy, and save it as the next version."""
    await _project_or_404(project_id)
    store = get_create_store()
    with tracer.start_as_current_span(f"create.{change}") as span:
        span.set_attribute("project.id", project_id)
        current = store.get(project_id)
        if body.expected_version is not None and body.expected_version != current.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This creation changed since you loaded it; "
                        "review the latest and try again."
                    ),
                    "current": current.model_dump(mode="json"),
                },
            )
        draft = current.model_copy(deep=True)
        summary = mutate(draft) or body.summary
        _log(request, draft, change, summary or body.summary)
        try:
            saved = store.save(draft, expected_version=current.version)
        except CreationConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "This creation changed while saving; try again.",
                    "current": exc.current.model_dump(mode="json"),
                },
            ) from exc
        logger.info("create_write", project_id=project_id, change=change, version=saved.version)
        return await _view(request, saved)


def _build_or_404(creation: Creation, build_id: str) -> Build:
    try:
        return creation.build(build_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"build '{build_id}' not found") from exc


def _unconfirmed_critical(creation: Creation) -> list[str]:
    return [
        m.label for m in creation.brief.measurements if m.critical and m.status != "user_confirmed"
    ]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{project_id}", response_model=CreationView)
async def get_creation(project_id: str, request: Request) -> CreationView:
    """The creation's records and where it stands."""
    with tracer.start_as_current_span("create.get") as span:
        span.set_attribute("project.id", project_id)
        return await _view(request, get_create_store().get(project_id))


@router.put("/{project_id}/brief", response_model=CreationView)
async def put_brief(project_id: str, body: BriefUpdate, request: Request) -> CreationView:
    """Replace the brief: items, measurements, assumptions and references."""
    unknown = sorted(set(body.brief.items) - set(BRIEF_KEYS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown brief items: {', '.join(unknown)}")

    def mutate(c: Creation) -> str:
        before = c.brief
        c.brief = body.brief
        changed = [k for k in BRIEF_KEYS if before.items.get(k) != body.brief.items.get(k)]
        if before.measurements != body.brief.measurements:
            changed.append("measurements")
        if before.assumptions != body.brief.assumptions:
            changed.append("assumptions")
        if before.references != body.brief.references:
            changed.append("references")
        note = f"Brief changed: {', '.join(changed) or 'nothing'}"
        if c.active_builds and changed:
            note += f"; build {c.active_builds[-1].number} keeps the revision it was released with"
        return note

    return await _write(request, project_id, body, "brief", mutate)


@router.put("/{project_id}/route", response_model=CreationView)
async def put_route(project_id: str, body: RouteUpdate, request: Request) -> CreationView:
    """Choose how it will be made: at home, in a workshop or by a manufacturer."""

    def mutate(c: Creation) -> str:
        c.route = body.route
        stale = [b for b in c.builds if b.status == "preparing" and b.route != body.route.route]
        for b in stale:
            # A prepared build for another route must be prepared again.
            b.status = "cancelled"
            b.status_note = "Route changed before release"
            b.updated_at = now_iso()
        return f"Route set to {body.route.route or 'none'}" + (
            f"; {len(stale)} prepared build(s) cancelled" if stale else ""
        )

    return await _write(request, project_id, body, "route", mutate)


@router.put("/{project_id}/inspection-plan", response_model=CreationView)
async def put_inspection_plan(project_id: str, body: PlanUpdate, request: Request) -> CreationView:
    """Replace the inspection plan. A released build keeps the checks it was released with."""
    ids = [c.id for c in body.checks]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="inspection check ids must be unique")

    def mutate(c: Creation) -> str:
        c.inspection_plan = body.checks
        return f"Inspection plan: {len(body.checks)} check(s)"

    return await _write(request, project_id, body, "inspection_plan", mutate)


@router.post("/{project_id}/builds", response_model=CreationView, status_code=201)
async def create_build(project_id: str, body: BuildCreate, request: Request) -> CreationView:
    """Prepare a manufacturing attempt of one exact revision."""

    def mutate(c: Creation) -> str:
        if c.route.route is None:
            raise HTTPException(
                status_code=409, detail="Choose how it will be made before preparing a build"
            )
        number = max((b.number for b in c.builds), default=0) + 1
        c.builds.append(
            Build(
                id=uuid.uuid4().hex[:12],
                number=number,
                revision=body.revision,
                route=c.route.route,
                process=body.process if body.process is not None else c.route.process,
                machine_id=body.machine_id if body.machine_id is not None else c.route.machine_id,
                material=body.material if body.material is not None else c.route.material,
                settings=body.settings,
                estimates=body.estimates,
                checks=list(c.inspection_plan),
            )
        )
        return (
            f"Build {number} prepared from {body.revision.label or body.revision.work_product_id}"
        )

    return await _write(request, project_id, body, "build_prepare", mutate)


@router.patch("/{project_id}/builds/{build_id}", response_model=CreationView)
async def edit_build(
    project_id: str, build_id: str, body: BuildEdit, request: Request
) -> CreationView:
    """Change a prepared build. A released one keeps its instructions: prepare a new one."""

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        if b.status != "preparing":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Build {b.number} is {b.status}; its instructions are not changed. "
                    "Prepare a new build instead."
                ),
            )
        for field in ("revision", "process", "machine_id", "material", "settings", "estimates"):
            value = getattr(body, field)
            if value is not None:
                setattr(b, field, value)
        b.updated_at = now_iso()
        return f"Build {b.number} edited"

    return await _write(request, project_id, body, "build_edit", mutate)


@router.post("/{project_id}/builds/{build_id}/release", response_model=CreationView)
async def release_build(
    project_id: str, build_id: str, body: VersionedWrite, request: Request
) -> CreationView:
    """A person releases this exact revision for making; starting a machine is separate."""
    _require_person(request, "Releasing a build for making")
    approver = approver_from_request(request)

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        if b.status != "preparing":
            raise HTTPException(
                status_code=409, detail=f"Build {b.number} is {b.status}, not awaiting release"
            )
        if not c.inspection_plan:
            raise HTTPException(
                status_code=409,
                detail="Add an inspection plan first: its checks decide if the build is accepted",
            )
        critical = _unconfirmed_critical(c)
        if critical:
            raise HTTPException(
                status_code=409,
                detail=f"Confirm these critical dimensions first: {', '.join(critical)}",
            )
        b.checks = list(c.inspection_plan)
        b.results = {chk.id: CheckResult() for chk in b.checks}
        b.status = "released"
        b.release = Release(
            approver=str(approver.actor_id),
            approver_verified=approver.verified,
            surface=_surface(request),
        )
        b.updated_at = now_iso()
        return f"Build {b.number} released for making by {approver.actor_id}"

    return await _write(request, project_id, body, "build_release", mutate)


@router.post("/{project_id}/builds/{build_id}/status", response_model=CreationView)
async def set_build_status(
    project_id: str, build_id: str, body: StatusUpdate, request: Request
) -> CreationView:
    """Record that a released build started, finished, failed or was cancelled."""

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        if body.status not in _TRANSITIONS[b.status]:
            raise HTTPException(
                status_code=409,
                detail=f"Build {b.number} cannot go from {b.status} to {body.status}",
            )
        b.status = body.status
        b.status_note = body.note
        b.updated_at = now_iso()
        return f"Build {b.number} {body.status}" + (f": {body.note}" if body.note else "")

    return await _write(request, project_id, body, "build_status", mutate)


@router.post(
    "/{project_id}/builds/{build_id}/evidence", response_model=CreationView, status_code=201
)
async def add_evidence(
    project_id: str, build_id: str, body: EvidenceCreate, request: Request
) -> CreationView:
    """Attach a photo, measurement, telemetry reading or note to a build."""
    approver = approver_from_request(request)

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        if b.status == "preparing":
            raise HTTPException(
                status_code=409, detail="Release the build before recording evidence for it"
            )
        if body.check_id and body.check_id not in {chk.id for chk in b.checks}:
            raise HTTPException(
                status_code=422, detail=f"build {b.number} has no check '{body.check_id}'"
            )
        ev = Evidence(
            id=uuid.uuid4().hex[:12],
            kind=body.kind,
            check_id=body.check_id,
            work_product_id=body.work_product_id,
            upload_id=body.upload_id,
            value=body.value,
            unit=body.unit,
            note=body.note,
            recorded_by=str(approver.actor_id),
            surface=_surface(request),
        )
        b.evidence.append(ev)
        if body.check_id:
            result = b.results.setdefault(body.check_id, CheckResult())
            result.evidence_ids.append(ev.id)
        b.updated_at = now_iso()
        return f"Evidence ({body.kind}) added to build {b.number}"

    return await _write(request, project_id, body, "build_evidence", mutate)


@router.put("/{project_id}/builds/{build_id}/results/{check_id}", response_model=CreationView)
async def set_result(
    project_id: str, build_id: str, check_id: str, body: ResultUpdate, request: Request
) -> CreationView:
    """Record one check's outcome. A check a person reviews cannot be passed by an agent."""
    approver = approver_from_request(request)
    surface = _surface(request)

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        check = next((chk for chk in b.checks if chk.id == check_id), None)
        if check is None:
            raise HTTPException(
                status_code=404, detail=f"build {b.number} has no check '{check_id}'"
            )
        if surface == "agent" and check.reviewer == "person" and body.status == "passed":
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{check.feature}' is reviewed by a person; "
                    "an agent may report a finding, not pass it"
                ),
            )
        previous = b.results.get(check_id) or CheckResult()
        b.results[check_id] = CheckResult(
            status=body.status,
            note=body.note,
            confidence=body.confidence,
            evidence_ids=sorted(set(previous.evidence_ids) | set(body.evidence_ids)),
            decided_by=str(approver.actor_id),
            surface=surface,
        )
        b.updated_at = now_iso()
        return f"{check.feature}: {body.status.replace('_', ' ')} (build {b.number})"

    return await _write(request, project_id, body, "build_result", mutate)


@router.post("/{project_id}/builds/{build_id}/accept", response_model=CreationView)
async def decide_build(
    project_id: str, build_id: str, body: AcceptRequest, request: Request
) -> CreationView:
    """A person accepts or rejects a finished build. Completion is not acceptance."""
    _require_person(request, "Accepting a build")
    approver = approver_from_request(request)

    def mutate(c: Creation) -> str:
        b = _build_or_404(c, build_id)
        if b.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Build {b.number} is {b.status}; only a completed build is decided",
            )
        if b.acceptance is not None:
            raise HTTPException(
                status_code=409, detail=f"Build {b.number} was already {b.acceptance.decision}ed"
            )
        limitations = list(body.limitations)
        if body.decision == "accept":
            unmet = [
                chk.feature
                for chk in b.checks
                if chk.blocking and (b.results.get(chk.id) or CheckResult()).status != "passed"
            ]
            if unmet:
                raise HTTPException(
                    status_code=409,
                    detail=f"These required checks have not passed: {', '.join(unmet)}",
                )
            # Unresolved non-blocking checks stay on the record as limitations.
            for chk in b.checks:
                status = (b.results.get(chk.id) or CheckResult()).status
                if not chk.blocking and status != "passed":
                    limitations.append(f"{chk.feature}: {status.replace('_', ' ')}")
        b.acceptance = Decision(
            decision="accept" if body.decision == "accept" else "reject",
            reason=body.reason,
            limitations=limitations,
            approver=str(approver.actor_id),
            approver_verified=approver.verified,
            surface=_surface(request),
        )
        b.updated_at = now_iso()
        return f"Build {b.number} {body.decision}ed by {approver.actor_id}"

    return await _write(request, project_id, body, "build_decision", mutate)


@router.post("/{project_id}/duplicate", response_model=DuplicateResponse, status_code=201)
async def duplicate_creation(
    project_id: str, body: DuplicateRequest, request: Request
) -> DuplicateResponse:
    """A new project with this creation's brief, route and inspection plan; no builds."""
    from api_gateway.projects.routes import get_project_backend

    source = await _project_or_404(project_id)
    try:
        project = await get_project_backend().create_project(
            name=body.name, description=source.description, status="draft"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    original = get_create_store().get(project_id)
    copy = Creation(
        project_id=project.id,
        brief=original.brief.model_copy(deep=True),
        route=original.route.model_copy(deep=True),
        inspection_plan=[c.model_copy() for c in original.inspection_plan],
    )
    _log(request, copy, "duplicate", f"Duplicated from {source.name}")
    get_create_store().save(copy, expected_version=None)
    logger.info("create_duplicated", source=project_id, project_id=project.id)
    return DuplicateResponse(project_id=project.id, name=project.name)


@router.delete("/{project_id}", status_code=204)
async def delete_creation(project_id: str) -> None:
    """Remove the Create records. Refused while a build is released or being made."""
    store = get_create_store()
    creation = store.get(project_id)
    if creation.active_builds:
        numbers = ", ".join(str(b.number) for b in creation.active_builds)
        raise HTTPException(
            status_code=409,
            detail=(
                f"Build {numbers} is still released or being made; "
                "finish or cancel it first. Nothing was deleted."
            ),
        )
    store.delete(project_id)
    logger.info("create_deleted", project_id=project_id)
