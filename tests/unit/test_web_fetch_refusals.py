"""web.fetch when a site turns automated readers away (FORGE-693).

The responses are the ones recorded from the sandbox on 2026-10-26 with
web.fetch's own headers: DigiKey's product page answered 403 with a
Cloudflare challenge, and www.st.com held the connection open without ever
answering. No network: every response comes from ``httpx.MockTransport`` and
the public-host check is stubbed so no DNS lookup happens.
"""

from __future__ import annotations

import httpx
import pytest

from tool_registry.tools.web import brave
from tool_registry.tools.web.brave import fetch_page
from tool_registry.tools.web.mcp_adapter import WebMcpServer
from tool_registry.tools.web.refusals import SiteUnreachableError, explain

ST_PDF = "https://www.st.com/resource/en/datasheet/stm32f407vg.pdf"
DIGIKEY_PAGE = "https://www.digikey.com/en/products/detail/stmicroelectronics/STM32F407VGT6/2747117"

# The head of the page DigiKey's Cloudflare front served (trimmed).
CLOUDFLARE_CHALLENGE = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
    '</head><body><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page">'
    "</script></body></html>"
)


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _public(url: str) -> list[str]:
        return ["203.0.113.10"]

    monkeypatch.setattr(brave, "assert_public_host", _public)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _fetch(url: str, handler) -> SiteUnreachableError:
    client = _client(handler)
    try:
        with pytest.raises(SiteUnreachableError) as caught:
            await fetch_page(url, client=client)
    finally:
        await client.aclose()
    return caught.value


async def test_cloudflare_challenge_is_named_and_points_at_the_digikey_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text=CLOUDFLARE_CHALLENGE,
            headers={"content-type": "text/html; charset=UTF-8", "server": "cloudflare"},
        )

    message = str(await _fetch(DIGIKEY_PAGE, handler))
    assert "www.digikey.com" in message
    assert "Cloudflare bot challenge" in message
    assert "digikey.get_product" in message
    assert "Retrying the same URL will not help" in message


async def test_a_site_that_never_answers_says_so_rather_than_a_bare_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    message = str(await _fetch(ST_PDF, handler))
    assert message.startswith("www.st.com did not answer in time")
    assert "knowledge.ingest" in message
    assert "assumption" in message


async def test_a_dropped_connection_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("stream reset: INTERNAL_ERROR", request=request)

    message = str(await _fetch(ST_PDF, handler))
    assert "www.st.com dropped the connection" in message


async def test_a_plain_404_is_not_called_a_bot_wall() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope", headers={"content-type": "text/plain"})

    message = str(await _fetch("https://example.com/missing.pdf", handler))
    assert "HTTP 404" in message and "bot" not in message


async def test_a_refusal_after_a_redirect_names_the_host_that_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": DIGIKEY_PAGE})
        return httpx.Response(
            403,
            text=CLOUDFLARE_CHALLENGE,
            headers={"content-type": "text/html", "cf-mitigated": "challenge"},
        )

    message = str(await _fetch("https://example.com/part", handler))
    assert message.startswith("www.digikey.com answered HTTP 403")


async def test_fetch_asks_for_pdfs_by_name_and_says_who_it_is() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    client = _client(handler)
    try:
        await fetch_page("https://example.com/a.txt", client=client)
    finally:
        await client.aclose()
    assert "application/pdf" in seen["accept"]
    assert seen["user-agent"].startswith("MetaForge/")


async def test_the_model_gets_the_explanation_as_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tool_registry.tools.web import mcp_adapter

    async def _refused(url: str, **_: object) -> None:
        raise SiteUnreachableError(explain(url, httpx.ReadTimeout("t")))

    monkeypatch.setattr(mcp_adapter, "fetch_page", _refused)

    class _NoSearch:
        name = "none"

        async def search(self, query: str, *, limit: int = 10) -> list[object]:
            return []

        async def close(self) -> None:
            return None

    server = WebMcpServer(provider=_NoSearch())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"web.fetch could not read that page: www.st.com"):
        await server.handle_fetch({"url": ST_PDF})
