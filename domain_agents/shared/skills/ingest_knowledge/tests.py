"""Skill-specific tests for ingest_knowledge (FORGE-552).

They run against the real in-memory store and the knowledge.ingest names
(``source_path``, the store's ``knowledge_type`` enum).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.knowledge.store import InMemoryKnowledgeStore, KnowledgeType
from skill_registry.skill_base import SkillContext

from .handler import IngestKnowledgeHandler
from .schema import IngestKnowledgeInput


class _FixedEmbedding(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


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


class TestIngestKnowledgeSkill:
    async def test_stores_source_path_and_chunks(self, context: SkillContext) -> None:
        store = InMemoryKnowledgeStore()
        handler = IngestKnowledgeHandler(context, store, _FixedEmbedding())
        out = await handler.execute(
            IngestKnowledgeInput(
                content="x" * 1200,
                knowledge_type="constraint",
                source_path="docs/design_rules/clearance.md",
            )
        )
        assert out.embedded is True
        assert out.chunk_count == 3
        entries = list(store._entries.values())
        assert {e.source_path for e in entries} == {"docs/design_rules/clearance.md"}
        assert sorted(e.chunk_index for e in entries) == [0, 1, 2]
        assert {e.knowledge_type for e in entries} == {KnowledgeType.CONSTRAINT}

    def test_legacy_source_name_is_still_accepted(self) -> None:
        inp = IngestKnowledgeInput(content="c", knowledge_type="component", source="ds.pdf")
        assert inp.source_path == "ds.pdf"

    @pytest.mark.parametrize("bad", ["design_rule", "material_property", "general"])
    def test_types_outside_the_tool_enum_are_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            IngestKnowledgeInput(content="c", knowledge_type=bad, source_path="s")

    async def test_without_embeddings_says_not_embedded(self, context: SkillContext) -> None:
        handler = IngestKnowledgeHandler(context, InMemoryKnowledgeStore())
        out = await handler.execute(
            IngestKnowledgeInput(content="c", knowledge_type="failure", source_path="s")
        )
        assert out.embedded is False
