"""Tests for the create_procurement_record skill (MET-747 lifecycle-mapping follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.supply_chain.skills.create_procurement_record.handler import (
    CreateProcurementRecordHandler,
)
from domain_agents.supply_chain.skills.create_procurement_record.schema import (
    CreateProcurementRecordInput,
    CreateProcurementRecordOutput,
    ProcurementLineItem,
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
    ctx.domain = "supply_chain"
    return ctx


@pytest.fixture()
def sample_input() -> CreateProcurementRecordInput:
    return CreateProcurementRecordInput(
        name="PO-1",
        line_items=[
            ProcurementLineItem(
                part_number="R-1001", quantity=100, unit_cost=0.02, lead_time_days=5
            ),
            ProcurementLineItem(
                part_number="C-2002", quantity=50, unit_cost=0.05, lead_time_days=10
            ),
        ],
    )


class TestSchemas:
    def test_quantity_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementLineItem(part_number="x", quantity=0, unit_cost=1.0)

    def test_line_items_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            CreateProcurementRecordInput(name="PO-1", line_items=[])


class TestCreateProcurementRecordHandler:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_procurement_record", "twin_procurement_record")
        mock_context.mcp.register_tool_response(
            "twin.commit_procurement_record",
            {
                "node_id": "node-1",
                "line_item_count": 2,
                "total_cost": 4.5,
                "currency": "USD",
                "max_lead_time_days": 10,
            },
        )
        handler = CreateProcurementRecordHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, CreateProcurementRecordOutput)
        assert output.max_lead_time_days == 10

    async def test_bom_work_product_id_becomes_source_node_id(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        from uuid import uuid4 as _uuid4

        bom_id = _uuid4()
        sample_input = sample_input.model_copy(update={"bom_work_product_id": bom_id})
        mock_context.twin.get_work_product.return_value = {"id": str(bom_id)}
        mock_context.mcp.register_tool("twin.commit_procurement_record", "twin_procurement_record")
        mock_context.mcp.register_tool_response(
            "twin.commit_procurement_record",
            {
                "node_id": "node-1",
                "line_item_count": 2,
                "total_cost": 4.5,
                "currency": "USD",
                "max_lead_time_days": 10,
            },
        )
        handler = CreateProcurementRecordHandler(mock_context)
        await handler.execute(sample_input)
        _, params = mock_context.mcp.calls[0]
        assert params["source_node_ids"] == [str(bom_id)]


class TestPreconditions:
    async def test_missing_bom_fails(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        from uuid import uuid4 as _uuid4

        sample_input = sample_input.model_copy(update={"bom_work_product_id": _uuid4()})
        mock_context.twin.get_work_product.return_value = None
        mock_context.mcp.register_tool("twin.commit_procurement_record", "twin_procurement_record")
        handler = CreateProcurementRecordHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not found" in e for e in errors)

    async def test_no_bom_given_skips_that_check(
        self, mock_context: SkillContext, sample_input: CreateProcurementRecordInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_procurement_record", "twin_procurement_record")
        handler = CreateProcurementRecordHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert errors == []
