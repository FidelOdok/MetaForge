"""Dashboard-facing read/approve support for ``technical_drawing`` work
products (FORGE-293, gap G-H1).

The data half of this capability already existed before this ticket
(``structured_document_recorder.make_technical_drawing_recorder``, PR #734):
a real ``technical_drawing`` WorkProduct with structured dimensions, GD&T
callouts, surface finishes and inspection requirements, linked back to its
source CAD_MODEL via a ``PARENT_OF`` edge (drawing -> source part). It was
never surfaced in the dashboard -- the same "real data, no consumer" shape
this session found repeatedly elsewhere (FORGE-268's risk scorer, FORGE-297's
coverage agent).

This module adds exactly two things: (1) listing a part's real recorded
drawings by walking the real ``PARENT_OF`` edge, and (2) a human approval
gate mirroring ``design_sketch_recorder.make_design_sketch_approver``'s exact
shape. Rendering a real 2D vector/projection drawing (TechDraw or an
equivalent) stays out of scope -- confirmed absent anywhere in this codebase
(only two honest "no TechDraw-equivalent generator wired up yet" comments
exist, in ``generate_technical_drawing/handler.py`` and this same recorder
module) -- a drawing viewer here means a structured-data table, not a
rendered page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.technical_drawing_viewer")


def make_technical_drawing_lister(twin: Any) -> Any:
    """Return an async ``list_drawings(*, work_product_id) -> list[dict]``,
    oldest first, reading real ``technical_drawing`` work products linked to
    ``work_product_id`` via their own outgoing ``PARENT_OF`` edge (the exact
    edge ``generate_technical_drawing``'s handler already writes on commit)."""

    async def list_drawings(*, work_product_id: str) -> list[dict[str, Any]]:
        from uuid import UUID as _UUID

        from twin_core.models.enums import EdgeType, WorkProductType

        with tracer.start_as_current_span("twin.list_technical_drawings") as span:
            span.set_attribute("technical_drawing.work_product_id", work_product_id)
            target_id = _UUID(work_product_id)
            edges = await twin.get_edges(
                target_id, direction="incoming", edge_type=EdgeType.PARENT_OF
            )

            drawings: list[dict[str, Any]] = []
            for edge in edges:
                wp = await twin.get_work_product(edge.source_id)
                if wp is None or wp.type != WorkProductType.TECHNICAL_DRAWING:
                    continue
                meta = wp.metadata
                drawings.append(
                    {
                        "node_id": str(wp.id),
                        "created_at": wp.created_at.isoformat(),
                        "name": wp.name,
                        "part_name": meta.get("part_name", ""),
                        "dimensions": meta.get("dimensions", []),
                        "gdt_callouts": meta.get("gdt_callouts", []),
                        "surface_finishes": meta.get("surface_finishes", []),
                        "inspection_requirements": meta.get("inspection_requirements", []),
                        "approved": bool(meta.get("approved")),
                        "approved_at": meta.get("approved_at"),
                        "approved_by": meta.get("approved_by"),
                    }
                )

            drawings_sorted = sorted(drawings, key=lambda d: d["created_at"])
            logger.info(
                "technical_drawings_listed",
                work_product_id=work_product_id,
                count=len(drawings_sorted),
            )
            return drawings_sorted

    return list_drawings


def make_technical_drawing_approver(twin: Any) -> Any:
    """Return an async ``approve(node_id, ...)`` bound to a twin.

    Mirrors ``design_sketch_recorder.make_design_sketch_approver``'s exact
    shape: a dedicated state transition (approved: false -> true), versioned
    via the same revision-history append, not a silent metadata overwrite.
    Treats a missing ``approved`` key (a drawing recorded before this ticket)
    the same as an explicit ``False`` -- real pre-existing drawings approve
    cleanly, they don't need a backfill migration.
    """

    async def approve(
        node_id: str,
        *,
        approved_by: str | None = None,
        change_description: str = "technical drawing approved",
    ) -> dict[str, Any]:
        from uuid import UUID as _UUID

        from api_gateway.twin.version_service import VersionService
        from twin_core.models.enums import WorkProductType

        with tracer.start_as_current_span("twin.approve_technical_drawing") as span:
            span.set_attribute("technical_drawing.node_id", node_id)
            uid = _UUID(node_id)
            wp = await twin.get_work_product(uid)
            if wp is None:
                raise ValueError(f"technical_drawing approve: node {node_id} not found")
            if wp.type != WorkProductType.TECHNICAL_DRAWING:
                raise ValueError(
                    f"technical_drawing approve: node {node_id} is a {wp.type.value}, "
                    "not a technical_drawing"
                )
            if wp.metadata.get("approved"):
                raise ValueError(f"technical_drawing approve: node {node_id} is already approved")

            updated_meta = dict(wp.metadata)
            updated_meta["approved"] = True
            updated_meta["approved_at"] = datetime.now(UTC).isoformat()
            if approved_by:
                updated_meta["approved_by"] = approved_by

            revision = VersionService.build_revision(
                wp, change_description, snapshot_override=updated_meta
            )
            final_meta = VersionService.append_to_metadata(updated_meta, revision)
            await twin.update_work_product(
                uid, {"metadata": final_meta, "updated_at": datetime.now(UTC)}
            )

            logger.info(
                "technical_drawing_approved",
                node_id=node_id,
                approved_by=approved_by,
                revision_number=revision.get("revision"),
            )
            return {
                "node_id": node_id,
                "approved": True,
                "approved_at": updated_meta["approved_at"],
            }

    return approve
