"""Tests for the record_compliance_checklist skill (MET-747 lifecycle-mapping follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.compliance.models import ComplianceRegime
from domain_agents.compliance.skills.record_compliance_checklist.handler import (
    RecordComplianceChecklistHandler,
)
from domain_agents.compliance.skills.record_compliance_checklist.schema import (
    RecordComplianceChecklistInput,
    RecordComplianceChecklistOutput,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    ctx.metrics_collector = None
    ctx.domain = "compliance"
    return ctx


@pytest.fixture()
def sample_input() -> RecordComplianceChecklistInput:
    return RecordComplianceChecklistInput(
        project_id="proj-1", target_markets=[ComplianceRegime.FCC, ComplianceRegime.CE]
    )


class TestRecordComplianceChecklistHandler:
    async def test_generates_and_persists_checklist(
        self, mock_context: SkillContext, sample_input: RecordComplianceChecklistInput
    ) -> None:
        mock_context.mcp.register_tool(
            "twin.commit_compliance_checklist", "twin_compliance_checklist"
        )
        mock_context.mcp.register_tool_response(
            "twin.commit_compliance_checklist", {"node_id": "node-1"}
        )
        handler = RecordComplianceChecklistHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, RecordComplianceChecklistOutput)
        assert output.node_id == "node-1"
        assert output.total_items == len(output.items)
        assert set(output.target_markets) == {ComplianceRegime.FCC, ComplianceRegime.CE}

    async def test_invokes_commit_tool_with_serialized_items(
        self, mock_context: SkillContext, sample_input: RecordComplianceChecklistInput
    ) -> None:
        mock_context.mcp.register_tool(
            "twin.commit_compliance_checklist", "twin_compliance_checklist"
        )
        mock_context.mcp.register_tool_response(
            "twin.commit_compliance_checklist", {"node_id": "node-1"}
        )
        handler = RecordComplianceChecklistHandler(mock_context)
        output = await handler.execute(sample_input)
        _, params = mock_context.mcp.calls[0]
        assert params["items"]
        assert isinstance(params["items"][0]["regime"], str)
        assert len(params["items"]) == output.total_items


class TestSkillRunPipeline:
    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: RecordComplianceChecklistInput
    ) -> None:
        handler = RecordComplianceChecklistHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is False
        assert result.data is None
