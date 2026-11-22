"""Find a component: the library first, then live distributors, then adopt one (FORGE-899).

Finding a part took an agent many tools: ``component.search_*`` searched a separate catalog,
not the library; distributor search, pricing and library registration were separate calls
it had to piece together from a playbook. ``component.find`` does the searching in one call
and ``component.adopt`` turns a chosen distributor part into a library entry.

``component.find`` returns two ranked lists, each candidate saying where it came from:

- **library:** entries matching every word of the need (the library's own search), with a
  part made or sold only by blocked organisations marked ``blocked``;
- **distributors:** live search results from each configured distributor, best first (in
  production, in stock, with a datasheet), each marked ``in_library`` when the library
  already holds that order code, so a known part is reused rather than registered twice.

Each candidate lists what is still unknown (size, pins, price), so the agent knows what to
read from the datasheet before it relies on the part. Nothing is guessed: a distributor gives
an order code, a maker, a description, stock and a datasheet link; sizes and pins come only
from the datasheet the agent reads.

``component.adopt`` registers a distributor part in the library with that distributor's live
price breaks and stock as a dated offer. Sizes and interfaces go in only when the caller
passes them with their source. It is a write, held for the person's approval like any
library registration.

The distributor adapters are borrowed, as ``distributors.resolve_offers`` borrows them.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.distributors.base import DistributorAdapter, LifecycleStatus

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.components.finder")

_LIMITS = ResourceLimits(max_memory_mb=256, max_cpu_seconds=60, max_disk_mb=64)
_PER_ADAPTER_TIMEOUT_S = 10.0
#: The library's component families a part can be adopted as.
FAMILIES = ("module", "electronic", "battery", "other")


def _slug(text: str, n: int = 63) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:n].strip("_") or "part"


def _score_distributor(hit: dict[str, Any]) -> tuple[int, int, int]:
    """Higher is better: in production, in stock, with a datasheet."""
    active = 1 if hit.get("lifecycle") in ("active", "") else 0
    return (
        active,
        1 if (hit.get("stock_qty") or 0) > 0 else 0,
        1 if hit.get("datasheet_url") else 0,
    )


class ComponentFinderServer(McpToolServer):
    """``component.find`` and ``component.adopt`` over the library and the distributors."""

    def __init__(self, library: Any, adapters: list[DistributorAdapter], web: Any = None) -> None:
        super().__init__(adapter_id="component_finder", version="0.1.0")
        self._library = library
        self._adapters = adapters
        #: A web search provider (``tool_registry.tools.web.base.WebSearchProvider``), or None.
        self._web = web
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="component.find",
                adapter_id="component_finder",
                name="Find Component",
                description=(
                    "Find a real part for a need, in one call: the MetaForge library first, "
                    "then every configured distributor (DigiKey, Mouser, Nexar, Farnell, Arrow). "
                    "Returns two ranked lists, library and distributors, each candidate saying "
                    "where it came from, whether the library already holds it (in_library), "
                    "whether its maker or every seller is blocked, and what is still unknown "
                    "(size, pins, price). Prefer a library part; reuse it with its name. A "
                    "distributor part you choose is registered with component.adopt, after you "
                    "read its size and pins from its datasheet. need: words describing the part "
                    "(e.g. 'ESP32-S3 module', '12 pixel RGB LED ring 5V'); a part number "
                    "works too. "
                    "When neither finds enough, it searches the web (web: auto); a web result is "
                    "only a page (a maker's product page or a datasheet): read it with web.fetch "
                    "before trusting it, and register a part found that way with "
                    "library.register_component, every value sourced to the page."
                ),
                capability="component_search",
                input_schema={
                    "type": "object",
                    "properties": {
                        "need": {"type": "string", "description": "What the part must be."},
                        "region": {
                            "type": "string",
                            "description": "Only library parts with a seller serving this "
                            "region (UK, EU, Europe, Asia, Americas, Oceania, Africa, Global).",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                        "distributors": {
                            "type": "boolean",
                            "description": "Search distributors too (default true). False "
                            "searches only the library.",
                        },
                        "web": {
                            "type": "string",
                            "enum": ["auto", "on", "off"],
                            "description": "Search the web too: auto (default) only when the "
                            "library finds nothing and the distributors fewer than three; on "
                            "always; off never. Each web search is a paid API call.",
                        },
                    },
                    "required": ["need"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "library": {"type": "array"},
                        "distributors": {"type": "array"},
                        "web": {"type": "array"},
                        "searched": {"type": "object"},
                        "next": {"type": "string"},
                    },
                },
                phase=2,
                resource_limits=_LIMITS,
            ),
            handler=self.handle_find,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="component.adopt",
                adapter_id="component_finder",
                name="Adopt Component",
                description=(
                    "Register a distributor part from component.find in the library, so it is "
                    "reused from now on: its maker, order code, title and datasheet, and the "
                    "distributor's live price breaks and stock as a dated offer. Pass family "
                    "(module for a bought board or module, electronic for a single chip, button "
                    "or connector, battery, other) and, only from the datasheet you read, specs "
                    "(length_mm, width_mm, height_mm, each {value, unit, source_kind: datasheet, "
                    "source_ref: the page or URL}) and interfaces [{id, bus, dir}]. Never guess "
                    "a size or a pin. Held for the person's approval."
                ),
                capability="component_library_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mpn": {"type": "string"},
                        "manufacturer": {"type": "string"},
                        "distributor": {
                            "type": "string",
                            "description": "Which distributor's offer to record (its name as "
                            "component.find returned it).",
                        },
                        "family": {"type": "string", "enum": list(FAMILIES)},
                        "title": {"type": "string"},
                        "name": {
                            "type": "string",
                            "description": "Library name; derived if omitted.",
                        },
                        "datasheet": {"type": "string"},
                        "specs": {"type": "object"},
                        "interfaces": {"type": "array", "items": {"type": "object"}},
                        "ratings": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (
                                "Rows from the datasheet's rating tables, as "
                                "library.register_component takes them (FORGE-906)."
                            ),
                        },
                        "ecad": {
                            "type": "object",
                            "description": "{symbol, footprint, source_kind, source_ref}",
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["mpn", "manufacturer", "distributor", "family"],
                },
                output_schema={"type": "object", "properties": {"entry": {"type": "object"}}},
                phase=2,
                resource_limits=_LIMITS,
            ),
            handler=self.handle_adopt,
        )

    # ── find ─────────────────────────────────────────────────────────────

    async def _library_hits(
        self, need: str, region: str | None, limit: int
    ) -> list[dict[str, Any]]:
        if self._library is None:
            return []
        args: dict[str, Any] = {"q": need, "kinds": ["component"], "limit": limit}
        if region:
            args["region"] = region
        try:
            result = await self._library.search(args)
        except Exception as exc:  # noqa: BLE001 - a library outage leaves the distributors
            logger.warning("component_find_library_failed", error=str(exc))
            return []
        out = []
        for hit in result.get("results") or []:
            out.append(
                {
                    "source": "library",
                    "name": hit.get("name"),
                    "title": hit.get("title"),
                    "mpn": hit.get("mpn") or "",
                    "manufacturer": hit.get("manufacturer") or "",
                    "family": hit.get("family"),
                    "suppliers": hit.get("suppliers") or [],
                    "matched": hit.get("matched") or [],
                    **({"blocked": hit["blocked"]} if hit.get("blocked") else {}),
                    "use": f"set the part's library to '{hit.get('name')}'",
                }
            )
        return out

    async def _distributor_hits(
        self, need: str, limit: int
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        async def one(adapter: DistributorAdapter) -> tuple[str, list[Any] | str]:
            try:
                parts = await asyncio.wait_for(
                    adapter.search_parts(need, limit=limit), _PER_ADAPTER_TIMEOUT_S
                )
                return adapter.name, parts
            except TimeoutError:
                return adapter.name, "timed out"
            except Exception as exc:  # noqa: BLE001 - one distributor failing is reported, not fatal
                return adapter.name, f"failed: {exc}"[:200]

        outcomes = await asyncio.gather(*(one(a) for a in self._adapters))
        hits: list[dict[str, Any]] = []
        status: dict[str, str] = {}
        seen: set[tuple[str, str]] = set()
        for name, parts in outcomes:
            if isinstance(parts, str):
                status[name] = parts
                continue
            status[name] = f"{len(parts)} found"
            for p in parts:
                key = (p.mpn.strip().lower(), name)
                if key in seen:
                    continue
                seen.add(key)
                lifecycle = (
                    p.lifecycle_status.value
                    if isinstance(p.lifecycle_status, LifecycleStatus)
                    else str(p.lifecycle_status or "")
                )
                hits.append(
                    {
                        "source": name,
                        "mpn": p.mpn,
                        "manufacturer": p.manufacturer,
                        "description": p.description,
                        "distributor_pn": p.distributor_pn,
                        "stock_qty": p.stock_qty,
                        "lead_time_days": p.lead_time_days,
                        "lifecycle": "" if lifecycle.lower() == "unknown" else lifecycle.lower(),
                        "datasheet_url": p.datasheet_url,
                        "product_url": p.product_url,
                        "unknown": ["size", "pins", "price (component.adopt reads it)"],
                    }
                )
        hits.sort(key=_score_distributor, reverse=True)
        return hits, status

    async def _web_hits(self, need: str, limit: int) -> tuple[list[dict[str, Any]], str]:
        """Pages about the part: a maker's product page or a datasheet, unverified."""
        try:
            hits = await asyncio.wait_for(
                self._web.search(f"{need} datasheet", limit=limit), _PER_ADAPTER_TIMEOUT_S
            )
        except TimeoutError:
            return [], "timed out"
        except Exception as exc:  # noqa: BLE001 - the web failing leaves the other lists
            return [], f"failed: {exc}"[:200]
        out = []
        for h in hits:
            url = str(h.url)
            out.append(
                {
                    "source": "web",
                    "title": h.title,
                    "url": url,
                    "snippet": h.snippet,
                    "page": "datasheet" if url.lower().split("?")[0].endswith(".pdf") else "page",
                    "unknown": ["maker", "order code", "size", "pins", "price"],
                }
            )
        return out, f"{len(out)} found"

    async def handle_find(self, arguments: dict[str, Any]) -> dict[str, Any]:
        need = str(arguments.get("need") or "").strip()
        if not need:
            raise ValueError("component.find: 'need' is required")
        limit = max(1, min(int(arguments.get("limit") or 8), 25))
        region = str(arguments.get("region") or "").strip() or None
        with tracer.start_as_current_span("component.find") as span:
            span.set_attribute("component_find.limit", limit)
            library = await self._library_hits(need, region, limit)
            distributors: list[dict[str, Any]] = []
            status: dict[str, str] = {}
            if arguments.get("distributors", True) is not False and self._adapters:
                distributors, status = await self._distributor_hits(need, limit)
                held = {str(h.get("mpn") or "").lower() for h in library if h.get("mpn")}
                for hit in distributors:
                    if hit["mpn"].lower() in held:
                        hit["in_library"] = True
                for hit in distributors:
                    if not hit.get("in_library") and await self._held(hit["mpn"]):
                        hit["in_library"] = True
            mode = str(arguments.get("web") or "auto")
            web: list[dict[str, Any]] = []
            web_status = "not searched"
            if self._web is None:
                web_status = "no web search is configured"
            elif mode == "on" or (mode == "auto" and not library and len(distributors) < 3):
                web, web_status = await self._web_hits(need, limit)
            span.set_attribute("component_find.library", len(library))
            span.set_attribute("component_find.distributors", len(distributors))
            span.set_attribute("component_find.web", len(web))
        logger.info(
            "component_find", library=len(library), distributors=len(distributors), web=len(web)
        )
        if library:
            nxt = "Prefer a library part: set the studio part's library to its name."
        elif distributors:
            nxt = (
                "No library part fits. Read the best candidate's datasheet for its size and "
                "pins, then register it with component.adopt."
            )
        elif web:
            nxt = (
                "Only web pages found: no distributor carries it. Read the most likely page "
                "with web.fetch; if it is the part, register it with library.register_component, "
                "its maker, order code, size and pins each sourced to that page."
            )
        else:
            nxt = "Nothing found. Try other words, a part number, or fewer constraints."
        return {
            "library": library,
            "distributors": distributors[: limit * 2],
            "web": web,
            "searched": {
                "library": self._library is not None,
                "web": web_status,
                "distributors": status
                or ({} if self._adapters else {"none": "no distributor is configured"}),
            },
            "next": nxt,
        }

    async def _held(self, mpn: str) -> bool:
        """The library already holds this order code (searched by the code itself)."""
        if self._library is None or not mpn:
            return False
        try:
            result = await self._library.search({"q": mpn, "kinds": ["component"], "limit": 3})
        except Exception:  # noqa: BLE001 - best effort
            return False
        return any(
            str(h.get("mpn") or "").lower() == mpn.lower() for h in result.get("results") or []
        )

    # ── adopt ────────────────────────────────────────────────────────────

    async def handle_adopt(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mpn = str(arguments.get("mpn") or "").strip()
        maker = str(arguments.get("manufacturer") or "").strip()
        dist = str(arguments.get("distributor") or "").strip()
        family = str(arguments.get("family") or "").strip()
        if not (mpn and maker and dist):
            raise ValueError("component.adopt: mpn, manufacturer and distributor are required")
        if family not in FAMILIES:
            raise ValueError(f"component.adopt: family must be one of {', '.join(FAMILIES)}")
        if self._library is None:
            raise ValueError("component.adopt: no library is configured")
        if await self._held(mpn):
            raise ValueError(f"component.adopt: the library already holds {mpn}; use that entry")
        adapter = next((a for a in self._adapters if a.name.lower() == dist.lower()), None)
        if adapter is None:
            names = ", ".join(a.name for a in self._adapters) or "none"
            raise ValueError(f"component.adopt: distributor {dist!r} is not configured ({names})")
        with tracer.start_as_current_span("component.adopt") as span:
            span.set_attribute("component.mpn", mpn)
            found = [
                p for p in await adapter.search_parts(mpn, limit=5) if p.mpn.lower() == mpn.lower()
            ]
            part = found[0] if found else None
            breaks = await adapter.get_pricing(mpn)
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            offer: dict[str, Any] = {
                "read_on": today,
                "via": f"{adapter.name} API (component.adopt) read {today}",
                "confirmed": True,
            }
            if breaks:
                currency = breaks[0].currency
                rows = sorted(
                    {b.quantity: b.unit_price for b in breaks if b.currency == currency}.items()
                )
                offer["currency"] = currency
                offer["breaks"] = [{"qty": q, "unit_price": p} for q, p in rows]
            if part is not None:
                offer["stock"] = part.stock_qty
                if part.lead_time_days is not None:
                    offer["lead_time_days"] = part.lead_time_days
            supplier: dict[str, Any] = {
                "supplier": adapter.name,
                "sku": (part.distributor_pn if part and part.distributor_pn else mpn),
                "offer": offer,
            }
            if part is not None and part.product_url:
                supplier["url"] = part.product_url
            data: dict[str, Any] = {
                "name": str(arguments.get("name") or _slug(f"{maker} {mpn}")),
                "title": str(
                    arguments.get("title")
                    or (f"{maker} {mpn}: {part.description}" if part else f"{maker} {mpn}")
                )[:200],
                "family": family,
                "origin": "purchased",
                "manufacturer": maker,
                "mpn": mpn,
                "datasheet": str(
                    arguments.get("datasheet") or (part.datasheet_url if part else "") or ""
                ),
                "suppliers": [supplier],
                "notes": str(arguments.get("notes") or "")
                + f"\nAdopted from {adapter.name} by component.adopt on {today}.",
            }
            if arguments.get("specs"):
                data["specs"] = arguments["specs"]
            if arguments.get("interfaces"):
                data["interfaces"] = arguments["interfaces"]
            lifecycle = adopted_lifecycle(part, adapter.name, today)
            if lifecycle:
                data["lifecycle"] = lifecycle
            for key in ("ratings", "ecad"):
                if arguments.get(key):
                    data[key] = arguments[key]
            result = await self._library.register_entry("component", data, "component.adopt")
        logger.info("component_adopted", mpn=mpn, distributor=adapter.name)
        return result


#: A distributor's lifecycle word, as the library records it (FORGE-906).
_LIFECYCLE = {"ACTIVE": "active", "NRND": "nrnd", "EOL": "eol", "OBSOLETE": "obsolete"}


def adopted_lifecycle(part: Any, distributor: str, today: str) -> dict[str, Any]:
    """The lifecycle the distributor reported, sourced to it, or nothing.

    UNKNOWN (what a distributor says when it does not know) is left out
    rather than recorded, so the part shows its lifecycle as missing.
    """
    status = _LIFECYCLE.get(str(getattr(part, "lifecycle_status", "") or "").upper())
    if part is None or status is None:
        return {}
    return {
        "status": status,
        "source_kind": "supplier",
        "source_ref": getattr(part, "product_url", "") or f"{distributor} API",
        "read_on": today,
    }
