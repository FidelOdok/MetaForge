"""``/v1/library/machines``: the machines a person can make parts on (FORGE-451).

Two layers. A *model* is what a machine type can do (a Creality CR-10: build
volume, nozzle, materials), each capability carrying its source. An
*instance* is one machine somebody owns or a vendor runs ("Workshop CR-10"),
optionally pointing at a model and overriding any capability it has measured.

The store is the gateway's, so the dashboard, the design-flow worker and the
MCP sidecar (through ``metaforge.mcp.remote_library``) all read the same
library. Over MCP the writes are held for approval like any other write; here
the caller is already a person or a service acting for one.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.library.machines import (
    MachineLibraryError,
    MachineNotFoundError,
    SqliteMachineLibrary,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.routes")

router = APIRouter(prefix="/v1/library", tags=["library"])

AGENT_HEADER = "X-MetaForge-Agent"

_library: SqliteMachineLibrary | None = None


def get_machine_library() -> SqliteMachineLibrary:
    """The process's library; an in-memory one until the lifespan installs the file."""
    global _library  # noqa: PLW0603
    if _library is None:
        _library = SqliteMachineLibrary()
    return _library


def init_machine_library(library: SqliteMachineLibrary | None) -> None:
    """Install ``library`` (the lifespan), or ``None`` to start over (tests)."""
    global _library  # noqa: PLW0603
    _library = library


class DiscoverMachineRequest(BaseModel):
    connection: dict[str, Any]
    name: str = ""


class UpdateMachineRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    by: str = ""


def _by(request: Request, named: str) -> str:
    return (named or request.headers.get(AGENT_HEADER) or "").strip()[:100] or "unknown"


def _not_found(machine_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"machine '{machine_id}' not found")


@router.get("/machines")
async def list_machines(layer: str | None = None, kind: str | None = None) -> dict[str, Any]:
    """Models and instances; ``layer=model|instance`` and ``kind`` narrow it."""
    machines = get_machine_library().list(layer=layer or None, kind=kind or None)
    return {"machines": [m.as_dict() for m in machines]}


@router.get("/machines/{machine_id}")
async def get_machine(machine_id: str) -> dict[str, Any]:
    library = get_machine_library()
    try:
        machine = library.get(machine_id)
    except MachineNotFoundError as exc:
        raise _not_found(machine_id) from exc
    return {"machine": machine.as_dict(), "history": library.history(machine_id)}


@router.get("/machines/{machine_id}/limits")
async def machine_limits(machine_id: str) -> dict[str, Any]:
    """An instance's limits, shaped for the ranking and DFM skills."""
    try:
        return get_machine_library().limits(machine_id)
    except MachineNotFoundError as exc:
        raise _not_found(machine_id) from exc
    except MachineLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/machines", status_code=201)
async def register_machine(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Add a model (``layer: model``) or an instance (``layer: instance``)."""
    by = _by(request, str(body.pop("by", "") or ""))
    with tracer.start_as_current_span("library.register_machine") as span:
        try:
            machine = get_machine_library().register(body, by=by)
        except MachineLibraryError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        span.set_attribute("machine.id", machine.id)
        span.set_attribute("machine.kind", machine.kind)
    logger.info("machine_registered", machine_id=machine.id, kind=machine.kind, by=by)
    return {"machine": machine.as_dict()}


@router.patch("/machines/{machine_id}")
async def update_machine(
    machine_id: str, body: UpdateMachineRequest, request: Request
) -> dict[str, Any]:
    """Change a machine; the previous version is kept in its history."""
    by = _by(request, body.by)
    with tracer.start_as_current_span("library.update_machine") as span:
        span.set_attribute("machine.id", machine_id)
        try:
            machine = get_machine_library().update(machine_id, body.changes, by=by)
        except MachineNotFoundError as exc:
            raise _not_found(machine_id) from exc
        except MachineLibraryError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("machine_updated", machine_id=machine_id, version=machine.version, by=by)
    return {"machine": machine.as_dict()}


@router.post("/discover")
async def discover_machine(body: DiscoverMachineRequest) -> dict[str, Any]:
    """Read a printer's controller into a proposed instance. Registers nothing."""
    from api_gateway.library.discovery import DiscoveryError
    from api_gateway.library.discovery import discover_machine as discover

    try:
        return await discover(body.connection, name=body.name)
    except DiscoveryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DispatchJobRequest(BaseModel):
    machine_id: str
    work_product_id: str
    by: str = ""


class RecordJobRequest(BaseModel):
    by: str = ""


@router.post("/jobs", status_code=201)
async def dispatch_job(body: DispatchJobRequest, request: Request) -> dict[str, Any]:
    """Send a G-code work product to an idle in-house machine and start it."""
    from api_gateway.library.jobs import JobError
    from api_gateway.library.jobs import dispatch_job as dispatch

    try:
        return await dispatch(body.machine_id, body.work_product_id, by=_by(request, body.by))
    except JobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
async def list_jobs(machine_id: str | None = None) -> dict[str, Any]:
    return {"jobs": get_machine_library().list_jobs(machine_id)}


@router.get("/jobs/{job_id}/status")
async def job_status(job_id: str) -> dict[str, Any]:
    """The job as the machine reports it now. Writes nothing."""
    from api_gateway.library.jobs import JobError
    from api_gateway.library.jobs import job_status as status

    try:
        return await status(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/result")
async def record_job_result(
    job_id: str, body: RecordJobRequest, request: Request
) -> dict[str, Any]:
    """Record an ended job as evidence against the part revision it was sent for."""
    from api_gateway.library.jobs import JobError
    from api_gateway.library.jobs import record_job_result as record

    try:
        return await record(job_id, by=_by(request, body.by))
    except JobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
