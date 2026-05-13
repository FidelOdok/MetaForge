"""PDF table extraction → structured rows (MET-444).

Datasheet sections like ``Electrical Characteristics`` and ``Absolute
Maximum Ratings`` are where the typed values agents need. ``pdfplumber``
already detects table-shaped layout regions; this module wraps that
into a ``Table`` dataclass the downstream consumers (MET-445
``knowledge.extract``) can read directly.

Camelot is a stronger backend but adds a Ghostscript dependency and is
markedly slower. Stick with pdfplumber's default extractor for now —
it's already pulled in via the ``[knowledge]`` extra and works on the
ESP32 / STM32 datasheets we care about. Swap in camelot as a fallback
if pdfplumber returns zero tables on a known-good fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from digital_twin.datasheets.parser import PdfDependencyError


@dataclass
class Table:
    """One extracted table from a single PDF page.

    Cells are kept as strings — typing each cell is the property
    extractor's job (MET-445), not the table extractor's. ``columns``
    is the first row when it looks header-shaped (non-empty, mostly
    short strings); ``None`` otherwise.
    """

    page: int
    rows: list[list[str]]
    columns: list[str] | None = None
    heading: str | None = None
    # Free-form metadata so future passes can stash extractor name,
    # confidence, bbox, etc. without a schema change.
    metadata: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.rows or all(not row for row in self.rows)


def extract_tables(pdf_bytes: bytes) -> list[Table]:
    """Extract every table-shaped region from a PDF as ``Table`` rows.

    Empty pages — and pages where pdfplumber finds nothing — produce
    no entries (not error). Each emitted ``Table`` has at least one
    row; degenerate ``[None, None, …]`` cells are dropped to keep the
    typed-extraction surface clean.
    """
    try:
        import io

        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — exercised in the no-dep test
        raise PdfDependencyError(
            "pdfplumber is required to extract tables. "
            "Install with `pip install -e .[knowledge]` or `[dev]`."
        ) from exc

    tables: list[Table] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            raw_tables = page.extract_tables() or []
            for raw in raw_tables:
                rows = _clean_rows(raw)
                if not rows:
                    continue
                tables.append(
                    Table(
                        page=page_index,
                        rows=rows,
                        columns=_infer_columns(rows),
                    )
                )
    return tables


def _clean_rows(raw: list[list[str | None]]) -> list[list[str]]:
    """Strip surrounding whitespace, drop fully-empty rows, normalise None → ""."""
    cleaned: list[list[str]] = []
    for row in raw:
        normalised = [(cell or "").strip() for cell in row]
        if any(normalised):
            cleaned.append(normalised)
    return cleaned


def _infer_columns(rows: list[list[str]]) -> list[str] | None:
    """Treat the first row as a header when it looks header-shaped.

    Heuristic: all cells non-empty, average cell length ≤ 30 chars,
    no row-only-numeric cells. Conservative — when the first row
    looks ambiguous we return ``None`` rather than fabricating headers.
    """
    if not rows:
        return None
    head = rows[0]
    if not head or any(not cell for cell in head):
        return None
    avg_len = sum(len(c) for c in head) / len(head)
    if avg_len > 30:
        return None
    # Numeric-only headers are suspicious — the first row is probably data.
    numeric_only = all(cell.replace(".", "", 1).replace("-", "", 1).isdigit() for cell in head)
    if numeric_only:
        return None
    return list(head)
