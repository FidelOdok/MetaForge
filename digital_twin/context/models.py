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
# Token budget heuristic
# ---------------------------------------------------------------------------


_CHARS_PER_TOKEN = 4
"""Coarse char→token approximation.

OpenAI's official guidance for English text is roughly 4 chars / token;
this is the same heuristic used by `len(s) // 4` shortcuts across the
ecosystem. MET-317 will replace this with tiktoken — until then, every
caller gets a single deterministic answer.
"""


def estimate_tokens(text: str) -> int:
    """Return a deterministic token-count estimate for ``text``."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


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
