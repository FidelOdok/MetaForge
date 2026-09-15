"""PDF support + failure-vs-empty for web.search.

Both are regressions found by USING the tool, not by review:

1. A 429 from Brave's 1 req/s free tier was swallowed into ``[]``, so the
   agent told the user "No results returned" for a query that was never
   answered. A search tool must distinguish "nothing exists" from "I could
   not look".
2. ``web.fetch`` refused every PDF, which is most of what a hardware agent
   needs to read (datasheets, app notes, reference manuals).
"""

from __future__ import annotations

import io

import httpx
import pytest

from tool_registry.tools.web.base import WebSearchError
from tool_registry.tools.web.brave import BraveSearchProvider, fetch_page
from tool_registry.tools.web.mcp_adapter import WebMcpServer
from tool_registry.tools.web.pdf import PdfExtractionError, pdf_to_text
from tool_registry.tools.web.safety import content_type_allowed, is_pdf

# ---------------------------------------------------------------------------
# Helpers — build a real PDF with pypdf so the tests exercise a real parse
# ---------------------------------------------------------------------------


def _make_pdf(pages: int = 1, text: str = "Vdd 3.3 V Iq 12 uA", title: str = "") -> bytes:
    """A genuine multi-page PDF with extractable text, built with NO extra
    dependency.

    Deliberately hand-rolled rather than using reportlab: reportlab is present
    in dev venvs only as a transitive of an undeclared package, so a
    reportlab-gated test would SKIP in CI and the PDF feature would ship with
    no real coverage — the exact trap that left pypdf undeclared.
    """
    objects: list[bytes] = [b"", b""]  # 1 = catalog, 2 = pages (filled last)

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for i in range(pages):
        stream = f"BT /F1 12 Tf 20 150 Td ({text} page {i + 1}) Tj ET".encode()
        cid = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_ids.append(
            add(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents "
                + str(cid).encode()
                + b" 0 R /Resources << /Font << /F1 "
                + str(font_id).encode()
                + b" 0 R >> >> >>"
            )
        )

    info_id = add(b"<< /Title (" + title.encode() + b") >>") if title else None

    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    objects[1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(pages).encode() + b" >>"
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(idx).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_at = len(out)
    size = len(objects) + 1
    out += b"xref\n0 " + str(size).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = b"<< /Size " + str(size).encode() + b" /Root 1 0 R"
    if info_id:
        trailer += b" /Info " + str(info_id).encode() + b" 0 R"
    out += b"trailer\n" + trailer + b" >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    return bytes(out)


# ---------------------------------------------------------------------------
# 1. Failure is not emptiness
# ---------------------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry tests fast — the backoff behaviour itself is asserted by
    the retry-count and Retry-After tests, not by wall-clock sleeping."""
    monkeypatch.setattr("tool_registry.tools.web.brave._BACKOFF_SECONDS", 0.0)


async def test_rate_limit_raises_instead_of_reporting_no_results() -> None:
    """THE regression: a 429 must never look like an empty result set."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "slow down"})

    provider = BraveSearchProvider(client=_client(handler), api_key="k")
    provider._rate_limiter._rate = 1000.0  # don't actually sleep the test
    with pytest.raises(WebSearchError) as excinfo:
        await provider.search("robot dog leg actuator brushless QDD")

    assert "429" in str(excinfo.value) or "rate limited" in str(excinfo.value)
    # The message must actively warn the model off the wrong conclusion.
    assert "not" in str(excinfo.value).lower()
    assert "no results" in str(excinfo.value).lower()
    assert calls["n"] > 1, "a transient 429 should be retried before giving up"


async def test_rate_limit_that_clears_on_retry_succeeds() -> None:
    calls = {"n": 0}
    payload = {
        "web": {"results": [{"title": "T", "url": "https://x.example/", "description": "d"}]}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0.01"})
        return httpx.Response(200, json=payload)

    provider = BraveSearchProvider(client=_client(handler), api_key="k")
    provider._rate_limiter._rate = 1000.0
    hits = await provider.search("anything")
    assert len(hits) == 1
    assert calls["n"] == 2


async def test_genuinely_empty_results_still_return_empty_list() -> None:
    """The other half: a real miss must NOT raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    provider = BraveSearchProvider(client=_client(handler), api_key="k")
    assert await provider.search("zzzz no such thing") == []


async def test_server_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = BraveSearchProvider(client=_client(handler), api_key="k")
    provider._rate_limiter._rate = 1000.0
    with pytest.raises(WebSearchError):
        await provider.search("anything")


async def test_missing_key_raises_rather_than_returning_empty() -> None:
    provider = BraveSearchProvider(client=_client(lambda r: httpx.Response(200)), api_key="")
    with pytest.raises(WebSearchError, match="not configured"):
        await provider.search("anything")


async def test_search_failure_reaches_the_model_as_a_tool_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    provider = BraveSearchProvider(client=_client(handler), api_key="k")
    provider._rate_limiter._rate = 1000.0
    server = WebMcpServer(provider=provider)
    with pytest.raises(ValueError, match="web.search failed"):
        await server.handle_search({"query": "x"})


# ---------------------------------------------------------------------------
# 2. PDF support
# ---------------------------------------------------------------------------


def test_pdf_content_type_is_now_allowed() -> None:
    assert content_type_allowed("application/pdf") is True
    assert is_pdf("application/pdf") is True
    assert is_pdf("text/html") is False


def test_pdf_to_text_extracts_content_and_page_counts() -> None:
    title, text, stats = pdf_to_text(_make_pdf(pages=3, title="STM32H743 Datasheet"))
    assert "Vdd 3.3 V" in text
    assert stats == {"pages_total": 3, "pages_read": 3}
    assert title == "STM32H743 Datasheet"


def test_pdf_page_limit_is_reported_not_hidden() -> None:
    _, text, stats = pdf_to_text(_make_pdf(pages=10), max_pages=3)
    assert stats == {"pages_total": 10, "pages_read": 3}
    assert "page 3" in text
    assert "page 4" not in text


async def test_fetch_reads_a_pdf_and_announces_partial_reads() -> None:
    body = _make_pdf(pages=8)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    client = _client(handler)
    try:
        page = await fetch_page("https://example.com/ds.pdf", client=client, max_pages=2)
    finally:
        await client.aclose()

    assert "Vdd 3.3 V" in page.text
    assert page.truncated is True
    # In-band, so the model cannot claim it read the whole datasheet.
    assert "Read pages 1-2 of 8" in page.text


async def test_fetch_reads_a_short_pdf_whole_without_a_partial_marker() -> None:
    body = _make_pdf(pages=2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    client = _client(handler)
    try:
        page = await fetch_page("https://example.com/ds.pdf", client=client, max_pages=30)
    finally:
        await client.aclose()

    assert page.truncated is False
    assert "Read pages" not in page.text


async def test_oversized_pdf_is_refused_not_truncated() -> None:
    """Truncating a PDF corrupts it — its xref table is at the END of the
    file — so an oversize PDF must be refused with a reason."""
    from tool_registry.tools.web.pdf import MAX_PDF_BYTES
    from tool_registry.tools.web.safety import UnsafeUrlError

    body = b"%PDF-1.4" + b"\x00" * (MAX_PDF_BYTES + 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    client = _client(handler)
    try:
        with pytest.raises(UnsafeUrlError, match="truncating a PDF corrupts it"):
            await fetch_page("https://example.com/huge.pdf", client=client)
    finally:
        await client.aclose()


def test_corrupt_pdf_reports_a_reason() -> None:
    with pytest.raises(PdfExtractionError, match="could not parse"):
        pdf_to_text(b"not a pdf at all")


def test_pdf_with_no_extractable_text_says_it_is_probably_a_scan() -> None:
    """A scanned datasheet yields zero characters — that must be reported,
    not returned as an empty-but-successful page."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(PdfExtractionError, match="scan"):
        pdf_to_text(buf.getvalue())


async def test_pdf_extraction_failure_reaches_the_model_as_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PdfExtractionError must be mapped to a reportable tool error, not
    propagate raw or be swallowed into an empty page."""
    from tool_registry.tools.web import mcp_adapter

    async def _boom(*args: object, **kwargs: object) -> object:
        raise PdfExtractionError("no extractable text — most likely a scan")

    monkeypatch.setattr(mcp_adapter, "fetch_page", _boom)
    server = WebMcpServer(
        provider=BraveSearchProvider(client=_client(lambda r: httpx.Response(200)), api_key="k")
    )
    with pytest.raises(ValueError, match="could not read that PDF"):
        await server.handle_fetch({"url": "https://example.com/x.pdf"})


@pytest.mark.parametrize("bad", [0, 101, "x"])
async def test_max_pages_is_validated(bad: object) -> None:
    server = WebMcpServer(
        provider=BraveSearchProvider(client=_client(lambda r: httpx.Response(200)), api_key="k")
    )
    with pytest.raises(ValueError, match="max_pages"):
        await server.handle_fetch({"url": "https://example.com/", "max_pages": bad})
