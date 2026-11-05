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
