"""Decision lookup API (FORGE-289, gap G-G3).

``GET /v1/decisions?related_to=<node_id>`` answers "what decisions touch
this node" -- the dashboard-side half of the gap ("Decision cards linked
from hierarchy nodes and iterations"). No new twin_core method is needed:
every Decision link (``parent_refs``, ``evidence_refs``, and the design
loop's own ``GENERATED_FROM`` winner link) is a real *incoming* edge onto
the related node, from a ``WorkProductType.DESIGN_DECISION`` work product --
so this walks ``twin.get_edges(node_id, direction="incoming")`` and keeps
only the edges whose source is actually a Decision. Filtering by source
type rather than by edge type is deliberate: ``parent_refs``' own
``relation`` is caller-configurable (default ``satisfies``, but not fixed),
so an edge-type allowlist would silently miss a Decision linked with a
non-default relation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.decision_routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("decision_routes_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/decisions", tags=["decisions"])


def _decision_to_dict(wp: Any) -> dict[str, Any]:
    metadata = wp.metadata or {}
    return {
        "id": str(wp.id),
        "title": wp.name,
        "rationale": metadata.get("rationale", ""),
        "alternatives": metadata.get("alternatives", []),
        "parent_refs": metadata.get("parent_refs", []),
        "evidence_refs": metadata.get("evidence_refs", []),
        "created_at": wp.created_at.isoformat() if wp.created_at else None,
    }


@router.get("")
async def list_related_decisions(related_to: str) -> dict[str, Any]:
    with tracer.start_as_current_span("decisions.list_related") as span:
        try:
            node_id = UUID(related_to)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"'related_to' must be a UUID, got {related_to!r}"
            ) from exc
        span.set_attribute("decisions.related_to", related_to)

        edges = await _twin.get_edges(node_id, direction="incoming")
        seen: set[UUID] = set()
        decisions: list[dict[str, Any]] = []
        for edge in edges:
            if edge.source_id in seen:
                continue
            wp = await _twin.get_work_product(edge.source_id)
            if wp is None or wp.type != WorkProductType.DESIGN_DECISION:
                continue
            seen.add(edge.source_id)
            decisions.append(_decision_to_dict(wp))

        span.set_attribute("decisions.count", len(decisions))
        return {"related_to": related_to, "decisions": decisions}
