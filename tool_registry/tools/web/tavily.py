"""Tavily Search API provider (a second ``web.search`` backend beside Brave).

Tavily is a search API built for agents: one POST, ranked results with a
content snippet per page. Like Brave it needs a single API key,
``TAVILY_API_KEY``, which is never read from a file in the repo and never
logged.

The contract is the same as Brave's (:class:`WebSearchProvider`): a completed
query that matched nothing is ``[]``; a call that failed raises
:class:`WebSearchError`, so an agent never reports "no results" for a query that
was never answered.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter
from tool_registry.tools.web.base import SearchHit, WebSearchError, WebSearchProvider
from tool_registry.tools.web.safety import REQUEST_TIMEOUT_SECONDS

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.web.tavily")

_SEARCH_URL = "https://api.tavily.com/search"

# Tavily's development keys allow about 100 requests a minute; pacing well under
# that keeps a turn that fires several searches clear of a 429.
_RATE_PER_SECOND = 1.5

_MAX_RETRIES = 2
_BACKOFF_SECONDS = 1.5

#: Characters of a result's content kept as its snippet.
_SNIPPET_CHARS = 400

_USER_AGENT = "MetaForge/0.1 (+https://github.com/FidelOdok/MetaForge)"


class TavilySearchProvider(WebSearchProvider):
    """Tavily Search API client. Construction is pure config, no I/O."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self._rate_limiter = TokenBucketRateLimiter(rate=_RATE_PER_SECOND, burst=2)

    @property
    def name(self) -> str:
        return "Tavily"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        """Ranked hits, or raise :class:`WebSearchError` if the call failed."""
        with tracer.start_as_current_span("web.tavily.search") as span:
            span.set_attribute("web.query_length", len(query))
            span.set_attribute("web.limit", limit)
            if not self._api_key:
                raise WebSearchError("web search is not configured (TAVILY_API_KEY is unset)")
            payload = await self._post_with_retry(query, limit, span=span)
            hits = self._parse(payload, limit=limit)
            span.set_attribute("web.result_count", len(hits))
            logger.info("tavily_search", query=query[:80], count=len(hits))
            return hits

    async def _post_with_retry(self, query: str, limit: int, *, span: Any) -> dict[str, Any]:
        last_error = ""
        for attempt in range(_MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                resp = await self._client.post(
                    _SEARCH_URL,
                    json={
                        "query": query,
                        "max_results": limit,
                        "search_depth": "basic",
                        "include_answer": False,
                    },
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": _USER_AGENT,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - transport failure
                span.record_exception(exc)
                last_error = str(exc)
                logger.warning("tavily_search_transport_error", error=last_error, attempt=attempt)
                if attempt >= _MAX_RETRIES:
                    break
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue

            if resp.status_code == 429:
                last_error = "rate limited by Tavily (HTTP 429)"
                logger.warning("tavily_search_rate_limited", attempt=attempt)
                if attempt >= _MAX_RETRIES:
                    break
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            if resp.status_code in (401, 403):
                # Not transient: a bad or exhausted key. str() of the error never
                # carries the Authorization header, so the key stays out of logs.
                last_error = f"Tavily refused the key (HTTP {resp.status_code})"
                logger.warning("tavily_search_unauthorised", status=resp.status_code)
                break
            if resp.status_code == 432:
                last_error = "Tavily plan limit reached (HTTP 432)"
                logger.warning("tavily_search_plan_limit")
                break

            try:
                resp.raise_for_status()
                return dict(resp.json())
            except Exception as exc:  # noqa: BLE001 - 4xx/5xx or bad JSON
                span.record_exception(exc)
                last_error = str(exc)
                logger.warning("tavily_search_failed", error=last_error, query=query[:80])
                break

        raise WebSearchError(
            f"web search failed and returned no data ({last_error}) — this is NOT "
            "an empty result set; the query was never answered, so do not report "
            "it as 'no results found'"
        )

    @staticmethod
    def _parse(payload: dict[str, Any], *, limit: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for item in (payload.get("results") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            snippet = " ".join(str(item.get("content") or "").split())
            if len(snippet) > _SNIPPET_CHARS:
                snippet = snippet[: _SNIPPET_CHARS - 1].rstrip() + "…"
            published = item.get("published_date")
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published=str(published) if published else None,
                )
            )
        return hits

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


#: Which provider ``web.search`` uses: ``auto`` (default), ``tavily`` or ``brave``.
PROVIDER_ENV = "WEB_SEARCH_PROVIDER"


def select_search_provider() -> WebSearchProvider | None:
    """The configured ``web.search`` provider, or ``None`` when none has a key.

    ``WEB_SEARCH_PROVIDER`` names one (``tavily`` or ``brave``); ``auto`` or unset
    takes Tavily when ``TAVILY_API_KEY`` is set, else Brave when ``BRAVE_API_KEY``
    is. A named provider without its key is not swapped for the other: the tool is
    not offered, and the log says why.
    """
    choice = (os.environ.get(PROVIDER_ENV) or "auto").strip().lower()
    tavily = bool(os.environ.get("TAVILY_API_KEY"))
    brave = bool(os.environ.get("BRAVE_API_KEY"))
    if choice not in ("auto", "tavily", "brave"):
        logger.warning("web_search_provider_unknown", choice=choice)
        return None
    if choice == "tavily" or (choice == "auto" and tavily):
        if not tavily:
            logger.warning("web_search_provider_unconfigured", provider="tavily")
            return None
        return TavilySearchProvider()
    if choice == "brave" or (choice == "auto" and brave):
        if not brave:
            logger.warning("web_search_provider_unconfigured", provider="brave")
            return None
        from tool_registry.tools.web.brave import BraveSearchProvider

        return BraveSearchProvider()
    return None
