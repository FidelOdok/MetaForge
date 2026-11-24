"""web.extract: Tavily Extract as a fallback reader for pages web.fetch cannot open."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tool_registry.tools.web import mcp_adapter
from tool_registry.tools.web.mcp_adapter import WebMcpServer
from tool_registry.tools.web.tavily import TavilyExtractor

URL = "https://www.mouser.com/datasheet/2/783/bst_bmi423_ds000-3215036.pdf"
TEXT = "BMI423 datasheet. Page 287: gyroscope noise density 3.8 mdps/sqrt(Hz). " * 400


def _extractor(
    payload: dict[str, Any] | None = None,
    status: int = 200,
    seen: list[httpx.Request] | None = None,
) -> TavilyExtractor:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        body = payload if payload is not None else {"results": [{"url": URL, "raw_content": TEXT}]}
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TavilyExtractor(client=client, api_key="tvly-test")


@pytest.fixture(autouse=True)
def _public(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(_url: str) -> None:
        return None

    monkeypatch.setattr(mcp_adapter, "assert_public_host", public)


def _server(extractor: TavilyExtractor | None) -> WebMcpServer:
    return WebMcpServer(provider=None, extractor=extractor)


def test_web_extract_is_listed_only_with_a_tavily_key() -> None:
    assert "web.extract" in _server(_extractor()).tool_ids
    assert "web.extract" not in _server(None).tool_ids


async def test_it_reads_through_tavily_and_labels_the_text() -> None:
    seen: list[httpx.Request] = []
    out = await _server(_extractor(seen=seen)).handle_extract({"url": URL, "max_chars": 1000})
    (request,) = seen
    assert str(request.url) == "https://api.tavily.com/extract"
    assert request.headers["authorization"] == "Bearer tvly-test"
    assert json.loads(request.content)["urls"] == [URL]
    assert out["read_via"] == "Tavily Extract" and out["total_chars"] == len(TEXT.strip())
    assert "Read via Tavily Extract" in out["text"] and "UNTRUSTED WEB CONTENT" in out["text"]
    assert out["next_start_char"] == 1000


async def test_reading_on_uses_the_cached_text_and_ends() -> None:
    seen: list[httpx.Request] = []
    server = _server(_extractor(seen=seen))
    first = await server.handle_extract({"url": URL, "max_chars": 20_000})
    rest = await server.handle_extract(
        {"url": URL, "start_char": first["next_start_char"], "max_chars": 100_000}
    )
    assert len(seen) == 1  # the second part is not billed again
    assert rest["next_start_char"] is None and "More follows" not in rest["text"]


async def test_a_page_tavily_cannot_read_is_an_error_with_its_reason() -> None:
    failed = {"results": [], "failed_results": [{"url": URL, "error": "timeout"}]}
    with pytest.raises(ValueError, match="timeout"):
        await _server(_extractor(payload=failed)).handle_extract({"url": URL})


@pytest.mark.parametrize(("status", "why"), [(401, "refused the key"), (432, "plan limit")])
async def test_a_refused_key_or_plan_limit_says_so(status: int, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        await _server(_extractor(payload={}, status=status)).handle_extract({"url": URL})


async def test_a_private_address_never_goes_to_the_third_party(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the real public-address check
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError, match="refused"):
        await _server(_extractor(seen=seen)).handle_extract({"url": "http://127.0.0.1/secret"})
    assert seen == []
