"""Skill-specific tests for retrieve_knowledge (FORGE-552)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.store import InMemoryKnowledgeStore, KnowledgeEntry, KnowledgeType
from skill_registry.skill_base import SkillContext

from .handler import RetrieveKnowledgeHandler
from .schema import RetrieveKnowledgeInput

_VECTORS = {"q": [1.0, 0.0], "near": [0.9, 0.1], "far": [0.0, 1.0]}


class _TableEmbedding(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        return _VECTORS[text]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [_VECTORS[t] for t in texts]


@pytest.fixture()
def context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = MagicMock()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


async def _store() -> InMemoryKnowledgeStore:
    store = InMemoryKnowledgeStore()
    for name, ktype in (("near", KnowledgeType.CONSTRAINT), ("far", KnowledgeType.COMPONENT)):
        await store.store(
            KnowledgeEntry(
                content=name,
                embedding=_VECTORS[name],
                knowledge_type=ktype,
                source_path=f"docs/{name}.md",
            )
        )
    return store


class TestRetrieveKnowledgeSkill:
    async def test_scores_are_real_similarities_and_cite_the_source(
        self, context: SkillContext
    ) -> None:
        handler = RetrieveKnowledgeHandler(context, await _store(), _TableEmbedding())
        out = await handler.execute(RetrieveKnowledgeInput(query="q", top_k=2))
        assert [r.content for r in out.results] == ["near", "far"]
        assert out.results[0].score == pytest.approx(0.9939, abs=1e-3)
        assert out.results[1].score == 0.0
        assert out.results[0].source_path == "docs/near.md"

    async def test_type_filter_uses_the_store_enum(self, context: SkillContext) -> None:
        handler = RetrieveKnowledgeHandler(context, await _store(), _TableEmbedding())
        out = await handler.execute(RetrieveKnowledgeInput(query="q", knowledge_type="component"))
        assert [r.content for r in out.results] == ["far"]

    def test_limit_alias_and_bad_type(self) -> None:
        assert RetrieveKnowledgeInput(query="q", limit=3).top_k == 3
        with pytest.raises(ValidationError):
            RetrieveKnowledgeInput(query="q", knowledge_type="design_rule")

    async def test_refuses_without_an_embedding_service(self, context: SkillContext) -> None:
        handler = RetrieveKnowledgeHandler(context, await _store())
        with pytest.raises(ValueError, match="embedding service"):
            await handler.execute(RetrieveKnowledgeInput(query="q"))
