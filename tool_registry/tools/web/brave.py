"""Brave Search API provider + the guarded page fetcher (MET-7).

Brave was picked for v1 because it is a first-party index (not a Google
scraper that can break without notice), bills per request with a usable
free tier, and needs nothing but a single API key -- no OAuth dance like
Nexar/Digi-Key.

Key comes from ``BRAVE_API_KEY``. It is never read from a file in the repo
and never logged.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter
from tool_registry.tools.web.base import (
    PageContent,
    SearchHit,
    WebSearchError,
    WebSearchProvider,
)
from tool_registry.tools.web.pdf import DEFAULT_MAX_PAGES, MAX_PDF_BYTES, pdf_to_text
from tool_registry.tools.web.safety import (
    MAX_BYTES,
    MAX_REDIRECTS,
    REQUEST_TIMEOUT_SECONDS,
    UnsafeUrlError,
    assert_public_host,
    content_type_allowed,
    html_to_text,
    is_pdf,
    validate_url,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.web.brave")

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave's free tier is 1 request/second and rejects bursts with HTTP 429.
# Deliberately a touch under 1.0: pacing at exactly the published rate races
# the server's own window boundary, which is how a multi-search turn still
# collected a 429 in practice.
_RATE_PER_SECOND = 0.8

_MAX_RETRIES = 2
_BACKOFF_SECONDS = 1.5


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, when the server sends a usable one."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        # Seconds form only; the HTTP-date form is rare here and not worth
        # mis-parsing into a huge sleep.
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if 0 < seconds <= 30 else None


_USER_AGENT = "MetaForge/0.1 (+https://github.com/FidelOdok/MetaForge)"


class BraveSearchProvider(WebSearchProvider):
    """Brave Search API client.

    Construction is pure config -- no I/O, no key validation -- so the
    gateway boots identically whether or not the key works.

    Unlike the distributor adapters, a failed call RAISES rather than
    degrading to an empty list: for a search tool, "I could not look" and
    "nothing exists" are different answers and collapsing them makes the
    agent report a rate-limit as a fact about the world. See :meth:`search`.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self._rate_limiter = TokenBucketRateLimiter(rate=_RATE_PER_SECOND, burst=1)

    @property
    def name(self) -> str:
        return "Brave"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        """Ranked hits, or raise :class:`WebSearchError` if the call FAILED.

        An empty list means the index genuinely matched nothing. A failure
        raises. The original contract collapsed both into ``[]``, and it bit
        immediately in real use: a 429 from Brave's 1 req/s free tier was
        reported to the agent as an empty result set, and the agent stated
        "No results returned" as fact for a query that was never answered.
        A tool that cannot distinguish "nothing exists" from "I could not
        look" makes the model confidently wrong.
        """
        with tracer.start_as_current_span("web.brave.search") as span:
            span.set_attribute("web.query_length", len(query))
            span.set_attribute("web.limit", limit)
            if not self._api_key:
                # Normally unreachable: bootstrap skips registering the adapter
                # without a key, so the model never sees the tool at all.
                raise WebSearchError("web search is not configured (BRAVE_API_KEY is unset)")

            payload = await self._get_with_retry(query, limit, span=span)
            hits = self._parse(payload, limit=limit)
            span.set_attribute("web.result_count", len(hits))
            logger.info("brave_search", query=query[:80], count=len(hits))
            return hits

    async def _get_with_retry(self, query: str, limit: int, *, span: Any) -> dict[str, Any]:
        """One search request, retrying a rate-limit a few times, then raising.

        Brave's free tier is 1 req/s and a turn that fires several searches
        will brush against it even with the limiter, so a 429 is treated as
        *transient* — honour ``Retry-After`` when present, back off otherwise.
        Anything still failing after the retries is raised, never swallowed.
        """
        last_error = ""
        for attempt in range(_MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                resp = await self._client.get(
                    _SEARCH_URL,
                    params={"q": query, "count": limit},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": self._api_key,
                        "User-Agent": _USER_AGENT,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - transport failure
                span.record_exception(exc)
                last_error = str(exc)
                logger.warning("brave_search_transport_error", error=last_error, attempt=attempt)
                if attempt >= _MAX_RETRIES:
                    break
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue

            if resp.status_code == 429:
                last_error = "rate limited by Brave (HTTP 429)"
                wait = _retry_after_seconds(resp) or _BACKOFF_SECONDS * (attempt + 1)
                logger.warning("brave_search_rate_limited", attempt=attempt, wait_seconds=wait)
                if attempt >= _MAX_RETRIES:
                    break
                await asyncio.sleep(wait)
                continue

            try:
                resp.raise_for_status()
                # str(exc) on an httpx error carries the request URL but never
                # the headers, so the subscription token stays out of the logs.
                return dict(resp.json())
            except Exception as exc:  # noqa: BLE001 - 4xx/5xx or bad JSON
                span.record_exception(exc)
                last_error = str(exc)
                logger.warning("brave_search_failed", error=last_error, query=query[:80])
                break

        raise WebSearchError(
            f"web search failed and returned no data ({last_error}) — this is NOT "
            "an empty result set; the query was never answered, so do not report "
            "it as 'no results found'"
        )

    @staticmethod
    def _parse(payload: dict[str, Any], *, limit: int) -> list[SearchHit]:
        raw_results = (payload.get("web") or {}).get("results") or []
        hits: list[SearchHit] = []
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    # Brave calls the snippet "description"; it contains
                    # <strong> highlight markup around matched terms.
                    snippet=_strip_tags(str(item.get("description") or "")),
                    published=(item.get("page_age") or item.get("age") or None),
                )
            )
        return hits

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _strip_tags(value: str) -> str:
    """Remove Brave's <strong> highlight markup from a snippet."""
    out: list[str] = []
    depth = 0
    for char in value:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return " ".join("".join(out).split())


async def fetch_page(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = MAX_BYTES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> PageContent:
    """Fetch one page, refusing any hop that points at a non-public address.

    Redirects are followed MANUALLY (``follow_redirects=False``) so each
    ``Location`` is re-validated. Letting httpx follow them internally
    would check only the first URL -- an open redirector on a public host
    would then hand an attacker the internal network for free.

    ``max_bytes`` caps HTML/text bodies. PDFs are page-limited by
    ``max_pages`` instead and are never byte-truncated -- see ``_to_page``.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    current = validate_url(url)

    try:
        with tracer.start_as_current_span("web.fetch") as span:
            from urllib.parse import urlsplit  # local: only needed for the span attr

            span.set_attribute("web.host", urlsplit(current).hostname or "")
            for hop in range(MAX_REDIRECTS + 1):
                await assert_public_host(current)
                response = await http.get(
                    current,
                    headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain,*/*;q=0.5"},
                    follow_redirects=False,
                )
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("redirect response carried no Location header")
                    if hop >= MAX_REDIRECTS:
                        raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS})")
                    # Resolve relative Location values against the current URL.
                    current = validate_url(str(response.url.join(location)))
                    continue
                response.raise_for_status()
                return _to_page(response, current, max_bytes=max_bytes, max_pages=max_pages)
            raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS})")
    finally:
        if owns_client:
            await http.aclose()


def _to_page(response: httpx.Response, url: str, *, max_bytes: int, max_pages: int) -> PageContent:
    content_type = response.headers.get("content-type", "")
    if not content_type_allowed(content_type):
        raise UnsafeUrlError(
            f"content-type {content_type!r} is not readable -- web.fetch returns text and PDF only"
        )

    raw = response.content
    fetched = len(raw)

    if is_pdf(content_type):
        # Never truncate a PDF. Its cross-reference table lives at the END of
        # the file, so a tail-truncated PDF generally will not open at all --
        # the loud-truncation approach that works for HTML produces an
        # unparseable file here. Refuse with a reason instead.
        if fetched > MAX_PDF_BYTES:
            raise UnsafeUrlError(
                f"PDF is {fetched / 1_000_000:.1f} MB, over the "
                f"{MAX_PDF_BYTES / 1_000_000:.0f} MB limit -- truncating a PDF "
                "corrupts it, so it was not fetched"
            )
        title, text, stats = pdf_to_text(raw, max_pages=max_pages)
        page_limited = stats["pages_read"] < stats["pages_total"]
        logger.info(
            "web_fetch_pdf",
            url=url[:200],
            bytes=fetched,
            pages_total=stats["pages_total"],
            pages_read=stats["pages_read"],
        )
        if page_limited:
            # Say so in-band: the model must report "read 30 of 212 pages",
            # not imply it read the whole datasheet.
            text = (
                f"[Read pages 1-{stats['pages_read']} of {stats['pages_total']}. "
                "Later pages were NOT read.]\n" + text
            )
        return PageContent(
            url=url,
            title=title,
            text=text,
            content_type=content_type,
            truncated=page_limited,
            fetched_bytes=fetched,
        )

    truncated = fetched > max_bytes
    if truncated:
        raw = raw[:max_bytes]

    body = raw.decode(response.encoding or "utf-8", errors="replace")
    if "html" in content_type.lower():
        title, text = html_to_text(body)
    else:
        title, text = "", body

    logger.info(
        "web_fetch",
        url=url[:200],
        bytes=fetched,
        truncated=truncated,
        content_type=content_type.split(";", 1)[0],
    )
    return PageContent(
        url=url,
        title=title,
        text=text,
        content_type=content_type,
        truncated=truncated,
        fetched_bytes=fetched,
    )
