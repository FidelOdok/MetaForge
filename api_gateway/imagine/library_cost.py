"""A linked part's price, from the component library's offers (MetaForge, 2026-11-12).

The studio's cost estimate adds a bought part's ``cost`` (``unit``, ``currency``, ``qty``,
``source``) when it is in pounds. A part researched once kept its price only in the studio
that found it. Now a real part that names its library entry (``library``) and has no cost
of its own gets one on each studio write, from the entry's supplier offers:

- only offers confirmed on a live page count; a search snapshot never sets a price;
- the price is the break that applies to the build quantity (``process.qty``, else 1);
- pounds are preferred, because the page adds only pounds; with no pound offer the
  cheapest confirmed offer is kept in its own currency, and the page says it is not added.
  No exchange rate is ever assumed.

The derived cost carries ``derived_from: "library"`` and is derived again on every write,
so a newer offer reaches the studio and a withdrawn one leaves it. A cost the agent wrote
itself is never touched.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

MARK = "library"
#: The currency the studio's estimate adds.
HOME_CURRENCY = "GBP"

Lookup = Callable[[str], "dict[str, Any] | None"]


def _build_qty(config: dict[str, Any]) -> int:
    raw = (config.get("process") or {}).get("qty") if isinstance(config, dict) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 1:
        return int(raw)
    return 1


def _price_at(offer: dict[str, Any], qty: int) -> tuple[float, int] | None:
    """The unit price of the largest break not above ``qty``, with that break's quantity."""
    best: tuple[float, int] | None = None
    for row in offer.get("breaks") or []:
        q, p = row.get("qty"), row.get("unit_price")
        if isinstance(q, int) and isinstance(p, (int, float)) and q <= qty:
            if best is None or q > best[1]:
                best = (float(p), q)
    return best


def best_offer(entry: dict[str, Any], qty: int) -> dict[str, Any] | None:
    """The cost to record for ``entry`` at ``qty``, or None when no confirmed offer prices it."""
    found: list[tuple[bool, float, dict[str, Any]]] = []
    for sup in entry.get("suppliers") or []:
        offer = sup.get("offer") if isinstance(sup, dict) else None
        if not isinstance(offer, dict) or offer.get("confirmed") is False:
            continue
        price = _price_at(offer, qty)
        currency = str(offer.get("currency") or "")
        if price is None or not currency:
            continue
        unit, at = price
        found.append(
            (
                currency != HOME_CURRENCY,
                unit,
                {
                    "unit": unit,
                    "currency": currency,
                    "qty": at,
                    "source": (
                        f"{sup.get('supplier')} {sup.get('sku')}, read {offer.get('read_on')} "
                        f"via {offer.get('via')}"
                    ),
                },
            )
        )
    if not found:
        return None
    found.sort(key=lambda row: (row[0], row[1]))
    return found[0][2]


def price_parts(config: dict[str, Any], lookup: Lookup) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with each linked real part's cost derived, and notes."""
    out = copy.deepcopy(config)
    comps = (out.get("context") or {}).get("components") if isinstance(out, dict) else None
    parts = comps.get("parts") if isinstance(comps, dict) else None
    if not isinstance(parts, list):
        return out, []
    qty = _build_qty(out)
    unpriced: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("standIn"):
            continue
        name = str(part.get(MARK) or "").strip()
        own = part.get("cost")
        if not name or (isinstance(own, dict) and own.get("derived_from") != MARK):
            continue  # not linked, or priced by the agent: leave it
        try:
            entry = lookup(name)
        except Exception as exc:
            logger.warning("imagine_library_price_lookup_failed", entry=name, error=str(exc))
            continue
        if entry is None:
            continue  # the library link check says so
        cost = best_offer(entry, qty)
        if cost is None:
            part.pop("cost", None)
            unpriced.append(str(part.get("id") or name))
            continue
        cost["source"] = f"library {name} v{entry.get('version')}: {cost['source']}"
        cost["derived_from"] = MARK
        part["cost"] = cost
    if not unpriced:
        return out, []
    return out, [
        f"{len(unpriced)} linked part(s) have no price confirmed on a live page at {qty} "
        f"unit(s), so the estimate leaves them out: {', '.join(unpriced)}. Record a supplier "
        "offer read from a live page (an unconfirmed offer is never used)."
    ]


def cached(lookup: Lookup) -> Lookup:
    """One library read per name for the length of one write."""
    memo: dict[str, dict[str, Any] | None] = {}

    def read(name: str) -> dict[str, Any] | None:
        if name not in memo:
            memo[name] = lookup(name)
        return memo[name]

    return read


def catalog_entry(name: str) -> dict[str, Any] | None:
    """The gateway's library entry of this name as a dict, or None when there is none."""
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import CatalogNotFoundError

    try:
        entry: dict[str, Any] = get_catalog().get(name, "component").as_dict()
    except CatalogNotFoundError:
        return None
    return entry
