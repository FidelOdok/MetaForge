"""Pydantic v2 models for the context assembly protocol (MET-315).

Two outward-facing types:

* ``ContextAssemblyRequest`` — what an agent (or its harness) asks for.
* ``ContextAssemblyResponse`` — the assembled, attributed answer.

Three internal enums (``ContextScope``, ``ContextSourceKind``) are stable
strings so callers can construct them from JSON without importing this
module first.

The token-budget contract is intentionally simple here. Sophisticated
priority scoring + tiktoken-based counting land in MET-317; this PR
gives every consumer a working ``token_count`` field today using a
deterministic char-based heuristic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from digital_twin.knowledge.types import KnowledgeType

__all__ = [
    "ContextAssemblyRequest",
    "ContextAssemblyResponse",
    "ContextFragment",
    "ContextScope",
    "ContextSourceKind",
    "estimate_tokens",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContextScope(StrEnum):
    """Selector for which classes of context the assembler should draw from.

    Multiple scopes can be requested by passing a list — the response
    union-merges them in attribution order.
    """

    KNOWLEDGE = "knowledge"  # Semantic search over the L1 knowledge layer
    GRAPH = "graph"  # Structural neighbourhood from the Twin graph
    WORK_PRODUCT = "work_product"  # Specific work_product node + content
    ALL = "all"  # Union of every scope above


class ContextSourceKind(StrEnum):
    """Concrete origin of a context fragment.

    Used in attribution so consumers can render badges / colour-code
    citations by source, and so MET-322 (conflict detection) can group
    by origin.
    """

    KNOWLEDGE_HIT = "knowledge_hit"  # ``KnowledgeService.search`` result
    GRAPH_NODE = "graph_node"  # Twin work-product / node
    GRAPH_EDGE = "graph_edge"  # Twin relationship
    USER_INPUT = "user_input"  # PRD / constraints document


# ---------------------------------------------------------------------------
# Token counting (MET-317)
# ---------------------------------------------------------------------------


_DEFAULT_TIKTOKEN_MODEL = "gpt-4o-mini"
"""Default model for ``tiktoken.encoding_for_model``.

Matches LightRAG's bundled tokenizer so the count we charge against
the budget agrees with the count the L1 layer ingests with.
"""

_FALLBACK_TIKTOKEN_ENCODING = "cl100k_base"
"""Fallback encoding when the requested model is unknown to tiktoken."""

_CHARS_PER_TOKEN = 4
"""Coarse char→token approximation, used only when tiktoken is missing.

MET-317 replaces the project-wide heuristic with real BPE counts via
tiktoken; this constant is kept as a graceful fallback so unit tests
still pass in environments that don't install the LightRAG extras.
"""


_ENCODER_CACHE: dict[str, object] = {}
"""Module-level cache so we pay the encoder-load cost once per process."""


def _get_encoder(model: str) -> object | None:
    """Return a cached tiktoken encoder, or ``None`` if unavailable.

    Tries ``encoding_for_model(model)`` first, falls back to
    ``get_encoding(_FALLBACK_TIKTOKEN_ENCODING)`` for models tiktoken
    doesn't recognise (anthropic / local models commonly hit this
    path), and finally returns ``None`` if tiktoken itself isn't
    installed.
    """
    if model in _ENCODER_CACHE:
        encoder = _ENCODER_CACHE[model]
        return None if encoder is False else encoder
    try:
        import tiktoken  # type: ignore[import-untyped]
    except ImportError:
        _ENCODER_CACHE[model] = False
        return None
    try:
        encoder = tiktoken.encoding_for_model(model)
    except KeyError:
        encoder = tiktoken.get_encoding(_FALLBACK_TIKTOKEN_ENCODING)
    _ENCODER_CACHE[model] = encoder
    return encoder


def estimate_tokens(text: str, model: str = _DEFAULT_TIKTOKEN_MODEL) -> int:
    """Return the BPE token count for ``text`` (tiktoken-backed).

    Falls back to ``len(text) // 4`` when tiktoken can't be imported.
    Returns ``0`` for empty input; otherwise ``max(1, ...)`` so a
    minimum of 1 token is charged for any non-empty fragment (matches
    the budget enforcement contract — we never let a fragment claim a
    zero cost).
    """
    if not text:
        return 0
    encoder = _get_encoder(model)
    if encoder is not None:
        try:
            return max(1, len(encoder.encode(text)))  # type: ignore[attr-defined]
        except Exception:
            pass
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Priority scoring (MET-317)
# ---------------------------------------------------------------------------


# Source-kind authority weights. Higher = more trustworthy / less likely
# to be dropped under budget pressure. Tuned so a strong knowledge hit
# (similarity 0.9) and a default graph node (no similarity, authority
# 0.85) compete fairly.
_SOURCE_AUTHORITY: dict[str, float] = {
    "user_input": 1.00,
    "graph_node": 0.85,
    "knowledge_hit": 0.70,
    "graph_edge": 0.50,
}

# 30-day exponential half-life — a fragment from a month ago carries
# half the recency weight of a fresh one. Tuned to "older work-product
# data deprioritises but does not vanish".
_RECENCY_HALF_LIFE_SECONDS = 30 * 24 * 3600


def _decay_recency(age_seconds: float) -> float:
    """Exponential decay in [0, 1] — 1.0 at age 0, 0.5 at half-life."""
    if age_seconds <= 0:
        return 1.0
    import math

    return math.exp(-math.log(2) * age_seconds / _RECENCY_HALF_LIFE_SECONDS)


def _fragment_recency(fragment_metadata: dict[str, Any], now_ts: float | None = None) -> float:
    """Recency factor for a fragment, given its metadata snapshot.

    Reads ``metadata["created_at"]`` (ISO-8601 string or epoch float).
    Returns ``1.0`` when no timestamp can be parsed — this is the
    correct default because no-data shouldn't penalise the fragment.
    """
    raw = fragment_metadata.get("created_at")
    if raw is None:
        return 1.0
    import time
    from datetime import datetime

    if now_ts is None:
        now_ts = time.time()

    if isinstance(raw, int | float):
        ts = float(raw)
    else:
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 1.0

    return _decay_recency(max(0.0, now_ts - ts))


def fragment_priority(
    source_kind: str,
    similarity: float | None,
    metadata: dict[str, Any],
    now_ts: float | None = None,
) -> float:
    """Composite priority = ``recency × authority × relevance``.

    * `recency` exponential-decays via 30-day half-life on
      ``metadata["created_at"]`` (defaults to 1.0 when absent).
    * `authority` is the per-source-kind weight in
      ``_SOURCE_AUTHORITY`` (defaults to 0.5 for unknown kinds).
    * `relevance` is ``similarity`` when set, else 0.5 (graph
      fragments arrive without a cosine score).
    """
    recency = _fragment_recency(metadata, now_ts=now_ts)
    authority = _SOURCE_AUTHORITY.get(source_kind, 0.5)
    relevance = similarity if similarity is not None else 0.5
    return recency * authority * relevance


# ---------------------------------------------------------------------------
# Outward-facing models
# ---------------------------------------------------------------------------


class ContextFragment(BaseModel):
    """A single attributed piece of context."""

    content: str = Field(..., description="Text content of the fragment")
    source_kind: ContextSourceKind = Field(..., description="Origin of this fragment")
    source_id: str = Field(
        ...,
        description=(
            "Stable identifier of the originating source — knowledge "
            "``source_path``, ``work_product://<uuid>``, or twin node id."
        ),
    )
    source_path: str | None = Field(
        default=None,
        description="File path / URI when the source has one (always set for KNOWLEDGE_HIT)",
    )
    heading: str | None = Field(
        default=None,
        description="Section heading the fragment came from (KNOWLEDGE_HIT only)",
    )
    chunk_index: int | None = Field(default=None)
    total_chunks: int | None = Field(default=None)
    similarity_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Cosine similarity for KNOWLEDGE_HIT fragments",
    )
    knowledge_type: KnowledgeType | None = Field(default=None)
    work_product_id: UUID | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_count: int = Field(..., ge=0, description="Estimated token cost (see estimate_tokens)")


class ContextAssemblyRequest(BaseModel):
    """An agent's request for context."""

    agent_id: str = Field(..., min_length=1, description="Agent / role identifier")
    query: str | None = Field(
        default=None,
        description=(
            "Free-text question or task description. Required when "
            "``ContextScope.KNOWLEDGE`` or ``ContextScope.ALL`` is in scope."
        ),
    )
    scope: list[ContextScope] = Field(
        default_factory=lambda: [ContextScope.ALL],
        description="Which sources to draw from",
    )
    work_product_id: UUID | None = Field(
        default=None,
        description=(
            "Optional pivot: when set, ``GRAPH`` and ``WORK_PRODUCT`` scopes "
            "centre their queries on this node."
        ),
    )
    knowledge_type: KnowledgeType | None = Field(
        default=None,
        description="Optional filter passed through to the KnowledgeService",
    )
    knowledge_top_k: int = Field(
        default=5, ge=1, le=50, description="Top-k for the knowledge search"
    )
    graph_depth: int = Field(
        default=1, ge=0, le=5, description="Subgraph traversal depth around work_product_id"
    )
    token_budget: int = Field(default=8000, ge=1, description="Hard cap on total fragment tokens")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata filters forwarded to the knowledge service",
    )

    @property
    def includes_knowledge(self) -> bool:
        return any(s in (ContextScope.KNOWLEDGE, ContextScope.ALL) for s in self.scope)

    @property
    def includes_graph(self) -> bool:
        return any(s in (ContextScope.GRAPH, ContextScope.ALL) for s in self.scope)

    @property
    def includes_work_product(self) -> bool:
        return any(s in (ContextScope.WORK_PRODUCT, ContextScope.ALL) for s in self.scope)


class ContextAssemblyResponse(BaseModel):
    """The assembled, attributed context."""

    fragments: list[ContextFragment] = Field(
        default_factory=list,
        description="Fragments in priority order — first is most relevant",
    )
    token_count: int = Field(..., ge=0, description="Sum of fragment token estimates")
    truncated: bool = Field(
        default=False,
        description="True when the budget caused at least one fragment to be dropped",
    )
    dropped_source_ids: list[str] = Field(
        default_factory=list,
        description="source_id list for fragments removed by the budget",
    )
    sources: dict[str, int] = Field(
        default_factory=dict,
        description="source_kind → fragment count, for quick attribution stats",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
