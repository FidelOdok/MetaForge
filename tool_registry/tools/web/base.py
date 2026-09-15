"""Web search provider contract (MET-7).

Mirrors ``tool_registry/tools/distributors/base.py``: an ABC plus plain
result models, so the MCP wrapper in ``mcp_adapter.py`` is written once and
a second provider (Tavily, SerpAPI, ...) is a new subclass rather than a
new tool surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """One ranked result from a web search."""

    title: str
    url: str
    snippet: str = ""
    # Provider-reported publication date, when it supplies one. Free-text
    # rather than a date type -- providers return wildly inconsistent
    # formats ("2 days ago", "2026-01-04", "January 2026") and normalising
    # them would invent precision the source never had.
    published: str | None = None


class PageContent(BaseModel):
    """One fetched page, reduced to text."""

    url: str
    """The FINAL url after redirects -- not necessarily the one requested."""
    title: str = ""
    text: str = ""
    content_type: str = ""
    # True when the page body hit the byte cap and was cut. Surfaced to the
    # model so it reports "partial" rather than treating a truncated page as
    # the whole document (the same loud-truncation discipline as MET-568).
    truncated: bool = False
    fetched_bytes: int = 0


class WebSearchProvider(ABC):
    """A keyword web-search backend."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. ``"Brave"``."""

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        """Ranked hits for ``query``, or raise :class:`WebSearchError` on failure.

        Deliberately the OPPOSITE of ``DistributorAdapter.search_parts``'s
        contract: a failed call here must raise, never degrade to ``[]``.
        The two are indistinguishable to a caller that only sees an empty
        list -- a 429/unreachable-provider read as "the index genuinely
        matched nothing," and the agent stated that as fact for a query
        that was never answered. Return ``[]`` only for a real, completed
        "no results" outcome.
        """

    async def close(self) -> None:
        """Release any HTTP resources. Default: nothing to do."""
        return None


class WebSearchError(RuntimeError):
    """Raised for a search that FAILED -- caller-fixable bad arguments
    (``coerce_limit``) as well as provider-side failures (unconfigured key,
    rate-limited, unreachable -- see :class:`WebSearchProvider`). Never
    raised for a completed query that genuinely matched nothing; that's an
    empty list, not an error."""


DEFAULT_LIMIT = 10
MAX_LIMIT = 20
"""Brave returns at most 20 results per request on the free tier."""


def coerce_limit(raw: object, *, default: int = DEFAULT_LIMIT) -> int:
    """Validate a caller-supplied ``limit``."""
    if raw is None:
        return default
    try:
        limit = int(raw)  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise WebSearchError("'limit' must be an integer") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise WebSearchError(f"'limit' must be in [1, {MAX_LIMIT}]")
    return limit


class ProviderQuota(BaseModel):
    """Optional quota/rate metadata a provider may report back."""

    remaining: int | None = Field(default=None)
    reset_seconds: int | None = Field(default=None)
