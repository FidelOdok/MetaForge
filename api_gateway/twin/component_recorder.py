"""Component-selection recorder for twin.record_component_selection (MET-436 follow-up).

``component.search_parametric``/``component.search_intent`` return candidate
MPNs but persist nothing — a chosen result was pure chat output, gone once
the session ended, with no reviewable, versioned trace in the Digital Twin
(violates the Prime Rule: "if it can't be versioned, reviewed, and built,
MetaForge doesn't output it"). This recorder closes that gap: it persists one
chosen search result as a ``BOMItem`` graph node and links it to its project,
mirroring :func:`make_decision_recorder`'s project-link facet without the
markdown/MinIO facet — a BOMItem is a small structured record, not a
blob-worthy document.

Every recorded ``BOMItem`` is also linked, via a real graph edge, to its
project's ``BOM`` work product (created on first use, reused after). Without
this a ``BOMItem`` had ONLY the Postgres project-junction link (which puts it
on the Projects page) and no edge in the graph at all -- ``TwinAPI.
find_orphans()`` treats ``BOM_ITEM`` as a "dependent" node type expected to be
reachable from a parent work product (see its docstring), so every BOMItem
recorded before this fix showed up as an orphan and was unreachable via
``twin.thread_for``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.component_recorder")

_BOM_WORK_PRODUCT_NAME = "Bill of Materials"


def _urn_segment(value: str) -> str:
    """Sanitize one segment of the ``BOMItem.global_asset_id`` URN."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-") or "unknown"


async def _find_or_create_bom_work_product(
    twin: Any, project_backend: Any, project_id: str
) -> str | None:
    """Return the project's ``BOM`` work product id, creating one if absent.

    One per project (looked up by ``work_product_type=BOM`` + ``project_id``,
    same shape as ``decision_recorder``'s content-hash dedup lookup) -- every
    recorded component selection links to the same container rather than
    minting a new work product per call. Best-effort: any failure here must
    never block the BOMItem write itself, so callers get ``None`` (no edge
    added) rather than an exception.
    """
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    try:
        scope = UUID(project_id)
        existing = await twin.list_work_products(
            work_product_type=WorkProductType.BOM, project_id=scope
        )
        if existing:
            return str(existing[0].id)

        now = datetime.now(UTC)
        wp = WorkProduct(
            name=_BOM_WORK_PRODUCT_NAME,
            type=WorkProductType.BOM,
            domain="electronics",
            file_path="",
            content_hash="",
            format="",
            metadata={"created_by": "twin.record_component_selection"},
            created_at=now,
            updated_at=now,
            created_by="twin.record_component_selection",
            project_id=project_id,
        )
        created = await twin.create_work_product(wp)
        wp_id = str(getattr(created, "id", wp.id))

        if project_backend is not None:
            try:
                await project_backend.link_work_product(
                    project_id, wp_id, _BOM_WORK_PRODUCT_NAME, "bom"
                )
            except Exception as exc:  # noqa: BLE001 — link is best-effort
                logger.warning("bom_work_product_project_link_failed", error=str(exc))

        return wp_id
    except Exception as exc:  # noqa: BLE001 — never block the BOMItem write
        logger.warning("bom_work_product_lookup_failed", project_id=project_id, error=str(exc))
        return None


def make_component_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend.

    The returned callable is what the twin MCP adapter invokes; binding the
    dependencies here keeps the adapter free of api_gateway/twin_core
    imports, matching every other recorder in this package.
    """

    async def record(
        *,
        mpn: str,
        manufacturer: str,
        category: str,
        purchase_unit: str,
        role: str | None = None,
        quantity: int = 1,
        unit_cost_usd: float | None = None,
        specs: dict[str, Any] | None = None,
        source: str | None = None,
        distributor: str | None = None,
        datasheet_url: str | None = None,
        image_url: str | None = None,
        footprint: str | None = None,
        cad_model_url: str | None = None,
        purchase_url: str | None = None,
        price_currency: str = "USD",
        priced_distributor: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.bom_item import BOMItem

        if not mpn or not isinstance(mpn, str):
            raise ValueError("component recorder: 'mpn' is required (non-empty string)")
        if not manufacturer or not isinstance(manufacturer, str):
            raise ValueError("component recorder: 'manufacturer' is required (non-empty string)")
        if not category or not isinstance(category, str):
            raise ValueError("component recorder: 'category' is required (non-empty string)")

        with tracer.start_as_current_span("twin.record_component_selection") as span:
            span.set_attribute("component.mpn", mpn)
            span.set_attribute("component.category", category)
            span.set_attribute("component.purchase_unit", purchase_unit)

            description = category if not role else f"{category} ({role})"
            specifications: dict[str, Any] = {
                "category": category,
                "purchase_unit": purchase_unit,
            }
            if role:
                specifications["role"] = role
            if source:
                specifications["source"] = source
            if specs:
                specifications.update(specs)
            if session_id:
                specifications["session_id"] = session_id

            global_asset_id = f"urn:metaforge:bom:{_urn_segment(manufacturer)}:{_urn_segment(mpn)}"

            # A price is a snapshot, not a fact -- capture *when* it was
            # taken here, at the moment of recording, rather than trusting
            # a caller-supplied timestamp (which could be stale or simply
            # wrong). None when no cost is given at all: there's nothing to
            # timestamp.
            priced_at = datetime.now(UTC) if unit_cost_usd is not None else None

            item = BOMItem(
                part_number=mpn,
                manufacturer=manufacturer,
                description=description,
                quantity=max(1, quantity),
                unit_cost=unit_cost_usd,
                specifications=specifications,
                global_asset_id=global_asset_id,
                supplier=distributor,
                datasheet_url=datasheet_url,
                image_url=image_url,
                footprint=footprint,
                cad_model_url=cad_model_url,
                purchase_url=purchase_url,
                priced_at=priced_at,
                price_currency=price_currency,
                # Defaults to the buy-from supplier when the caller didn't
                # separately say which distributor's quote this price came
                # from (the common case: source==distributor).
                priced_distributor=priced_distributor if priced_distributor else distributor,
                project_id=project_id,  # pydantic coerces str → UUID
            )
            created = await twin.add_bom_item(item)
            node_id = str(getattr(created, "id", item.id))

            # Project junction link (same facet as every other recorder in
            # this package) so it shows on the Projects page, not just the
            # scoped twin view — see the "dual-representation" gotcha in
            # this repo's operational notes: node.project_id alone only
            # covers the /twin filter, not the Projects page.
            linked = False
            bom_wp_id: str | None = None
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, node_id, f"{mpn} ({category})", "bom_item"
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("component_selection_project_link_failed", error=str(exc))

            # Graph edge to the project's BOM work product — without this the
            # BOMItem has no place in the digital thread at all: unreachable
            # via twin.thread_for, and flagged an orphan by find_orphans()
            # (BOM_ITEM is a "dependent" node type). Only possible when the
            # call is project-scoped; an unscoped recording has no parent
            # work product to attach to and stays an orphan, same as today.
            if project_id:
                from twin_core.models.enums import EdgeType

                bom_wp_id = await _find_or_create_bom_work_product(
                    twin, project_backend, project_id
                )
                if bom_wp_id is not None:
                    try:
                        await twin.add_edge(UUID(bom_wp_id), UUID(node_id), EdgeType.CONTAINS)
                    except Exception as exc:  # noqa: BLE001 — edge is best-effort
                        logger.warning(
                            "component_selection_bom_edge_failed",
                            bom_work_product_id=bom_wp_id,
                            error=str(exc),
                        )
                        bom_wp_id = None

            logger.info(
                "component_selection_recorded",
                node_id=node_id,
                mpn=mpn,
                category=category,
                project_id=project_id,
                linked=linked,
                bom_work_product_id=bom_wp_id,
            )
            return {
                "node_id": node_id,
                "mpn": mpn,
                "category": category,
                "project_linked": linked,
                "bom_work_product_id": bom_wp_id,
            }

    return record
