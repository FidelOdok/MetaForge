"""Cross-distributor offer resolution and ranking (MET-436).

Even once the right component is identified, the same MPN is often listed
by several distributors (Digi-Key, Mouser, Nexar/Octopart) at different
prices, stock levels, and lead times. This module fans out to whichever
:class:`~tool_registry.tools.distributors.base.DistributorAdapter` instances
are configured, normalizes their answers into :class:`Offer` records, and
ranks them by what actually matters to a buyer: quantity-aware pricing
(the tier that covers the real order size, not the cheapest listed tier),
stock *sufficiency* (not just stock > 0), an optional delivery deadline,
and MOQ-forced overbuy (total committed spend, not bare unit price).

A part absent from every configured distributor — the common case for
specialty/hobbyist modules (e.g. a COTS flight-controller board) that
aren't carried by general electronics distributors — is a **normal
outcome** (``status="no_offers_found"``), never an exception.

This module only imports from ``tool_registry.tools.distributors.base``,
``tool_registry.mcp_server.*``, ``observability``, ``pydantic``, and the
stdlib, per the ``tool_registry`` layer rule. It does not modify
``base.py``, ``mcp_adapter.py``, or any concrete adapter.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    LifecycleStatus,
    PricingBreak,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.distributors.offer_resolver")

_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=256, max_cpu_seconds=30, max_disk_mb=64)

_DEFAULT_PER_ADAPTER_TIMEOUT_S = 8.0

OfferResolutionStatus = Literal["ok", "insufficient_stock_everywhere", "no_offers_found"]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Offer(BaseModel):
    """One distributor's normalized purchasing offer for an MPN."""

    mpn: str
    distributor: str
    distributor_pn: str = ""

    required_qty: int
    committed_qty: int = Field(
        description=(
            "Quantity you'd actually have to pay for — required_qty raised to "
            "whichever of MOQ or an extrapolated price-break threshold forces "
            "a larger purchase. Always use this (via total_committed_cost), "
            "never unit_price_at_qty alone, to compare offers."
        )
    )
    unit_price_at_qty: float | None = None
    currency: str = "USD"
    total_committed_cost: float | None = None

    stock_qty: int = 0
    lead_time_days: int | None = None
    moq: int = 1
    lifecycle_status: LifecycleStatus = LifecycleStatus.UNKNOWN

    meets_qty: bool
    meets_deadline: bool | None = None

    price_extrapolated_below_min_tier: bool = False
    is_multi_seller_aggregate: bool = False
    partial_data: bool = False
    fetched_at: datetime


class OfferResolution(BaseModel):
    """Resolved, ranked offers for one MPN + required quantity."""

    mpn: str
    required_qty: int
    deadline_days: int | None = None
    status: OfferResolutionStatus
    reason: str | None = None
    offers: list[Offer] = Field(default_factory=list)
    insufficient_offers: list[Offer] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------


def _select_tier(breaks: list[PricingBreak], required_qty: int) -> tuple[PricingBreak, bool]:
    """Pick the price-break tier that applies to ``required_qty`` units.

    Returns ``(tier, extrapolated)``. ``extrapolated=True`` means
    ``required_qty`` is below every listed tier's threshold, so the
    returned tier is the best *known* price but only actually unlocks at
    ``tier.quantity`` units — the caller must inflate the committed
    quantity to that threshold, not silently apply the price to the
    smaller order size.
    """
    if not breaks:
        raise ValueError("breaks must be non-empty")
    sorted_breaks = sorted(breaks, key=lambda b: b.quantity)
    eligible = [b for b in sorted_breaks if b.quantity <= required_qty]
    if eligible:
        return max(eligible, key=lambda b: b.quantity), False
    return sorted_breaks[0], True


def _build_offer(
    *,
    distributor: str,
    mpn: str,
    required_qty: int,
    deadline_days: int | None,
    breaks: list[PricingBreak],
    availability: AvailabilityInfo | None,
    partial_data: bool,
    is_multi_seller_aggregate: bool,
    fetched_at: datetime,
) -> Offer:
    """Normalize one adapter's raw pricing/availability into an ``Offer``."""
    extrapolated = False
    tier_quantity = 0
    unit_price_at_qty: float | None = None
    currency = "USD"
    if breaks:
        tier, extrapolated = _select_tier(breaks, required_qty)
        unit_price_at_qty = tier.unit_price
        currency = tier.currency
        tier_quantity = tier.quantity

    stock_qty = availability.stock_qty if availability is not None else 0
    lead_time_days = availability.lead_time_days if availability is not None else None
    moq = availability.minimum_order_qty if availability is not None else 1

    committed_qty = required_qty
    if extrapolated:
        committed_qty = max(committed_qty, tier_quantity)
    committed_qty = max(committed_qty, moq)

    total_committed_cost = (
        committed_qty * unit_price_at_qty if unit_price_at_qty is not None else None
    )

    meets_qty = stock_qty >= required_qty

    meets_deadline: bool | None
    if deadline_days is None:
        meets_deadline = None
    elif lead_time_days is not None:
        meets_deadline = lead_time_days <= deadline_days
    elif stock_qty > 0:
        # No reported lead time but in stock — established convention
        # across these adapters is "ships now," not "unknown."
        meets_deadline = True
    else:
        meets_deadline = None

    return Offer(
        mpn=mpn,
        distributor=distributor,
        required_qty=required_qty,
        committed_qty=committed_qty,
        unit_price_at_qty=unit_price_at_qty,
        currency=currency,
        total_committed_cost=total_committed_cost,
        stock_qty=stock_qty,
        lead_time_days=lead_time_days,
        moq=moq,
        meets_qty=meets_qty,
        meets_deadline=meets_deadline,
        price_extrapolated_below_min_tier=extrapolated,
        is_multi_seller_aggregate=is_multi_seller_aggregate,
        partial_data=partial_data,
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


async def _call_pricing(
    adapter: DistributorAdapter, mpn: str, timeout_s: float
) -> tuple[list[PricingBreak], bool]:
    try:
        breaks = await asyncio.wait_for(adapter.get_pricing(mpn), timeout=timeout_s)
        return breaks, True
    except TimeoutError:
        logger.warning("offer_resolver_pricing_timeout", distributor=adapter.name, mpn=mpn)
        return [], False
    except Exception as exc:  # noqa: BLE001 - one distributor's failure must never propagate
        logger.warning(
            "offer_resolver_pricing_failed", distributor=adapter.name, mpn=mpn, error=str(exc)
        )
        return [], False


async def _call_availability(
    adapter: DistributorAdapter, mpn: str, timeout_s: float
) -> tuple[AvailabilityInfo | None, bool]:
    try:
        availability = await asyncio.wait_for(adapter.get_availability(mpn), timeout=timeout_s)
        return availability, True
    except TimeoutError:
        logger.warning("offer_resolver_availability_timeout", distributor=adapter.name, mpn=mpn)
        return None, False
    except Exception as exc:  # noqa: BLE001 - one distributor's failure must never propagate
        logger.warning(
            "offer_resolver_availability_failed", distributor=adapter.name, mpn=mpn, error=str(exc)
        )
        return None, False


async def _fetch_one(
    adapter: DistributorAdapter,
    mpn: str,
    required_qty: int,
    deadline_days: int | None,
    timeout_s: float,
) -> Offer | None:
    """Resolve one adapter's offer for ``mpn``, or ``None`` if it has none.

    ``None`` covers two distinct, both-normal situations: the adapter
    genuinely doesn't carry this part (both calls succeeded, both came
    back empty — the expected outcome for a specialty COTS module absent
    from a general distributor's catalog), or the adapter was unreachable
    (both calls failed/timed out — logged as a warning, contributes
    nothing rather than failing the whole resolution).
    """
    (breaks, pricing_ok), (availability, availability_ok) = await asyncio.gather(
        _call_pricing(adapter, mpn, timeout_s),
        _call_availability(adapter, mpn, timeout_s),
    )

    if not pricing_ok and not availability_ok:
        logger.warning("offer_resolver_adapter_unreachable", distributor=adapter.name, mpn=mpn)
        return None

    has_data = bool(breaks) or availability is not None
    if not has_data:
        # Either both calls succeeded and both were empty (not carried by
        # this distributor — normal), or the one call that succeeded was
        # itself empty while the other failed. Either way there's nothing
        # usable to build an offer from.
        return None

    return _build_offer(
        distributor=adapter.name,
        mpn=mpn,
        required_qty=required_qty,
        deadline_days=deadline_days,
        breaks=breaks,
        availability=availability,
        partial_data=not (pricing_ok and availability_ok),
        is_multi_seller_aggregate=(adapter.name.lower() == "nexar"),
        fetched_at=datetime.now(UTC),
    )


async def fetch_offers_for_mpn(
    adapters: list[DistributorAdapter],
    mpn: str,
    required_qty: int,
    deadline_days: int | None = None,
    per_adapter_timeout_s: float = _DEFAULT_PER_ADAPTER_TIMEOUT_S,
) -> list[Offer]:
    """Fan out to every adapter concurrently and return the offers found.

    Zero adapters, or every adapter returning nothing, both produce an
    empty list — the same, deliberately unremarkable outcome.
    """
    if not adapters:
        return []
    with tracer.start_as_current_span("offer_resolver.fetch_offers_for_mpn") as span:
        span.set_attribute("offer_resolver.mpn", mpn)
        span.set_attribute("offer_resolver.required_qty", required_qty)
        span.set_attribute("offer_resolver.adapter_count", len(adapters))
        results = await asyncio.gather(
            *[
                _fetch_one(adapter, mpn, required_qty, deadline_days, per_adapter_timeout_s)
                for adapter in adapters
            ]
        )
        offers = [offer for offer in results if offer is not None]
        span.set_attribute("offer_resolver.offer_count", len(offers))
        logger.info(
            "offer_resolver_fetch_completed",
            mpn=mpn,
            required_qty=required_qty,
            adapter_count=len(adapters),
            offer_count=len(offers),
        )
        return offers


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _cost_key(offer: Offer) -> float:
    return offer.total_committed_cost if offer.total_committed_cost is not None else float("inf")


_DEADLINE_RANK: dict[bool | None, int] = {True: 0, None: 1, False: 2}


def _sort_key(
    offer: Offer, deadline_days: int | None
) -> tuple[float, ...] | tuple[int, float, int]:
    if deadline_days is not None:
        return (_DEADLINE_RANK[offer.meets_deadline], _cost_key(offer))
    lead_tiebreak = offer.lead_time_days if offer.lead_time_days is not None else -1
    return (_cost_key(offer), float(lead_tiebreak), -offer.stock_qty)


async def resolve_offers_for_item(
    adapters: list[DistributorAdapter],
    mpn: str,
    required_qty: int,
    deadline_days: int | None = None,
    per_adapter_timeout_s: float = _DEFAULT_PER_ADAPTER_TIMEOUT_S,
) -> OfferResolution:
    """Fetch, filter, and rank offers for one MPN + required quantity."""
    with tracer.start_as_current_span("offer_resolver.resolve_offers_for_item") as span:
        span.set_attribute("offer_resolver.mpn", mpn)
        span.set_attribute("offer_resolver.required_qty", required_qty)

        all_offers = await fetch_offers_for_mpn(
            adapters, mpn, required_qty, deadline_days, per_adapter_timeout_s
        )

        if not all_offers:
            span.set_attribute("offer_resolver.status", "no_offers_found")
            return OfferResolution(
                mpn=mpn,
                required_qty=required_qty,
                deadline_days=deadline_days,
                status="no_offers_found",
                reason=(
                    f"No distributor (of {len(adapters)} configured) returned an offer for {mpn}."
                ),
            )

        sufficient = [offer for offer in all_offers if offer.meets_qty]
        insufficient = [offer for offer in all_offers if not offer.meets_qty]

        if not sufficient:
            ranked_insufficient = sorted(
                insufficient, key=lambda o: (-o.stock_qty, _cost_key(o), o.distributor)
            )
            span.set_attribute("offer_resolver.status", "insufficient_stock_everywhere")
            return OfferResolution(
                mpn=mpn,
                required_qty=required_qty,
                deadline_days=deadline_days,
                status="insufficient_stock_everywhere",
                reason=(
                    f"Found {len(all_offers)} offer(s) but none had >= {required_qty} in stock."
                ),
                offers=ranked_insufficient,
            )

        ranked = sorted(sufficient, key=lambda o: _sort_key(o, deadline_days))
        span.set_attribute("offer_resolver.status", "ok")
        span.set_attribute("offer_resolver.offer_count", len(ranked))
        return OfferResolution(
            mpn=mpn,
            required_qty=required_qty,
            deadline_days=deadline_days,
            status="ok",
            offers=ranked,
            insufficient_offers=insufficient,
        )


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class OfferResolverServer(McpToolServer):
    """MCP wrapper exposing ``distributors.resolve_offers``.

    Takes already-constructed :class:`DistributorAdapter` instances —
    borrowed, not owned. Those same instances are also wrapped by a
    per-distributor ``DistributorMcpServer`` (``digikey.*``/``mouser.*``/
    ``nexar.*`` tools), which remains the sole owner of their HTTP client
    and OAuth token cache lifecycle; this server only reads through them.
    """

    def __init__(self, adapters: list[DistributorAdapter]) -> None:
        super().__init__(adapter_id="offer_resolver", version="0.1.0")
        self._adapters = adapters
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="distributors.resolve_offers",
                adapter_id="offer_resolver",
                name="Resolve Distributor Offers",
                description=(
                    "For each requested MPN, fan out to every configured "
                    "distributor (Digi-Key/Mouser/Nexar) and return ranked "
                    "purchasing offers accounting for required quantity, "
                    "stock sufficiency, MOQ-forced overbuy, and an optional "
                    "delivery deadline. A part absent from every configured "
                    "distributor (e.g. a specialty COTS module) returns "
                    "status='no_offers_found' — never an error."
                ),
                capability="distributor_offer_resolution",
                input_schema={
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "mpn": {
                                        "type": "string",
                                        "description": "Manufacturer Part Number.",
                                    },
                                    "required_qty": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "description": "Quantity needed.",
                                    },
                                    "deadline_days": {
                                        "type": ["integer", "null"],
                                        "description": (
                                            "Optional build deadline in days; "
                                            "offers with a longer lead time "
                                            "rank below ones that meet it."
                                        ),
                                    },
                                },
                                "required": ["mpn", "required_qty"],
                            },
                        },
                    },
                    "required": ["items"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_resolve_offers,
        )

    async def handle_resolve_offers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        items = self._parse_items(arguments)
        with tracer.start_as_current_span("offer_resolver.mcp.resolve_offers") as span:
            span.set_attribute("offer_resolver.item_count", len(items))
            resolutions = await asyncio.gather(
                *[
                    resolve_offers_for_item(
                        self._adapters,
                        mpn=item["mpn"],
                        required_qty=item["required_qty"],
                        deadline_days=item["deadline_days"],
                    )
                    for item in items
                ]
            )
            logger.info(
                "offer_resolver_batch_resolved",
                item_count=len(items),
                adapter_count=len(self._adapters),
            )
            return {"results": [resolution.model_dump(mode="json") for resolution in resolutions]}

    async def close(self) -> None:
        """Deliberate no-op — see class docstring: adapters are borrowed,
        not owned, and closing them here would double-close HTTP clients
        already owned by their per-distributor ``DistributorMcpServer``.
        """
        return

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_items(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        items = arguments.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("'items' is required and must be a non-empty array")

        parsed: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                raise ValueError(f"items[{index}] must be an object")

            mpn = raw.get("mpn")
            if not isinstance(mpn, str) or not mpn.strip():
                raise ValueError(f"items[{index}].mpn is required and must be a non-empty string")

            required_qty_raw = raw.get("required_qty")
            try:
                required_qty = int(required_qty_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"items[{index}].required_qty must be an integer") from exc
            if required_qty < 1:
                raise ValueError(f"items[{index}].required_qty must be >= 1")

            deadline_days_raw = raw.get("deadline_days")
            deadline_days: int | None = None
            if deadline_days_raw is not None:
                try:
                    deadline_days = int(deadline_days_raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"items[{index}].deadline_days must be an integer or null"
                    ) from exc

            parsed.append(
                {
                    "mpn": mpn.strip(),
                    "required_qty": required_qty,
                    "deadline_days": deadline_days,
                }
            )
        return parsed
