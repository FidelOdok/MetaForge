"""PDF text extraction for ``web.fetch``.

Datasheets are the single most valuable source for hardware work and they are
almost always PDFs, so refusing them made ``web.fetch`` close to useless for
its main job. Extraction is deliberately separate from ``safety.py`` (which
owns SSRF and HTML sanitation) because a PDF needs a real parser, not a
decode.

Three things differ from the HTML path and each is load-bearing:

* **A PDF is never truncated.** HTML degrades gracefully when cut; a PDF does
  not. The cross-reference table that tells a parser where every object lives
  sits at the *end* of the file, so a tail-truncated PDF usually fails to open
  at all. Oversize PDFs are refused with a clear reason instead.
* **Page-limited, not byte-limited.** A 200-page datasheet's full text will
  blow the turn's context budget long before it blows a byte cap, so the cap
  that matters is pages, reported honestly when it bites.
* **Encrypted/scanned PDFs are reported, not silently empty.** A scanned
  datasheet yields zero extractable characters; saying "0 characters, likely
  scanned — needs OCR" is very different from returning "".
"""

from __future__ import annotations

import io

import structlog

logger = structlog.get_logger(__name__)

MAX_PDF_BYTES = 25_000_000
"""Real datasheets run well past the 2 MB HTML cap (ST's STM32H7 reference
manual is ~12 MB). Generous, but bounded."""

DEFAULT_MAX_PAGES = 30


class PdfExtractionError(ValueError):
    """The PDF could not be turned into text, with a reportable reason."""


def pdf_to_text(
    raw: bytes, *, max_pages: int = DEFAULT_MAX_PAGES
) -> tuple[str, str, dict[str, int]]:
    """``(title, text, stats)`` from PDF bytes.

    ``stats`` carries ``pages_total`` / ``pages_read`` so the caller can tell
    the model it read 30 of 212 pages rather than implying it read the lot.

    Raises :class:`PdfExtractionError` for an encrypted, corrupt, or
    unparseable file — never returns an empty string as if it had succeeded.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise PdfExtractionError(
            "PDF support requires the 'pypdf' package, which is not installed in this environment"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 - any parse failure is reportable
        raise PdfExtractionError(f"could not parse the PDF: {exc}") from exc

    if reader.is_encrypted:
        # An empty-string decrypt succeeds on many "protected" datasheets.
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise PdfExtractionError(f"the PDF is encrypted: {exc}") from exc

    try:
        pages_total = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise PdfExtractionError(f"could not read the PDF page list: {exc}") from exc

    if pages_total == 0:
        raise PdfExtractionError("the PDF contains no pages")

    limit = max(1, min(max_pages, pages_total))
    chunks: list[str] = []
    for index in range(limit):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page must not sink the doc
            logger.warning("pdf_page_extract_failed", page=index, error=str(exc))

    text = "\n".join(c for c in chunks if c.strip())
    text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())

    if not text:
        raise PdfExtractionError(
            f"no extractable text in the first {limit} page(s) — the PDF is "
            "most likely a scan/image and would need OCR"
        )

    title = ""
    try:
        meta = reader.metadata
        if meta and meta.title:
            title = " ".join(str(meta.title).split())
    except Exception as exc:  # noqa: BLE001 - metadata is optional
        logger.debug("pdf_metadata_unavailable", error=str(exc))

    stats = {"pages_total": pages_total, "pages_read": limit}
    logger.info("pdf_extracted", pages_total=pages_total, pages_read=limit, chars=len(text))
    return title, text, stats
