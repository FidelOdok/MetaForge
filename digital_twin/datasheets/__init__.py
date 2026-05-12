"""Datasheet ingestion helpers (MET-430).

Lives in ``digital_twin`` rather than ``twin_core`` so the heavy PDF
dependency (``pdfplumber``) doesn't leak into the core model layer.
``twin_core.models.datasheet`` defines the schema; this package
parses PDFs into that schema.
"""

from digital_twin.datasheets.parser import (
    PdfDependencyError,
    compute_file_hash,
    extract_pages,
    parse_datasheet_pdf,
)

__all__ = [
    "PdfDependencyError",
    "compute_file_hash",
    "extract_pages",
    "parse_datasheet_pdf",
]
