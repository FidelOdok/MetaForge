"""Sending a job to a machine and recording how it went (FORGE-451 step 4).

Three operations, each a separate MCP tool so its annotation is honest:

``dispatch_job``
    Send a G-code work product to an idle in-house printer and start it.
    A physical action: the tool is destructive and held for the person on
    every call. The part's revision is pinned at dispatch, so the result is
    evidence about *that* revision even if the design moves on meanwhile.

``job_status``
    Ask the machine about the job. Reads only.

``record_job_result``
    Once the job has ended, record the outcome as ``demonstration`` evidence
    against the pinned part revision. The outcome is read from the machine
    here, never taken from the caller, so it cannot be asserted. Held, like
    every twin write.

MetaForge has no slicer: the work product must already be G-code.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import structlog

from api_gateway.library.connectors import TERMINAL_STATES, ConnectorError, connector_for
from api_gateway.library.routes import get_machine_library
from observability.tracing import get_tracer
from twin_core.library.machines import MachineInstance, MachineNotFoundError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.jobs")

GCODE_SUFFIXES = (".gcode", ".gco", ".g", ".bgcode")

__all__ = ["JobError", "dispatch_job", "job_status", "record_job_result"]


class JobError(ValueError):
    """The job cannot go ahead; the message says why and what to do."""


def _instance(machine_id: str) -> MachineInstance:
    try:
        machine = get_machine_library().get(machine_id)
    except MachineNotFoundError as exc:
        raise JobError(f"machine '{machine_id}' not found") from exc
    if not isinstance(machine, MachineInstance):
        raise JobError(f"{machine_id} is a machine model; send jobs to a machine instance")
    return machine


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


async def dispatch_job(
    machine_id: str,
    work_product_id: str,
    *,
    by: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    from api_gateway.twin.blob_store import resolve_work_product_blob
    from twin_core.consistency.staleness import work_product_current_revision

    machine = _instance(machine_id)
    if machine.route != "in_house":
        raise JobError(f"{machine.name} is a vendor machine; MetaForge sends jobs only in-house")
    if machine.status != "available":
        raise JobError(f"{machine.name} is {machine.status} in the library, not available")
    twin = _twin()
    try:
        wp_uuid = UUID(work_product_id)
    except ValueError as exc:
        raise JobError(f"work_product_id '{work_product_id}' is not a work product id") from exc
    wp = await twin.get_work_product(wp_uuid)
    if wp is None:
        raise JobError(f"work product {work_product_id} not found")
    try:
        content, filename = resolve_work_product_blob(wp)
    except Exception as exc:  # HTTPException from the blob layer: no file behind it
        raise JobError(f"work product {work_product_id} has no file to send ({exc})") from exc
    if not filename.lower().endswith(GCODE_SUFFIXES):
        raise JobError(
            f"{filename} is not G-code. MetaForge has no slicer: slice the part for "
            f"{machine.name} and record the G-code as a work product, then send that."
        )
    revision = await work_product_current_revision(twin, wp_uuid)
    project_id = getattr(wp, "project_id", None) or (wp.metadata or {}).get("project_id")
    with tracer.start_as_current_span("library.dispatch_job") as span:
        span.set_attribute("machine.id", machine_id)
        span.set_attribute("work_product.id", work_product_id)
        try:
            connector = connector_for(machine.connection, client)
            health = await connector.health()
            if health["state"] != "idle":
                raise JobError(f"{machine.name} is {health['state']}, not idle; nothing was sent")
            started = await connector.start(content, filename)
        except ConnectorError as exc:
            span.record_exception(exc)
            logger.warning("machine_job_not_sent", machine_id=machine_id, kind=exc.kind)
            raise JobError(f"{exc.kind}: {exc}") from exc
    job = get_machine_library().create_job(
        {
            "machine_id": machine_id,
            "machine": machine.name,
            "protocol": machine.connection.get("protocol"),
            "work_product_id": work_product_id,
            "part_revision": int(revision),
            "project_id": str(project_id) if project_id else None,
            "filename": filename,
            "remote_path": started.get("remote_path"),
            "state": "printing",
            "by": by,
            "evidence_id": None,
        }
    )
    logger.info(
        "machine_job_dispatched", job_id=job["id"], machine_id=machine_id, filename=filename, by=by
    )
    return {
        "job": job,
        "next_step": "Poll library.job_status; when it has ended, library.record_job_result.",
    }


def _ours(job: dict[str, Any], remote: dict[str, Any]) -> bool:
    name = str(remote.get("filename") or "")
    wanted = {str(job.get("filename") or ""), str(job.get("remote_path") or "")}
    return bool(name) and (name in wanted or name.rsplit("/", 1)[-1] in wanted)


async def job_status(job_id: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    try:
        job = get_machine_library().get_job(job_id)
    except MachineNotFoundError as exc:
        raise JobError(f"job '{job_id}' not found") from exc
    machine = _instance(job["machine_id"])
    try:
        remote = await connector_for(machine.connection, client).status()
    except ConnectorError as exc:
        return {"job": job, "machine_state": "unknown", "reason": f"{exc.kind}: {exc}"}
    if not _ours(job, remote):
        # Another file is on the machine now: ours ended before this poll.
        return {
            "job": job,
            "machine_state": remote["state"],
            "job_state": "unknown",
            "reason": f"the machine now shows {remote.get('filename') or 'no file'}",
        }
    return {
        "job": job,
        "machine_state": remote["state"],
        "job_state": remote["state"],
        "progress": remote.get("progress"),
        "ended": remote["state"] in TERMINAL_STATES,
    }


async def record_job_result(
    job_id: str, *, by: str = "", client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.twin.evidence_recorder import make_evidence_recorder

    library = get_machine_library()
    try:
        job = library.get_job(job_id)
    except MachineNotFoundError as exc:
        raise JobError(f"job '{job_id}' not found") from exc
    if job.get("evidence_id"):
        return {"job": job, "evidence": {"node_id": job["evidence_id"]}, "already_recorded": True}
    status = await job_status(job_id, client=client)
    state = status.get("job_state")
    if state not in TERMINAL_STATES:
        raise JobError(
            f"job {job_id} has not ended (the machine reads {status.get('machine_state')}"
            f"{', ' + status['reason'] if status.get('reason') else ''}); "
            "record its result once it has"
        )
    result = {
        "outcome": state,
        "progress": status.get("progress"),
        "machine_id": job["machine_id"],
        "machine": job["machine"],
        "filename": job["filename"],
        "job_id": job_id,
    }
    recorder = make_evidence_recorder(_twin(), get_project_backend())
    with tracer.start_as_current_span("library.record_job_result") as span:
        span.set_attribute("job.id", job_id)
        span.set_attribute("job.outcome", str(state))
        evidence = await recorder(
            evidence_type="demonstration",
            producer={"tool": "library.record_job_result", "protocol": job.get("protocol")},
            inputs={
                "work_product_id": job["work_product_id"],
                "filename": job["filename"],
                "dispatched_at": job["created_at"],
            },
            result=result,
            statement=(
                f"{job['filename']} on {job['machine']}: {state}"
                + (" (the part was made)" if state == "complete" else " (the part was not made)")
            ),
            valid_against=[
                {
                    "ref": job["work_product_id"],
                    "entity_kind": "work_product",
                    "revision": job["part_revision"],
                }
            ],
            project_id=job.get("project_id"),
        )
    job = library.update_job(
        job_id, {"state": state, "evidence_id": evidence.get("node_id"), "recorded_by": by}
    )
    logger.info("machine_job_recorded", job_id=job_id, outcome=state, by=by)
    return {"job": job, "evidence": evidence}
