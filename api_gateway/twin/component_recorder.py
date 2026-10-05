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

That work product is deliberately NOT just any ``WorkProductType.BOM`` node
found for the project -- ``api_gateway/twin/bom_recorder.py`` (the
electronics agent's whole-CSV-blob BOM) and ``import_service.py``'s CSV
importer can also create ``BOM`` work products, and those are a different
kind of artifact (one opaque document blob) from this module's "container of
individually-queryable BOMItem line items" -- attaching one to the other's
node would silently conflate two unrelated representations. The container
this module creates carries an explicit ``metadata["kind"]`` marker so the
lookup only ever finds/reuses its own.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin.item_revisions import (
    finish_definition_revision,
    plan_definition_revision,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.component_recorder")

_BOM_WORK_PRODUCT_NAME = "Bill of Materials"
_CONTAINER_KIND = "component_selection_container"


def _urn_segment(value: str) -> str:
    """Sanitize one segment of the ``BOMItem.global_asset_id`` URN."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-") or "unknown"


async def _find_or_create_bom_work_product(
    twin: Any, project_backend: Any, project_id: str
) -> str | None:
    """Return this recorder's own container ``BOM`` work product id for the
    project, creating one if absent.

    One per project, identified by ``metadata["kind"] == _CONTAINER_KIND`` --
    ``list_work_products`` has no metadata filter, so every ``BOM`` work
    product for the project is fetched and filtered client-side; a
    document-shaped ``BOM`` from ``bom_recorder``/``import_service`` (no such
    marker) is never matched and never reused. Best-effort: any failure here
    must never block the BOMItem write itself, so callers get ``None`` (no
    edge added) rather than an exception.
    """
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    try:
        scope = UUID(project_id)
        existing = await twin.list_work_products(
            work_product_type=WorkProductType.BOM, project_id=scope
        )
        for wp in existing:
            if (wp.metadata or {}).get("kind") == _CONTAINER_KIND:
                return str(wp.id)

        now = datetime.now(UTC)
        wp = WorkProduct(
            name=_BOM_WORK_PRODUCT_NAME,
            type=WorkProductType.BOM,
            domain="electronics",
            file_path="",
            content_hash="",
            format="",
            metadata={"created_by": "twin.record_component_selection", "kind": _CONTAINER_KIND},
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


async def _requirement_margins(
    twin: Any, project_id: str | None, specs: dict[str, Any]
) -> tuple[list[Any], list[Any]]:
    """Hold the chosen part's specs against the project's requirements.

    FORGE-346. Best-effort by design: a margin report that fails must not
    fail the BOM commit, which is the thing the caller actually asked for.
    """
    if not project_id or not specs:
        return [], []
    try:
        from uuid import UUID

        from twin_core.consistency.spec_margins import compare_specs_to_requirements

        constraints = await twin.list_constraints(project_id=UUID(str(project_id)))
        return compare_specs_to_requirements(specs, list(constraints))
    except Exception as exc:  # noqa: BLE001 — never fail the commit
        logger.warning("component_requirement_margins_failed", error=str(exc))
        return [], []


def make_component_recorder(
    twin: Any, project_backend: Any = None, catalog_store: Any = None
) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend.

    The returned callable is what the twin MCP adapter invokes; binding the
    dependencies here keeps the adapter free of api_gateway/twin_core
    imports, matching every other recorder in this package.

    ``catalog_store`` (optional, a ``ComponentCatalogStore``) lets the
    recorder auto-fill ``datasheet_url``/``image_url``/``footprint``/
    ``cad_model_url``/``unit_cost_usd`` from an already-indexed catalog row
    when the caller leaves them unset -- without this, a caller has to
    manually copy every field across from a prior ``component.
    search_parametric`` call even though the same data already lives in the
    catalog under the same (mpn, manufacturer). Only fills fields the caller
    left ``None``; an explicit caller-supplied value always wins.
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
        item_key: str | None = None,
        supersedes: str | None = None,
        change_reason: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.bom_item import BOMItem

        if not mpn or not isinstance(mpn, str):
            raise ValueError("component recorder: 'mpn' is required (non-empty string)")
        if not manufacturer or not isinstance(manufacturer, str):
            raise ValueError("component recorder: 'manufacturer' is required (non-empty string)")
        if not category or not isinstance(category, str):
            raise ValueError("component recorder: 'category' is required (non-empty string)")

        # FORGE-523: a component selection is a definition keyed by the role
        # it fills (or by the part when no role is given), so choosing a
        # different part for the same role is the next revision of one item.
        selection_name = role or f"{manufacturer} {mpn}"
        plan = await plan_definition_revision(
            twin,
            item_type="component_selection",
            name=selection_name,
            project_id=project_id,
            default_author="twin.record_component_selection",
            item_key=item_key,
            supersedes=supersedes,
            change_reason=change_reason,
            run_id=run_id,
        )

        with tracer.start_as_current_span("twin.record_component_selection") as span:
            span.set_attribute("component.mpn", mpn)
            span.set_attribute("component.category", category)
            span.set_attribute("component.purchase_unit", purchase_unit)

            # Auto-fill from the catalog when the caller left a field unset --
            # never overrides an explicit caller-supplied value (including an
            # explicit empty string, which is why this checks ``is None``,
            # not falsiness). A lookup failure degrades silently to "nothing
            # filled in", same as every other best-effort facet here.
            if catalog_store is not None and (
                datasheet_url is None
                or image_url is None
                or footprint is None
                or cad_model_url is None
                or unit_cost_usd is None
            ):
                try:
                    row = await catalog_store.get(mpn, manufacturer)
                except Exception as exc:  # noqa: BLE001 — auto-fill is best-effort
                    row = None
                    logger.warning("component_selection_catalog_lookup_failed", error=str(exc))
                if row is not None:
                    if datasheet_url is None:
                        datasheet_url = row.datasheet_url or None
                    if image_url is None:
                        image_url = row.image_url or None
                    if footprint is None:
                        footprint = row.footprint or None
                    if cad_model_url is None:
                        cad_model_url = row.cad_model_url or None
                    if unit_cost_usd is None:
                        unit_cost_usd = row.cost_usd

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
            result: dict[str, Any] = {
                "node_id": node_id,
                "mpn": mpn,
                "category": category,
                "project_linked": linked,
                "bom_work_product_id": bom_wp_id,
            }
            await finish_definition_revision(
                twin, plan, UUID(node_id), name=selection_name, result=result
            )
            margins, unchecked = await _requirement_margins(twin, project_id, specs or {})
            if margins or unchecked:
                result["requirement_margins"] = [m.model_dump() for m in margins]
                result["unchecked_requirements"] = [u.model_dump() for u in unchecked]
                violated = [m.requirement for m in margins if not m.satisfied]
                if violated:
                    # FORGE-346: the part is still recorded -- refusing a
                    # commit because a requirement is not met would block
                    # the normal case of choosing the best available part
                    # and then changing the requirement. But it must not be
                    # silent: without this the violation first surfaces at a
                    # gate, with nothing linking it back to the decision to
                    # buy this part.
                    result["violates"] = violated
                    logger.warning(
                        "component_selection_violates_requirements",
                        node_id=node_id,
                        mpn=mpn,
                        requirements=violated,
                    )
            return result

    return record
