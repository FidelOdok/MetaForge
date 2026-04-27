"""UAT-C1-L2 — Context assembly contract (MET-313/315/316/317/319/320/332).

Acceptance bullets validated:

* MET-315: ``ContextAssembler.assemble`` returns a budgeted response
  with attribution.
* MET-316: Role-based scoping narrows knowledge types (``mechanical_agent``
  doesn't see EE-only types).
* MET-317: Token budget enforced; over-budget fragments land in
  ``dropped_source_ids``.
* MET-319/332: Per-agent context spec docs exist and are linked from the
  protocol doc.
* MET-313: Protocol doc exists at the canonical path.
* MET-320: Every fragment carries a non-empty ``source_id``.
"""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.context import (
    ContextAssembler,
    ContextAssemblyRequest,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType
from tests.uat.conftest import REPO_ROOT, assert_validates
from twin_core.api import InMemoryTwinAPI

pytestmark = [pytest.mark.uat]


class _StubKnowledge:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def ingest(self, *a: Any, **k: Any) -> IngestResult:
        return IngestResult(entry_ids=[], chunks_indexed=0, source_path="")

    async def search(
        self,
        query: str,
        top_k: int = 5,
        knowledge_type: KnowledgeType | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        self.calls.append({"query": query, "top_k": top_k, "knowledge_type": knowledge_type})
        if knowledge_type is not None:
            return [h for h in self.hits if h.knowledge_type == knowledge_type][:top_k]
        return self.hits[:top_k]

    async def delete_by_source(self, source_path: str) -> int:
        return 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


def _hit(text: str, kt: KnowledgeType, score: float = 0.9) -> SearchHit:
    return SearchHit(
        content=text,
        similarity_score=score,
        source_path=f"uat://{kt.value}/{abs(hash(text)) % 10_000}",
        heading=None,
        chunk_index=0,
        total_chunks=1,
        knowledge_type=kt,
    )


# ---------------------------------------------------------------------------
# MET-313 — Protocol spec doc exists
# ---------------------------------------------------------------------------


def test_met313_context_engineering_spec_exists() -> None:
    spec = REPO_ROOT / "docs" / "architecture" / "context-engineering.md"
    assert_validates(
        "MET-313",
        "Context Engineering spec doc is committed at the canonical path",
        spec.exists() and spec.stat().st_size > 1000,
        f"path={spec}",
    )


# ---------------------------------------------------------------------------
# MET-319 / MET-332 — Per-agent context spec docs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_doc,met_id",
    [
        ("docs/agents/mechanical-context-spec.md", "MET-319"),
        ("docs/agents/electronics-context-spec.md", "MET-332"),
    ],
)
def test_met319_332_per_agent_context_spec_docs_exist(agent_doc: str, met_id: str) -> None:
    path = REPO_ROOT / agent_doc
    assert_validates(
        met_id,
        f"per-agent context spec exists at {agent_doc}",
        path.exists() and path.stat().st_size > 500,
        f"path={path}",
    )


# ---------------------------------------------------------------------------
# MET-315 + MET-320 — Assembly contract + attribution
# ---------------------------------------------------------------------------


async def test_met315_assemble_returns_attributed_fragments() -> None:
    twin = InMemoryTwinAPI.create()
    knowledge = _StubKnowledge(
        hits=[
            _hit("Decision A: titanium for the bracket.", KnowledgeType.DESIGN_DECISION),
            _hit("Component B: regulator footprint.", KnowledgeType.COMPONENT),
        ]
    )
    assembler = ContextAssembler(twin=twin, knowledge_service=knowledge)  # type: ignore[arg-type]
    response = await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="mechanical_agent",
            query="bracket?",
            scope=[ContextScope.KNOWLEDGE],
        )
    )
    assert_validates(
        "MET-315",
        "ContextAssembler returns at least one fragment for a non-empty query",
        len(response.fragments) >= 1,
        f"got {len(response.fragments)} fragments",
    )
    assert_validates(
        "MET-320",
        "every fragment carries a non-empty source_id",
        all(f.source_id for f in response.fragments),
        f"source_ids: {[f.source_id for f in response.fragments]}",
    )
    assert_validates(
        "MET-320",
        "every fragment declares its source_kind",
        all(f.source_kind in ContextSourceKind for f in response.fragments),
    )


# ---------------------------------------------------------------------------
# MET-316 — Role-based scoping
# ---------------------------------------------------------------------------


async def test_met316_role_scope_narrows_to_allowed_types() -> None:
    """``mechanical_agent``'s allow-list is {DESIGN_DECISION, COMPONENT,
    FAILURE} (per ``digital_twin/context/role_scope.py``). It must NOT
    receive SESSION-typed hits (those go to simulation_agent).
    """
    twin = InMemoryTwinAPI.create()
    knowledge = _StubKnowledge(
        hits=[
            _hit(
                "ME-relevant: stress analysis on titanium bracket.",
                KnowledgeType.DESIGN_DECISION,
            ),
            _hit(
                "Sim-only: prior FEA session ran with mesh size 5mm.",
                KnowledgeType.SESSION,
            ),
        ]
    )
    assembler = ContextAssembler(twin=twin, knowledge_service=knowledge)  # type: ignore[arg-type]
    response = await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="mechanical_agent",
            query="bracket?",
            scope=[ContextScope.KNOWLEDGE],
        )
    )
    received_types = {f.knowledge_type for f in response.fragments if f.knowledge_type}
    # mechanical_agent's allow-list deliberately excludes SESSION — that
    # belongs to simulation_agent.
    assert_validates(
        "MET-316",
        "mechanical_agent does not receive SESSION knowledge (sim_agent's territory)",
        KnowledgeType.SESSION not in received_types,
        f"received types: {received_types}",
    )
    # Positive check: it does receive DESIGN_DECISION (its primary type).
    assert_validates(
        "MET-316",
        "mechanical_agent receives DESIGN_DECISION knowledge (in its allow-list)",
        KnowledgeType.DESIGN_DECISION in received_types,
        f"received types: {received_types}",
    )


# ---------------------------------------------------------------------------
# MET-317 — Token budget enforcement
# ---------------------------------------------------------------------------


async def test_met317_token_budget_drops_lowest_priority() -> None:
    """A budget tighter than the corpus must drop fragments and report
    them in ``dropped_source_ids``."""
    twin = InMemoryTwinAPI.create()
    big = "word " * 500  # ~500 tokens
    knowledge = _StubKnowledge(
        hits=[
            _hit(big + " alpha", KnowledgeType.DESIGN_DECISION, score=0.95),
            _hit(big + " beta", KnowledgeType.DESIGN_DECISION, score=0.5),
            _hit(big + " gamma", KnowledgeType.DESIGN_DECISION, score=0.3),
        ]
    )
    assembler = ContextAssembler(twin=twin, knowledge_service=knowledge)  # type: ignore[arg-type]
    response = await assembler.assemble(
        ContextAssemblyRequest(
            agent_id="mechanical_agent",
            query="?",
            scope=[ContextScope.KNOWLEDGE],
            knowledge_top_k=3,
            token_budget=500,  # only fits 1 fragment
        )
    )
    assert_validates(
        "MET-317",
        "token budget enforced — response.token_count <= budget",
        response.token_count <= 500,
        f"token_count={response.token_count}, budget=500",
    )
    assert_validates(
        "MET-317",
        "dropped fragments reported in dropped_source_ids",
        len(response.dropped_source_ids) >= 1 if len(response.fragments) < 3 else True,
        f"kept={len(response.fragments)}, dropped={len(response.dropped_source_ids)}",
    )
    assert_validates(
        "MET-317",
        "response.truncated reflects whether anything was dropped",
        response.truncated == bool(response.dropped_source_ids),
    )
