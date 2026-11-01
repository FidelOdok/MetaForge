"""Routes for the live assembly view (FORGE-647).

``GET /v1/twin/projects/{id}/live`` streams a project's in-progress assembly
placements. ``POST /v1/twin/projects/{id}/live-events`` is how the ``mcp-http``
sidecar, a separate process with no stream of its own, reports a placement.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api_gateway.twin.live import SSE_HEADERS, hub, live_event_stream

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/twin/projects", tags=["twin"])


class LivePart(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)


class LiveEventRequest(BaseModel):
    """One placement change. Carries geometry placement only, never a model."""

    type: Literal["assembly.part_placed", "assembly.part_removed", "assembly.cleared"]
    assembly: str = Field(min_length=1, max_length=200)
    part: LivePart | None = None
    position: list[float] | None = Field(default=None, min_length=3, max_length=3)
    rotation: list[float] | None = Field(default=None, min_length=3, max_length=3)


async def publish_local(project_id: str, event: dict[str, Any]) -> None:
    """The publisher for code running inside the gateway process."""
    hub.publish(project_id, event)


@router.get("/{project_id}/live")
async def stream_live_assembly(
    project_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE: a ``live.snapshot`` of the project's assemblies in progress, then
    one ``live.event`` per placement change."""
    resume = int(last_event_id) if last_event_id and last_event_id.strip().isdigit() else None
    return StreamingResponse(
        live_event_stream(project_id, hub, resume),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/{project_id}/live/state")
async def live_assembly_state(project_id: str) -> dict[str, Any]:
    """The in-progress assemblies and their placed parts, as of now."""
    return hub.snapshot(project_id)


@router.post("/{project_id}/live-events", status_code=202)
async def ingest_live_event(project_id: str, body: LiveEventRequest) -> dict[str, int]:
    """Accept a placement event from a process that has no stream of its own."""
    stamped = hub.publish(project_id, body.model_dump(exclude_none=True))
    return {"seq": int(stamped["seq"])}
