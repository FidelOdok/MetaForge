"""Skill-specific tests for retrieve_knowledge.

These tests live alongside the skill for co-location.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext
from twin_core.knowledge.models import KnowledgeEntry, KnowledgeType, SearchResult
from twin_core.knowledge.store import KnowledgeStore

from .handler import RetrieveKnowledgeHandler
from .schema import RetrieveKnowledgeInput


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = MagicMock()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def mock_store() -> KnowledgeStore:
    return MagicMock(spec=KnowledgeStore)


class TestRetrieveKnowledgeSkill:
    """Co-located tests for the retrieve_knowledge handler."""

    async def test_execute_returns_results(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entry = KnowledgeEntry(
            content="Aluminum 6061 has a yield strength of 276 MPa",
            knowledge_type=KnowledgeType.MATERIAL_PROPERTY,
            source="materials-db",
        )
        mock_store.search = AsyncMock(return_value=[SearchResult(entry=entry, score=0.92)])

        handler = RetrieveKnowledgeHandler(mock_context, mock_store)
        input_data = RetrieveKnowledgeInput(query="aluminum yield strength", limit=5)
        output = await handler.execute(input_data)

        assert output.total_results == 1
        assert output.results[0].score == 0.92
        assert "Aluminum 6061" in output.results[0].content
        assert output.results[0].knowledge_type == "material_property"
        assert output.query == "aluminum yield strength"

    async def test_execute_with_type_filter(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        mock_store.search = AsyncMock(return_value=[])

        handler = RetrieveKnowledgeHandler(mock_context, mock_store)
        input_data = RetrieveKnowledgeInput(
            query="design rules for PCB",
            knowledge_type="design_rule",
            limit=3,
        )
        output = await handler.execute(input_data)

        mock_store.search.assert_awaited_once_with(
            query="design rules for PCB",
            knowledge_type=KnowledgeType.DESIGN_RULE,
            limit=3,
        )
        assert output.total_results == 0
        assert output.results == []

    async def test_execute_with_invalid_type_searches_all(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        mock_store.search = AsyncMock(return_value=[])

        handler = RetrieveKnowledgeHandler(mock_context, mock_store)
        input_data = RetrieveKnowledgeInput(
            query="something",
            knowledge_type="nonexistent_type",
        )
        output = await handler.execute(input_data)

        # Should fall back to searching all types (knowledge_type=None)
        mock_store.search.assert_awaited_once_with(
            query="something",
            knowledge_type=None,
            limit=5,
        )
        assert output.total_results == 0

    async def test_run_validates_input(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        handler = RetrieveKnowledgeHandler(mock_context, mock_store)
        input_data = RetrieveKnowledgeInput(query="test query")
        mock_store.search = AsyncMock(return_value=[])

        result = await handler.run(input_data)
        assert result.success is True

    async def test_schema_validation_rejects_empty_query(self) -> None:
        with pytest.raises(Exception):
            RetrieveKnowledgeInput(query="")
