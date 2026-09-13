"""Skill-specific tests for record_compliance_checklist.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_record_compliance_checklist.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.compliance.models import ComplianceRegime
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import RecordComplianceChecklistHandler
from .schema import RecordComplianceChecklistInput


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def sample_input() -> RecordComplianceChecklistInput:
    return RecordComplianceChecklistInput(
        project_id="proj-1", target_markets=[ComplianceRegime.FCC]
    )


class TestRecordComplianceChecklistSkill:
    async def test_execute_returns_output(
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
        assert output.node_id == "node-1"
        assert output.total_items > 0

    async def test_preconditions_catch_missing_tool(
        self, mock_context: SkillContext, sample_input: RecordComplianceChecklistInput
    ) -> None:
        handler = RecordComplianceChecklistHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
