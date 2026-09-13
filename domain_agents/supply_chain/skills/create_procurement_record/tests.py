"""Skill-specific tests for create_procurement_record.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_create_procurement_record.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import CreateProcurementRecordHandler
from .schema import CreateProcurementRecordInput, ProcurementLineItem


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
def sample_input() -> CreateProcurementRecordInput:
    return CreateProcurementRecordInput(
        name="PO-1",
        line_items=[
            ProcurementLineItem(part_number="R-1001", quantity=100, unit_cost=0.02),
        ],
    )


class TestCreateProcurementRecordSkill:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_procurement_record", "twin_procurement_record")
        mock_context.mcp.register_tool_response(
            "twin.commit_procurement_record",
            {
                "node_id": "node-1",
                "line_item_count": 1,
                "total_cost": 2.0,
                "currency": "USD",
                "max_lead_time_days": 0,
            },
        )
        handler = CreateProcurementRecordHandler(mock_context)
        output = await handler.execute(sample_input)
        assert output.node_id == "node-1"
        assert output.total_cost == 2.0

    async def test_preconditions_catch_missing_tool(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        handler = CreateProcurementRecordHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
