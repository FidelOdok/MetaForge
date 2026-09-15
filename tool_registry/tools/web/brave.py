"""Brave Search API provider + the guarded page fetcher (MET-7).

Brave was picked for v1 because it is a first-party index (not a Google
scraper that can break without notice), bills per request with a usable
free tier, and needs nothing but a single API key -- no OAuth dance like
Nexar/Digi-Key.

Key comes from ``BRAVE_API_KEY``. It is never read from a file in the repo
and never logged.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter
from tool_registry.tools.web.base import PageContent, SearchHit, WebSearchProvider
from tool_registry.tools.web.safety import (
    MAX_BYTES,
    MAX_REDIRECTS,
    REQUEST_TIMEOUT_SECONDS,
    UnsafeUrlError,
    assert_public_host,
    content_type_allowed,
    html_to_text,
    validate_url,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.web.brave")

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave's free tier is 1 request/second and rejects bursts with HTTP 429.
# Matching it here turns a hard failure into a short wait.
_RATE_PER_SECOND = 1.0

_USER_AGENT = "MetaForge/0.1 (+https://github.com/FidelOdok/MetaForge)"


class BraveSearchProvider(WebSearchProvider):
    """Brave Search API client.

    Construction is pure config -- no I/O, no key validation -- so the
    gateway boots identically whether or not the key works. A bad key
    surfaces as an empty result list plus a warning log, matching the
    distributor adapters' degraded-not-fatal contract.
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
        with tracer.start_as_current_span("web.brave.search") as span:
            span.set_attribute("web.query_length", len(query))
            span.set_attribute("web.limit", limit)
            if not self._api_key:
                logger.warning("brave_search_skipped", reason="BRAVE_API_KEY not set")
                return []
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
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001 - degrade to "no results"
                span.record_exception(exc)
                # str(exc) on an httpx error includes the request URL but
                # never the headers, so the subscription token stays out of
                # the logs.
                logger.warning("brave_search_failed", error=str(exc), query=query[:80])
                return []

            hits = self._parse(payload, limit=limit)
            span.set_attribute("web.result_count", len(hits))
            logger.info("brave_search", query=query[:80], count=len(hits))
            return hits

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
) -> PageContent:
    """Fetch one page, refusing any hop that points at a non-public address.

    Redirects are followed MANUALLY (``follow_redirects=False``) so each
    ``Location`` is re-validated. Letting httpx follow them internally
    would check only the first URL -- an open redirector on a public host
    would then hand an attacker the internal network for free.
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
                return _to_page(response, current, max_bytes=max_bytes)
            raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS})")
    finally:
        if owns_client:
            await http.aclose()


def _to_page(response: httpx.Response, url: str, *, max_bytes: int) -> PageContent:
    content_type = response.headers.get("content-type", "")
    if not content_type_allowed(content_type):
        raise UnsafeUrlError(
            f"content-type {content_type!r} is not textual -- web.fetch returns text only"
        )

    raw = response.content
    fetched = len(raw)
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
