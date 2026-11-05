"""DFM findings routes (C3D slice 4).

Under ``/v1/twin/projects/{project_id}/dfm``:

- ``GET``: the current heuristic findings by component, each with its status,
  rule profile and version, anchors, measured against limit, evidence and
  staleness, plus the product status and the open suggestion count the Create
  badge shows. ``?history=true`` adds earlier (stale or superseded) sets.
- ``POST .../evaluate``: run the heuristics now for every component (or the
  ones named) and store a finding set per component. The same inputs reuse
  the current set.
- ``POST .../findings/{finding_id}/decision``: ``keep_design``, ``try_fix``
  (a proposal; a fix that would change a protected requirement waits for a
  person's approval) or ``explore_process`` (a preview under the other
  profiles). Nothing here changes the design.

The logic is in :mod:`api_gateway.twin.dfm_findings`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api_gateway.twin.dfm_findings import DfmError, DfmView, decide, dfm_view, evaluate_project
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.dfm_routes")

router = APIRouter(prefix="/v1/twin", tags=["twin"])

_BASE = "/projects/{project_id}/dfm"

T = TypeVar("T")


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


async def _call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Run one call, mapping refusals to 4xx and anything else to 502."""
    from uuid import UUID

    try:
        UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id format") from None
    with tracer.start_as_current_span(f"twin.dfm_routes.{name}") as span:
        span.set_attribute("project_id", project_id)
        try:
            return await fn()
        except DfmError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.warning("dfm_route_failed", route=name, project_id=project_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail="The manufacturing guidance could not be read or checked"
            ) from exc


class DfmEvaluateRequest(BaseModel):
    """Which components to check; all when left out."""

    component_ids: list[str] | None = Field(None, max_length=100)


class DfmDecisionRequest(BaseModel):
    decision: Literal["keep_design", "try_fix", "explore_process"]
    reason: str = Field("", max_length=500)


def _actor(request: Request) -> str:
    from api_gateway.auth.approver import approver_from_request

    return str(approver_from_request(request).actor_id)


@router.get(_BASE, response_model=DfmView)
async def get_dfm(
    project_id: str, history: bool = Query(False, description="Include stale and superseded sets")
) -> DfmView:
    """The project's current DFM findings by component, status and profile versions."""
    return await _call(
        "get", project_id, lambda: dfm_view(_twin(), project_id, include_history=history)
    )


@router.post(f"{_BASE}/evaluate", response_model=DfmView)
async def evaluate_dfm(project_id: str, body: DfmEvaluateRequest | None = None) -> DfmView:
    """Run the heuristic DFM rules now and return the updated findings.

    Preliminary guidance only: a component's status is ``issues_found``,
    ``preliminary_guidance``, ``analysis_pending`` or ``not_assessed``, never
    an analysis pass.
    """

    async def run() -> DfmView:
        twin = _twin()
        await evaluate_project(
            twin,
            project_id,
            component_ids=body.component_ids if body is not None else None,
            trigger="manual",
        )
        return await dfm_view(twin, project_id)

    return await _call("evaluate", project_id, run)


@router.post(f"{_BASE}/findings/{{finding_id}}/decision")
async def decide_finding(
    project_id: str, finding_id: str, body: DfmDecisionRequest, request: Request
) -> dict[str, Any]:
    """Keep the design, try the suggested fix, or explore another process."""
    actor = _actor(request)
    return await _call(
        "decision",
        project_id,
        lambda: decide(
            _twin(), project_id, finding_id, body.decision, actor=actor, reason=body.reason
        ),
    )


# ---------------------------------------------------------------------------
# C3D slice 6: exact checks, verification evidence, report and checklist
# ---------------------------------------------------------------------------


class DfmExactRequest(BaseModel):
    """Which validated revision to check exactly: a CAD item (``KEY`` or ``KEY@n``) or a node."""

    item_key: str | None = Field(None, max_length=200)
    node_id: str | None = Field(None, max_length=80)
    #: The print's up direction in model coordinates; +Z when left out.
    build_direction: list[float] | None = Field(None, min_length=3, max_length=3)
    #: Run now and return the result instead of starting a job.
    wait: bool = False


class VerificationEvidenceRequest(BaseModel):
    """A person's evidence for a verification item: a note, an upload, or both."""

    note: str = Field("", max_length=2000)
    attachment_name: str | None = Field(None, max_length=200)
    #: The file, base64, up to 5 MB.
    attachment_base64: str | None = Field(None, max_length=7_000_000)


async def _exact_call(name: str, project_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    from api_gateway.twin.dfm_exact import ExactDfmError, ExactMeasureFailedError
    from api_gateway.twin.dfm_report import DfmReportError

    async def run() -> T:
        try:
            return await fn()
        except (ExactDfmError, DfmReportError) as exc:
            raise DfmError(str(exc), exc.status) from exc
        except ExactMeasureFailedError as exc:
            raise DfmError(str(exc), 502) from exc

    return await _call(name, project_id, run)


@router.post(f"{_BASE}/exact")
async def run_exact_dfm(project_id: str, body: DfmExactRequest, request: Request) -> dict[str, Any]:
    """Schedule the exact checks for a validated CAD revision (or run them now with ``wait``).

    Refused (409) for a revision that is not validated; ``scheduled: false``
    with the reason when the component's process has no exact checks.
    """
    from api_gateway.twin.dfm_exact import schedule

    if not body.item_key and not body.node_id:
        raise HTTPException(status_code=422, detail="name an item_key or a node_id")
    actor = _actor(request)
    return await _exact_call(
        "exact",
        project_id,
        lambda: schedule(
            _twin(),
            project_id,
            item_key=body.item_key,
            node_id=body.node_id,
            trigger="manual",
            build_direction=body.build_direction,
            session_id=actor or "dfm",
            wait=body.wait,
        ),
    )


@router.post(f"{_BASE}/verification/{{item_id}}/evidence")
async def record_verification(
    project_id: str, item_id: str, body: VerificationEvidenceRequest, request: Request
) -> dict[str, Any]:
    """Record evidence for a physical or supplier verification item.

    The item reads ``awaiting_approval`` and an approval is filed; it is
    satisfied only when a reviewer approves that approval.
    """
    from api_gateway.twin.dfm_exact import record_verification_evidence

    actor = _actor(request)
    return await _exact_call(
        "verification",
        project_id,
        lambda: record_verification_evidence(
            _twin(),
            project_id,
            item_id,
            note=body.note,
            attachment_name=body.attachment_name,
            attachment_base64=body.attachment_base64,
            actor=actor,
        ),
    )


@router.get(f"{_BASE}/checklist")
async def get_release_checklist(project_id: str) -> dict[str, Any]:
    """The manufacturing release checklist for the current revision, with Create's readiness.

    ``applies: false`` when no process with exact DFM checks is selected.
    """
    from twin_core.dfm.release_checklist import manufacturing_checklist, readiness

    async def run() -> dict[str, Any]:
        checklist = await manufacturing_checklist(_twin(), project_id)
        if checklist is None:
            return {"project_id": project_id, "applies": False, "readiness": readiness(None)}
        return {"applies": True, **checklist.as_dict()}

    return await _exact_call("checklist", project_id, run)


@router.post(f"{_BASE}/report")
async def generate_dfm_report(project_id: str, request: Request) -> dict[str, Any]:
    """Generate (or reuse) the revision-specific DFM report and store it in the twin."""
    from api_gateway.twin.dfm_report import generate_report

    actor = _actor(request)
    return await _exact_call(
        "report", project_id, lambda: generate_report(_twin(), project_id, actor=actor)
    )


@router.get(f"{_BASE}/report", response_model=None)
async def get_dfm_report(
    project_id: str,
    format: Literal["json", "markdown"] = Query("json", description="markdown: the rendered copy"),
) -> Any:
    """The latest DFM report, with whether the revisions it read are still current."""
    from fastapi.responses import PlainTextResponse

    from api_gateway.twin.dfm_report import latest_report

    out = await _exact_call("report_get", project_id, lambda: latest_report(_twin(), project_id))
    if format == "markdown":
        return PlainTextResponse(out.get("markdown") or "", media_type="text/markdown")
    return out


@router.get(f"{_BASE}/reports/{{report_id}}")
async def get_dfm_report_by_id(project_id: str, report_id: str) -> dict[str, Any]:
    """One stored DFM report by id (older reports stay browsable, marked stale)."""
    from api_gateway.twin.dfm_report import report_by_id

    return await _exact_call(
        "report_by_id", project_id, lambda: report_by_id(_twin(), project_id, report_id)
    )
