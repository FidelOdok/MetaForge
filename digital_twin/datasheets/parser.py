"""PDF → Datasheet parser (MET-430).

Thin wrapper around pdfplumber that produces a populated
``twin_core.models.Datasheet`` model from raw PDF bytes. Three steps,
each independently testable:

1. :func:`compute_file_hash` — SHA-256 of the bytes (idempotency key).
2. :func:`extract_pages` — per-page plain text via pdfplumber.
3. :func:`parse_datasheet_pdf` — convenience wrapper that ties the
   two together and stamps the metadata fields the Twin layer expects.

The downstream chunker (``digital_twin.knowledge.lightrag_service``)
already knows how to convert page-segmented text into citation-bearing
chunks — that flow stays where it is. This module's job is to land a
typed ``Datasheet`` node so other tools can hang properties off it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from twin_core.models import Datasheet


class PdfDependencyError(RuntimeError):
    """Raised when pdfplumber is unavailable in the current environment.

    pdfplumber is shipped in the ``[dev]`` and ``[knowledge]`` extras of
    pyproject.toml. Importing this module never fails — pdfplumber is
    only required when ``extract_pages`` / ``parse_datasheet_pdf`` is
    actually called. Production deployments must install the
    ``[knowledge]`` extra.
    """


def compute_file_hash(pdf_bytes: bytes) -> str:
    """SHA-256 hex digest of the PDF bytes.

    The hash is the idempotency key for :meth:`TwinAPI.ingest_datasheet`
    — re-ingesting the same bytes yields the same node. Deterministic
    and dependency-free so callers can compute it without paying the
    pdfplumber cost.
    """
    return hashlib.sha256(pdf_bytes).hexdigest()


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """Per-page plain text from a PDF.

    Uses pdfplumber, which is fast enough for the page counts we see
    in real datasheets (hundreds of pages). Each list entry is the
    raw ``extract_text()`` output for the corresponding page (1-indexed
    in display, 0-indexed in the returned list).
    """
    try:
        import io

        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — exercised in the no-dep test
        raise PdfDependencyError(
            "pdfplumber is required to parse PDFs. "
            "Install with `pip install -e .[knowledge]` or `[dev]`."
        ) from exc

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def parse_datasheet_pdf(
    pdf_path: str | Path,
    *,
    mpn: str,
    manufacturer: str,
    revision: str,
    source_url: str | None = None,
) -> Datasheet:
    """Build a populated :class:`Datasheet` model from a PDF on disk.

    Reads the bytes, computes the file hash, counts pages via
    pdfplumber, and returns a ``Datasheet`` ready to be passed to
    :meth:`TwinAPI.ingest_datasheet`. The TwinAPI handles idempotency
    and ``SUPERSEDES`` linking — this function is purely the PDF →
    model transform.
    """
    path = Path(pdf_path)
    pdf_bytes = path.read_bytes()
    pages = extract_pages(pdf_bytes)

    return Datasheet(
        mpn=mpn,
        manufacturer=manufacturer,
        revision=revision,
        file_hash=compute_file_hash(pdf_bytes),
        source_path=str(path),
        source_url=source_url,
        page_count=len(pages),
    )
