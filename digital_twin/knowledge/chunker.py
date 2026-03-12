"""Document chunking utilities for the knowledge pipeline.

Splits long text into overlapping chunks suitable for embedding.
Default configuration uses 512-token chunks with 64-token overlap,
approximated by whitespace-delimited word counts.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.chunker")


class TextChunker:
    """Split text into overlapping chunks for embedding.

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
