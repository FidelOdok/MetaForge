"""Tavily as a web.search provider beside Brave."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tool_registry.tools.web import tavily
from tool_registry.tools.web.base import WebSearchError
from tool_registry.tools.web.tavily import TavilySearchProvider, select_search_provider

_PAYLOAD = {
    "query": "LM76005 datasheet",
    "results": [
        {
            "title": "LM76005 data sheet | TI.com",
            "url": "https://www.ti.com/product/LM76005",
            "content": "  3.5-V to 60-V,\n 5-A synchronous   step-down converter. " * 30,
            "score": 0.91,
        },
        {"title": "", "url": "https://no-title.example"},
        {
            "title": "LM76005 news",
            "url": "https://news.example/lm76005",
            "content": "short",
            "published_date": "2026-01-04",
        },
    ],
}


def _client(status: int = 200, payload: Any = None, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else _PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_search_posts_the_query_with_a_bearer_key_and_parses_hits() -> None:
    seen: list[httpx.Request] = []
    provider = TavilySearchProvider(client=_client(seen=seen), api_key="tvly-test")
    hits = await provider.search("LM76005 datasheet", limit=5)
    (request,) = seen
    assert request.method == "POST" and str(request.url) == "https://api.tavily.com/search"
    assert request.headers["authorization"] == "Bearer tvly-test"
    body = json.loads(request.content)
    assert body["query"] == "LM76005 datasheet" and body["max_results"] == 5
    assert [h.url for h in hits] == [
        "https://www.ti.com/product/LM76005",
        "https://news.example/lm76005",
    ]
    # Whitespace collapsed and capped, so a long page does not flood the turn.
    assert "  " not in hits[0].snippet and len(hits[0].snippet) <= 400
    assert hits[1].published == "2026-01-04"
    assert provider.name == "Tavily"


async def test_no_results_is_an_empty_list_not_an_error() -> None:
    provider = TavilySearchProvider(client=_client(payload={"results": []}), api_key="k")
    assert await provider.search("nothing matches this", limit=3) == []


@pytest.mark.parametrize("status", [401, 403, 432, 500])
async def test_a_refused_or_failed_call_raises(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tavily, "_BACKOFF_SECONDS", 0.0)
    provider = TavilySearchProvider(client=_client(status=status, payload={}), api_key="k")
    with pytest.raises(WebSearchError, match="NOT an empty result set"):
        await provider.search("q", limit=3)


async def test_a_rate_limit_is_retried_then_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tavily, "_BACKOFF_SECONDS", 0.0)
    seen: list[httpx.Request] = []
    provider = TavilySearchProvider(client=_client(status=429, payload={}, seen=seen), api_key="k")
    with pytest.raises(WebSearchError, match="rate limited"):
        await provider.search("q", limit=3)
    assert len(seen) == 3


async def test_without_a_key_it_says_so() -> None:
    provider = TavilySearchProvider(client=_client(), api_key="")
    provider._api_key = ""  # noqa: SLF001 - no environment fallback in this test
    with pytest.raises(WebSearchError, match="TAVILY_API_KEY"):
        await provider.search("q")


@pytest.mark.parametrize(
    ("choice", "keys", "expected"),
    [
        ("auto", {"TAVILY_API_KEY": "t", "BRAVE_API_KEY": "b"}, "Tavily"),
        ("auto", {"BRAVE_API_KEY": "b"}, "Brave"),
        ("auto", {}, None),
        ("brave", {"TAVILY_API_KEY": "t", "BRAVE_API_KEY": "b"}, "Brave"),
        ("tavily", {"BRAVE_API_KEY": "b"}, None),  # named without its key: not swapped
        ("bing", {"TAVILY_API_KEY": "t"}, None),
    ],
)
def test_the_provider_follows_the_setting_and_the_keys(
    monkeypatch: pytest.MonkeyPatch, choice: str, keys: dict[str, str], expected: str | None
) -> None:
    for name in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", choice)
    provider = select_search_provider()
    assert (provider.name if provider else None) == expected
