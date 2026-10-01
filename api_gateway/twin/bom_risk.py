"""BOM supply-chain risk rollup (FORGE-268, gap G-C4).

``domain_agents/supply_chain/risk_scorer.py``'s ``BOMRiskScorer`` and
``alt_parts.py``'s ``AlternatePartsFinder`` already compute a real,
weighted, tested risk score (single-source, lead time, lifecycle, price
volatility, stock level, compliance) -- not a stub. The gap was wiring:
nothing called them for an arbitrary existing project's real BOM outside
a full ``hardware_v1`` design-flow pipeline run, and
``api_gateway/chat/activity.py``'s ``ChatContextAssembler`` hardcoded
``"supply_chain_risk": "unknown"`` as a placeholder.

This module is the missing wiring: for each real BOM line on a project,
resolve real distributor offers (``distributors.resolve_offers`` --
already used by FORGE-265's component selection) for stock/lead-time/
price/MOQ/source-count, resolve the top offer's real lifecycle status
(a ``{distributor}.get_product`` call -- ``Offer.lifecycle_status`` is
never populated by ``resolve_offers`` itself, it's schema-only there),
and feed the result into the already-tested ``BOMRiskScorer``.

**Known limitation, not fixed here**: RoHS/REACH compliance flags are not
sourced from anywhere in this codebase's distributor layer today (neither
``Offer`` nor ``PartDetail`` carries them) -- ``BOMRiskScorer``'s
compliance factor therefore always sees both flags absent (defaults to
``False``/``False``, its own "missing both" case) for every part scored
here. That is an honest reflection of a real data gap, not a bug in this
module or in the scorer -- it's a bounded 10%-weight input a future ticket
could fix by adding real compliance sourcing, not something to fabricate
a value for here.

**Also deliberately NOT fixed here**: ``ChatContextAssembler`` (all of
it -- ``properties``, ``alternates``, ``supply_chain_risk``,
``graph_neighbors``, and every other scope kind's handler) is explicitly
commented "stubs -- will query Neo4j in production"; it's a synchronous
method with no twin/mcp_bridge dependency injection at all, not a
gateway route. Wiring just its one ``supply_chain_risk`` field in
isolation, while every sibling field in the same stub stays fake, would
be a superficially-complete but actually-inconsistent fix -- real wiring
of that whole context-assembly layer is a separate, larger ticket.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.bom_risk")


def _build_distributor_data(
    resolution: dict[str, Any], lifecycle_status: str | None
) -> dict[str, Any] | None:
    """Turn one item's ``distributors.resolve_offers`` result (plus an
    optional real lifecycle lookup) into the ``distributor_data`` dict
    ``BOMRiskScorer`` expects. Returns ``None`` when no distributor
    carries the part at all -- the scorer still runs (``num_sources``
    defaults to 1 in that case, which is the wrong bias), so callers
    should treat ``None`` as "no real offer data available" and pass an
    explicit ``num_sources: 0`` instead (handled by the caller, not here,
    since that's a choice about what "no offer" means, not a data
    transform)."""
    sufficient = resolution.get("offers") or []
    insufficient = resolution.get("insufficient_offers") or []
    all_offers = [*sufficient, *insufficient]
    if not all_offers:
        return None

    top = sufficient[0] if sufficient else all_offers[0]
    prices = [
        o["total_committed_cost"] for o in all_offers if o.get("total_committed_cost") is not None
    ]

    lead_time_days = top.get("lead_time_days")
    return {
        "num_sources": len(all_offers),
        "lead_time_weeks": (lead_time_days / 7.0) if lead_time_days is not None else 0.0,
        "lifecycle": lifecycle_status or "unknown",
        "prices": prices,
        "stock": top.get("stock_qty", 0),
        "moq": top.get("moq", 1),
        # Compliance: see module docstring -- not sourced anywhere today,
        # left absent so the scorer's own documented "missing both"
        # default applies rather than fabricating a value.
    }


def make_bom_risk_scorer(twin: Any, *, mcp_bridge: Any) -> Any:
    """Return an async ``score_project_bom_risk(project_id) -> dict``
    bound to a twin + mcp_bridge (real ``distributors.resolve_offers`` and
    ``{distributor}.get_product`` calls)."""

    async def score_project_bom_risk(project_id: str) -> dict[str, Any]:
        from domain_agents.supply_chain.risk_scorer import BOMRiskScorer

        with tracer.start_as_current_span("bom_risk.score_project") as span:
            span.set_attribute("bom_risk.project_id", project_id)

            scoped = UUID(project_id)
            items = await twin.list_bom_items(project_id=scoped)
            bom_items = [i for i in items if getattr(i, "part_number", None)]
            span.set_attribute("bom_risk.item_count", len(bom_items))

            if not bom_items:
                from domain_agents.supply_chain.models import BOMRiskReport

                return BOMRiskReport(project_id=project_id).model_dump(mode="json")

            offer_items = [
                {"mpn": item.part_number, "required_qty": max(item.quantity, 1)}
                for item in bom_items
            ]
            offers_response = await mcp_bridge.invoke(
                "distributors.resolve_offers", {"items": offer_items}
            )
            resolutions_by_mpn: dict[str, dict[str, Any]] = {
                r["mpn"]: r for r in offers_response.get("results", [])
            }

            parts_data: list[dict[str, Any]] = []
            for item in bom_items:
                resolution = resolutions_by_mpn.get(item.part_number, {})
                sufficient = resolution.get("offers") or []
                insufficient = resolution.get("insufficient_offers") or []
                top = sufficient[0] if sufficient else (insufficient[0] if insufficient else None)

                lifecycle_status: str | None = None
                if top is not None:
                    try:
                        product = await mcp_bridge.invoke(
                            f"{top['distributor'].lower()}.get_product",
                            {"mpn": item.part_number},
                        )
                        part = product.get("part")
                        if part is not None:
                            lifecycle_status = part.get("lifecycle_status")
                    except Exception as exc:  # noqa: BLE001 -- one part's lifecycle
                        # lookup failing must never abort the whole BOM scoring.
                        logger.warning(
                            "bom_risk_lifecycle_lookup_failed",
                            mpn=item.part_number,
                            distributor=top.get("distributor") if top else None,
                            error=str(exc),
                        )

                distributor_data = _build_distributor_data(resolution, lifecycle_status)
                part_data: dict[str, Any] = {
                    "mpn": item.part_number,
                    "manufacturer": item.manufacturer,
                }
                if distributor_data is not None:
                    part_data.update(distributor_data)
                else:
                    # No distributor (of however many are configured) carries
                    # this part at all -- worse than single-source, not the
                    # scorer's default num_sources=1.
                    part_data["num_sources"] = 0
                parts_data.append(part_data)

            scorer = BOMRiskScorer()
            report = scorer.score_bom(parts_data, project_id=project_id)

            logger.info(
                "bom_risk_scored",
                project_id=project_id,
                total_parts=report.total_parts,
                overall_score=report.overall_score,
                critical=report.critical_count,
                high=report.high_count,
            )
            return report.model_dump(mode="json")

    return score_project_bom_risk
