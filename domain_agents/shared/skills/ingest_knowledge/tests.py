"""Skill-specific tests for ingest_knowledge.

These tests live alongside the skill for co-location.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext
from twin_core.knowledge.models import KnowledgeEntry, KnowledgeType
from twin_core.knowledge.store import KnowledgeStore

from .handler import IngestKnowledgeHandler
from .schema import IngestKnowledgeInput


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


class TestIngestKnowledgeSkill:
    """Co-located tests for the ingest_knowledge handler."""

    async def test_execute_ingests_content(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entry = KnowledgeEntry(
            content="Aluminum 6061-T6 yield strength is 276 MPa",
            knowledge_type=KnowledgeType.MATERIAL_PROPERTY,
            source="materials-db",
            embedding=[0.1] * 128,
        )
        mock_store.ingest_chunked = AsyncMock(return_value=[entry])

        handler = IngestKnowledgeHandler(mock_context, mock_store)
        input_data = IngestKnowledgeInput(
            content="Aluminum 6061-T6 yield strength is 276 MPa",
            knowledge_type="material_property",
            source="materials-db",
        )
        output = await handler.execute(input_data)

        assert output.entry_id == str(entry.id)
        assert output.embedded is True
        assert output.chunk_count == 1
        assert output.content_length == len(input_data.content)

    async def test_execute_with_metadata(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entry = KnowledgeEntry(
            content="IPC-2221 minimum trace width for 1A is 10mil",
            knowledge_type=KnowledgeType.DESIGN_RULE,
            source="IPC-2221",
            embedding=[0.2] * 128,
        )
        mock_store.ingest_chunked = AsyncMock(return_value=[entry])

        handler = IngestKnowledgeHandler(mock_context, mock_store)
        input_data = IngestKnowledgeInput(
            content="IPC-2221 minimum trace width for 1A is 10mil",
            knowledge_type="design_rule",
            source="IPC-2221",
            metadata={"standard_version": "2012", "section": "6.2"},
        )
        output = await handler.execute(input_data)

        mock_store.ingest_chunked.assert_awaited_once_with(
            content=input_data.content,
            knowledge_type=KnowledgeType.DESIGN_RULE,
            source="IPC-2221",
            metadata={"standard_version": "2012", "section": "6.2"},
        )
        assert output.embedded is True

    async def test_execute_with_unknown_type_defaults_to_general(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entry = KnowledgeEntry(
            content="Some content",
            knowledge_type=KnowledgeType.GENERAL,
            source="test",
            embedding=[0.3] * 128,
        )
        mock_store.ingest_chunked = AsyncMock(return_value=[entry])

        handler = IngestKnowledgeHandler(mock_context, mock_store)
        input_data = IngestKnowledgeInput(
            content="Some content",
            knowledge_type="totally_unknown_type",
            source="test",
        )
        output = await handler.execute(input_data)

        # Should fall back to GENERAL
        mock_store.ingest_chunked.assert_awaited_once_with(
            content="Some content",
            knowledge_type=KnowledgeType.GENERAL,
            source="test",
            metadata={},
        )
        assert output.entry_id == str(entry.id)

    async def test_execute_chunked_content(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entries = [
            KnowledgeEntry(
                content=f"chunk {i}",
                knowledge_type=KnowledgeType.BEST_PRACTICE,
                source="docs",
                embedding=[0.1 * i] * 128,
            )
            for i in range(3)
        ]
        mock_store.ingest_chunked = AsyncMock(return_value=entries)

        handler = IngestKnowledgeHandler(mock_context, mock_store)
        input_data = IngestKnowledgeInput(
            content="A very long document " * 100,
            knowledge_type="best_practice",
            source="docs",
        )
        output = await handler.execute(input_data)

        assert output.chunk_count == 3
        assert output.entry_id == str(entries[0].id)

    async def test_run_validates_input(
        self, mock_context: SkillContext, mock_store: KnowledgeStore
    ) -> None:
        entry = KnowledgeEntry(
            content="test",
            knowledge_type=KnowledgeType.GENERAL,
            source="test",
            embedding=[0.1] * 128,
        )
        mock_store.ingest_chunked = AsyncMock(return_value=[entry])

        handler = IngestKnowledgeHandler(mock_context, mock_store)
        input_data = IngestKnowledgeInput(
            content="test content",
            knowledge_type="general",
            source="test",
        )
        result = await handler.run(input_data)
        assert result.success is True

    async def test_schema_validation_rejects_empty_content(self) -> None:
        with pytest.raises(Exception):
            IngestKnowledgeInput(
                content="", knowledge_type="general", source="test"
            )

    async def test_schema_validation_rejects_empty_source(self) -> None:
        with pytest.raises(Exception):
            IngestKnowledgeInput(
                content="some content", knowledge_type="general", source=""
            )
