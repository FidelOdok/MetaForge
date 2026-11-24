"""Which library parts a project uses rest on unaccepted estimates (R8).

Registered as the release checklist's estimate gap source in the gateway and the
MCP sidecar: the checklist lives in ``twin_core``, which cannot import the library.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.estimate_gaps")


async def project_estimate_gaps(twin: Any, project_id: UUID) -> list[str]:
    """One line per library part the project's BOM uses that is not yet accepted."""
    from api_gateway.library.catalog_routes import get_catalog
    from api_gateway.twin.component_thread import library_components, library_for
    from twin_core.library.catalog import CatalogNotFoundError, estimate_gap

    with tracer.start_as_current_span("library.estimate_gaps") as span:
        span.set_attribute("project.id", str(project_id))
        items = list(await twin.list_bom_items(project_id))
        library: list[dict[str, Any]] | None = None
        catalog = get_catalog()
        seen: set[str] = set()
        gaps: list[str] = []
        for item in items:
            specs = dict(getattr(item, "specifications", None) or {})
            ref = (
                specs.get("library_entry") if isinstance(specs.get("library_entry"), dict) else None
            )
            if ref is None:
                if library is None:
                    library = library_components()
                ref = library_for(str(getattr(item, "part_number", "") or ""), library)
            entry_id = str((ref or {}).get("id") or "")
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            try:
                entry = catalog.get(entry_id, "component")
            except CatalogNotFoundError:
                continue
            gap = estimate_gap(entry)
            if gap:
                gaps.append(gap)
        span.set_attribute("estimate_gaps", len(gaps))
        logger.info("library_estimate_gaps", project_id=str(project_id), gaps=len(gaps))
        return gaps


def gaps_by_work_product(node_ids: list[str]) -> dict[str, str]:
    """Work product id -> gap, for library components whose model is that work product.

    A part's geometry links to its library entry through the entry's
    ``geometry.step_work_product_id``. Raises when the library cannot be read.
    """
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import estimate_gap

    wanted = set(node_ids)
    out: dict[str, str] = {}
    with tracer.start_as_current_span("library.estimate_gaps_by_work_product") as span:
        for entry in get_catalog().list("component"):
            wp = str((getattr(entry, "geometry", None) or {}).get("step_work_product_id") or "")
            if wp in wanted:
                gap = estimate_gap(entry)
                if gap:
                    out[wp] = gap
        span.set_attribute("estimate_gaps", len(out))
    return out
