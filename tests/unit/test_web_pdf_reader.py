"""Reading a PDF's pages: text layer, then OCR, then a vision model, and page ranges."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest

from tests.unit.test_web_tools_pdf_and_errors import _make_pdf
from tool_registry.tools.web import pdf_reader
from tool_registry.tools.web.pdf import PdfExtractionError
from tool_registry.tools.web.pdf_reader import parse_pages, read_pdf


def _mixed_pdf(text_pages: int, blank_pages: int) -> bytes:
    """``text_pages`` pages with a text layer, then ``blank_pages`` image-only ones."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(_make_pdf(pages=text_pages))).pages:
        writer.add_page(page)
    for _ in range(blank_pages):
        writer.add_blank_page(width=300, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@dataclass
class Reply:
    text: str
    provider: str = "anthropic"
    model: str = "claude-test"


@pytest.fixture
def ocr(monkeypatch: pytest.MonkeyPatch) -> dict[int, tuple[str, float]]:
    """OCR stubbed per page: ``{page: (text, confidence)}``; missing pages read nothing."""
    readings: dict[int, tuple[str, float]] = {}
    monkeypatch.setattr(pdf_reader, "ocr_available", lambda: True)
    monkeypatch.setattr(pdf_reader, "render_page", lambda _raw, index, scale=2.8: index + 1)
    monkeypatch.setattr(pdf_reader, "_png", lambda image: str(image).encode())
    monkeypatch.setattr(pdf_reader, "ocr_image", lambda page: readings.get(page, ("", 0.0)))
    return readings


def test_page_ranges_parse_in_order_and_refuse_bad_ones() -> None:
    assert parse_pages("3, 1-2, 2", 10) == [3, 1, 2]
    assert parse_pages("8-20", 10) == [8, 9, 10]  # clipped to the last page
    for bad in ("0", "5-3", "x", "11", ""):
        with pytest.raises(ValueError):
            parse_pages(bad, 10)
    with pytest.raises(ValueError, match="at most 100"):
        parse_pages("1-150", 400)


async def test_chosen_pages_are_read_and_labelled() -> None:
    reading = await read_pdf(_make_pdf(pages=12), pages="11,3")
    assert [p.number for p in reading.pages] == [11, 3]
    text = reading.text()
    assert "[page 11]" in text and "page 11" in text and "page 4" not in text
    assert reading.methods() == {"text": [11, 3]}


async def test_a_scanned_page_is_read_by_ocr_and_labelled(ocr: dict[int, Any]) -> None:
    ocr[3] = ("Supply voltage VDD 1.71 3.6 V", 0.93)
    reading = await read_pdf(_mixed_pdf(2, 1), max_pages=3)
    assert reading.methods() == {"text": [1, 2], "ocr": [3]}
    assert "[page 3 · read by OCR, confidence 0.93: confirm values before use]" in reading.text()


async def test_a_page_ocr_reads_badly_goes_to_the_vision_model(ocr: dict[int, Any]) -> None:
    ocr[2] = ("Vdd l.7l 3.G V", 0.41)
    asked: list[bytes] = []

    async def vision(image: bytes, mime: str, prompt: str) -> Reply:
        asked.append(image)
        assert mime == "image/png" and "Transcribe" in prompt
        return Reply("| Parameter | Min | Max |\n| VDD | 1.71 V | 3.6 V |")

    reading = await read_pdf(_mixed_pdf(1, 2), max_pages=3, vision_call=vision)
    assert reading.methods() == {"text": [1], "vision": [2, 3]}
    assert "transcribed by vision model anthropic/claude-test" in reading.text()
    assert len(asked) == 2


async def test_without_a_vision_model_low_confidence_ocr_is_kept_and_labelled(
    ocr: dict[int, Any],
) -> None:
    ocr[2] = ("Vdd l.7l V", 0.41)
    reading = await read_pdf(_mixed_pdf(1, 1), max_pages=2)
    assert reading.methods() == {"text": [1], "ocr": [2]}
    assert any("no vision model" in note for note in reading.notes)


async def test_vision_reads_at_most_four_pages_a_call(ocr: dict[int, Any]) -> None:
    async def vision(image: bytes, mime: str, prompt: str) -> Reply:
        return Reply("transcribed")

    reading = await read_pdf(_mixed_pdf(0, 6), max_pages=6, vision_call=vision)
    assert reading.methods() == {"vision": [1, 2, 3, 4], "none": [5, 6]}
    assert "a vision model reads at most 4 pages a call" in reading.text()


async def test_reader_text_tries_nothing_else(ocr: dict[int, Any]) -> None:
    ocr[2] = ("would have been read", 0.99)
    reading = await read_pdf(_mixed_pdf(1, 1), max_pages=2, reader="text")
    assert reading.methods() == {"text": [1], "none": [2]}


async def test_reader_ocr_reads_even_pages_with_a_text_layer(ocr: dict[int, Any]) -> None:
    ocr[1] = ("OCR of page one", 0.95)
    reading = await read_pdf(_make_pdf(pages=1), reader="ocr")
    assert reading.methods() == {"ocr": [1]}


async def test_nothing_readable_is_an_error_that_says_where_to_look() -> None:
    with pytest.raises(PdfExtractionError, match="maker's own copy"):
        await read_pdf(_mixed_pdf(0, 2), max_pages=2)
