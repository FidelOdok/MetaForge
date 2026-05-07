"""Document chunking utilities for the knowledge pipeline.

Splits long text into overlapping chunks suitable for embedding.
Default configuration uses 512-token chunks with 64-token overlap,
approximated by whitespace-delimited word counts.

Also exposes :func:`chunk_csv` (MET-340) — a row-level chunker for
CSV BOMs so each row becomes its own searchable fragment.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.chunker")


@dataclass
class CsvRowChunk:
    """One CSV data row, formatted for embedding + retrieval (MET-340).

    Attributes
    ----------
    content:
        The row rendered as ``col=val; col=val; ...`` so the embedding
        model sees both the column names and the values. Engineers
        searching for a part number get a hit on the whole row.
    row_index:
        Zero-based index of the data row (the header row does not
        count). ``row_index == 0`` is the first row beneath the header.
    columns:
        The row as a ``{column_name: value}`` mapping. Round-trips the
        row's structure into search results without re-parsing the
        source CSV.
    header:
        The CSV column names in source order. Bundled into every
        chunk's metadata so retrieval consumers can reconstruct the
        row layout for context display.
    """

    content: str
    row_index: int
    columns: dict[str, str]
    header: list[str] = field(default_factory=list)


def chunk_csv(content: str, *, header_in_metadata: bool = True) -> list[CsvRowChunk]:
    """Split a CSV document into one chunk per data row.

    The first row is treated as the header. Each subsequent row becomes
    a single :class:`CsvRowChunk` whose ``content`` is the row formatted
    as ``key=value`` pairs joined with ``"; "`` — for example
    ``"mpn=STM32H723VGT6; package=LQFP100; price=8.50"``. The full
    column header is preserved on every chunk so retrieval consumers
    can reconstruct the row's shape.

    Empty rows (all values blank or whitespace-only) are skipped — the
    spec is "one chunk per real BOM row," and a stray trailing newline
    would otherwise produce a garbage chunk that polluted search.

    Parameters
    ----------
    content:
        The CSV text. Encoding handling is the caller's responsibility;
        ``content`` is consumed as-is.
    header_in_metadata:
        Reserved switch (default True). The header is always included
        in :attr:`CsvRowChunk.header`; the flag exists so future
        consumers can opt out without breaking the call signature.
    """
    with tracer.start_as_current_span("chunker.chunk_csv") as span:
        span.set_attribute("chunker.input_length", len(content))
        if not content or not content.strip():
            span.set_attribute("chunker.chunk_count", 0)
            return []

        reader = csv.DictReader(io.StringIO(content))
        header = list(reader.fieldnames or [])
        if not header:
            span.set_attribute("chunker.chunk_count", 0)
            return []

        chunks: list[CsvRowChunk] = []
        data_index = 0
        for raw_row in reader:
            # csv.DictReader pads missing trailing columns with None; we
            # canonicalise to "" so downstream consumers get a stable
            # ``dict[str, str]`` shape.
            row = {col: ("" if raw_row.get(col) is None else str(raw_row[col])) for col in header}
            # Skip rows that are entirely empty/whitespace — DictReader
            # will happily emit an entry for a trailing blank line.
            if not any(value.strip() for value in row.values()):
                continue
            content_str = "; ".join(f"{col}={row[col]}" for col in header)
            chunks.append(
                CsvRowChunk(
                    content=content_str,
                    row_index=data_index,
                    columns=row,
                    header=list(header) if header_in_metadata else [],
                )
            )
            data_index += 1

        span.set_attribute("chunker.chunk_count", len(chunks))
        logger.debug(
            "csv_chunked",
            row_count=len(chunks),
            column_count=len(header),
        )
        return chunks


# Type alias kept light so callers don't need to import the dataclass
# just to type a list. The real shape lives on :class:`CsvRowChunk`.
_CsvChunkList = list[CsvRowChunk]
__all__ = ["TextChunker", "CsvRowChunk", "chunk_csv"]


class TextChunker:  # pragma: deprecated
    """Split text into overlapping chunks for embedding.

    .. deprecated:: MET-346
        Chunking is now LightRAG's responsibility (heading-aware
        markdown splitter inside ``LightRAGKnowledgeService``). Removal
        is gated on MET-307. Do not call from new code.


    Parameters
    ----------
    chunk_size:
        Target number of tokens (approximated by whitespace-delimited words)
        per chunk.  Defaults to 512.
    overlap:
        Number of overlapping tokens between consecutive chunks.
        Defaults to 64.
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 64) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if overlap < 0:
            raise ValueError(f"overlap must be non-negative, got {overlap}")
        if overlap >= chunk_size:
            raise ValueError(f"overlap ({overlap}) must be less than chunk_size ({chunk_size})")
        self._chunk_size = chunk_size
        self._overlap = overlap

    @property
    def chunk_size(self) -> int:
        """Target tokens per chunk."""
        return self._chunk_size

    @property
    def overlap(self) -> int:
        """Overlap tokens between consecutive chunks."""
        return self._overlap

    def chunk_text(self, text: str) -> list[str]:
        """Split *text* into overlapping chunks.

        Tokenisation is approximated by splitting on whitespace.  Each
        chunk contains up to ``chunk_size`` words, with ``overlap`` words
        shared between consecutive chunks.

        Returns an empty list for empty or whitespace-only input.
        """
        with tracer.start_as_current_span("chunker.chunk_text") as span:
            span.set_attribute("chunker.input_length", len(text))
            words = text.split()
            if not words:
                span.set_attribute("chunker.chunk_count", 0)
                return []

            chunks: list[str] = []
            step = self._chunk_size - self._overlap
            idx = 0
            while idx < len(words):
                chunk_words = words[idx : idx + self._chunk_size]
                chunks.append(" ".join(chunk_words))
                idx += step

            span.set_attribute("chunker.chunk_count", len(chunks))
            logger.debug(
                "text_chunked",
                input_words=len(words),
                chunk_count=len(chunks),
                chunk_size=self._chunk_size,
                overlap=self._overlap,
            )
            return chunks

    def chunk_document(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Split *text* and attach metadata to each chunk.

        Returns a list of dicts, each containing:
        - ``content``: The chunk text
        - ``chunk_index``: Zero-based chunk position
        - ``total_chunks``: Total number of chunks
        - All keys from *metadata* (if provided)
        """
        with tracer.start_as_current_span("chunker.chunk_document") as span:
            raw_chunks = self.chunk_text(text)
            total = len(raw_chunks)
            span.set_attribute("chunker.total_chunks", total)

            result: list[dict[str, Any]] = []
            base_meta = metadata if metadata is not None else {}
            for i, chunk in enumerate(raw_chunks):
                entry: dict[str, Any] = {
                    **base_meta,
                    "content": chunk,
                    "chunk_index": i,
                    "total_chunks": total,
                }
                result.append(entry)

            logger.debug(
                "document_chunked",
                total_chunks=total,
                has_metadata=bool(base_meta),
            )
            return result
