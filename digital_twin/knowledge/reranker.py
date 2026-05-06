"""Cross-encoder reranker for the L1 knowledge layer (MET-335).

Wraps ``sentence_transformers.CrossEncoder`` with the
``BAAI/bge-reranker-base`` model to re-order vector-search hits by a
query-aware relevance score. The cross-encoder sees both the query and
the candidate chunk text together (unlike the vector retrieval step,
which embeds them independently), which typically promotes chunks that
are *topically* on point even when their raw cosine score is lower than
a noisier near-duplicate.

Design notes:

* **Lazy model load.** ``BAAI/bge-reranker-base`` is ~440 MB and pulls
  ``torch`` + ``transformers`` into the import graph. We defer the
  ``CrossEncoder(...)`` constructor until the first ``rerank()`` call so
  the unit-test path (which mocks the class) and any deployment that
  leaves ``KNOWLEDGE_RERANKER_ENABLED=false`` never pays the cost.
* **Hits are the existing ``SearchHit`` dataclass.** We do not introduce
  a parallel taxonomy: the public Protocol already returns
  ``list[SearchHit]`` and the reranker re-orders that list in place.
* **No persistent state.** The ranker is a pure function of (query,
  hits). Cross-encoder scores are not stored on the hit so the public
  contract stays unchanged — only the *order* of the returned list
  reflects the reranker.
"""

from __future__ import annotations

from typing import Any

import structlog

from digital_twin.knowledge.service import SearchHit
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.reranker")


_DEFAULT_MODEL_NAME = "BAAI/bge-reranker-base"


class Reranker:
    """Cross-encoder reranker over ``SearchHit`` lists.

    The model name is configurable but defaults to the BGE base reranker
    used in the spec. ``rerank()`` is async-friendly even though the
    underlying ``CrossEncoder.predict`` is synchronous — we still expose
    an ``async def`` so callers (LightRAG service) can wrap it with the
    rest of their async pipeline without an awkward sync hop.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Any = None  # populated lazily on first rerank()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_loaded(self) -> bool:
        """True iff the underlying ``CrossEncoder`` has been instantiated."""
        return self._model is not None

    def _load(self) -> Any:
        """Instantiate the cross-encoder on first use.

        Resolves ``CrossEncoder`` via ``_resolve_cross_encoder`` so unit
        tests can patch ``digital_twin.knowledge.reranker.CrossEncoder``
        without ``sentence_transformers`` being installed. The real
        import only happens when the patched symbol is ``None`` (the
        module-level default), which is the case in production but not
        in tests.
        """
        if self._model is None:
            logger.info("knowledge_reranker_loading", model=self._model_name)
            cls = _resolve_cross_encoder()
            self._model = cls(self._model_name)
        return self._model

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Re-order ``hits`` by cross-encoder relevance score, descending.

        An empty / single-element ``hits`` list is returned unchanged
        without loading the model. When a chunk's rank changes we emit
        a ``knowledge_reranker_promoted`` log line with the old and new
        index so observability can quantify the reranker's effect.
        """
        if not hits or len(hits) == 1:
            return list(hits)

        with tracer.start_as_current_span("knowledge.rerank") as span:
            span.set_attribute("knowledge.rerank.query_length", len(query))
            span.set_attribute("knowledge.rerank.candidate_count", len(hits))

            model = self._load()
            pairs = [[query, h.content] for h in hits]
            raw_scores = model.predict(pairs)
            scores: list[float] = [float(s) for s in raw_scores]

            indexed: list[tuple[int, float, SearchHit]] = list(
                zip(range(len(hits)), scores, hits, strict=True)
            )
            indexed.sort(key=lambda item: item[1], reverse=True)

            for new_index, (old_index, score, hit) in enumerate(indexed):
                if new_index != old_index:
                    logger.info(
                        "knowledge_reranker_promoted",
                        old_index=old_index,
                        new_index=new_index,
                        score=score,
                        source_path=hit.source_path,
                    )

            span.set_attribute("knowledge.rerank.result_count", len(indexed))
            return [hit for _old_index, _score, hit in indexed]


# ---------------------------------------------------------------------------
# Lazy ``sentence_transformers`` shim
# ---------------------------------------------------------------------------

# Importing ``sentence_transformers`` is expensive (pulls torch). We hold
# ``CrossEncoder`` as a module-level attribute that defaults to ``None``;
# unit tests can ``@patch("digital_twin.knowledge.reranker.CrossEncoder",
# <stub>)`` without triggering the real import. ``_resolve_cross_encoder``
# only imports ``sentence_transformers`` when no patch is in place.

CrossEncoder: Any = None


def _resolve_cross_encoder() -> Any:
    """Return the ``CrossEncoder`` class — patched stub or real import.

    If a test (or other caller) has assigned a non-``None`` value to the
    module-level ``CrossEncoder`` attribute, return it directly. Otherwise
    import the real class from ``sentence_transformers``.
    """
    if CrossEncoder is not None:
        return CrossEncoder
    from sentence_transformers import CrossEncoder as _CrossEncoder

    return _CrossEncoder
