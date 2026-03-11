"""Tests for skill_registry: SkillBase, SkillContext, McpBridge, SchemaValidator."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from skill_registry.mcp_bridge import (
    InMemoryMcpBridge,
    McpToolError,
)
from skill_registry.schema_validator import SchemaValidator, SkillDefinition
from skill_registry.skill_base import SkillBase, SkillContext, SkillResult

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class DummyInput(BaseModel):
    value: int


class DummyOutput(BaseModel):
    result: str


class EchoSkill(SkillBase[DummyInput, DummyOutput]):
    """Concrete skill for testing."""

    input_type = DummyInput
    output_type = DummyOutput

    async def execute(self, input_data: DummyInput) -> DummyOutput:
        return DummyOutput(result=f"echo-{input_data.value}")


class FailingSkill(SkillBase[DummyInput, DummyOutput]):
    """Skill that always raises during execute."""

    input_type = DummyInput
    output_type = DummyOutput

    async def execute(self, input_data: DummyInput) -> DummyOutput:
        raise RuntimeError("boom")


class PreconditionSkill(SkillBase[DummyInput, DummyOutput]):
    """Skill with failing preconditions."""

    input_type = DummyInput
    output_type = DummyOutput

    async def validate_preconditions(self, input_data: DummyInput) -> list[str]:
        if input_data.value < 0:
            return ["Value must be non-negative"]
        return []

    async def execute(self, input_data: DummyInput) -> DummyOutput:
        return DummyOutput(result=f"ok-{input_data.value}")


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.logger = MagicMock()
    ctx.logger.bind.return_value = ctx.logger
    ctx.session_id = uuid4()
    ctx.branch = "main"
    ctx.metrics_collector = None
    ctx.domain = "unknown"
    return ctx


# ---------------------------------------------------------------------------
# TestSkillBase
# ---------------------------------------------------------------------------


class TestSkillBase:
    async def test_concrete_skill_executes_successfully(self, mock_context: SkillContext) -> None:
        skill = EchoSkill(mock_context)
        output = await skill.execute(DummyInput(value=42))
        assert output.result == "echo-42"

    async def test_skill_run_returns_result_with_metadata(self, mock_context: SkillContext) -> None:
        skill = EchoSkill(mock_context)
        result = await skill.run(DummyInput(value=7))
        assert isinstance(result, SkillResult)
        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, DummyOutput)
        assert result.data.result == "echo-7"  # type: ignore[union-attr]
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_skill_run_catches_execution_errors(self, mock_context: SkillContext) -> None:
        skill = FailingSkill(mock_context)
        result = await skill.run(DummyInput(value=1))
        assert result.success is False
        assert len(result.errors) == 1
        assert "Execution failed" in result.errors[0]
        assert "boom" in result.errors[0]
        assert result.duration_ms >= 0

    async def test_skill_run_checks_preconditions(self, mock_context: SkillContext) -> None:
        skill = PreconditionSkill(mock_context)
        result = await skill.run(DummyInput(value=-1))
        assert result.success is False
        assert "Value must be non-negative" in result.errors

    async def test_validate_preconditions_default_returns_empty(
        self, mock_context: SkillContext
    ) -> None:
        skill = EchoSkill(mock_context)
        errors = await skill.validate_preconditions(DummyInput(value=1))
        assert errors == []


# ---------------------------------------------------------------------------
# TestSkillContext
# ---------------------------------------------------------------------------


class TestSkillContext:
    def test_context_holds_dependencies(self) -> None:
        twin = MagicMock()
        mcp = InMemoryMcpBridge()
        logger = MagicMock()
        sid = uuid4()
        ctx = SkillContext(twin=twin, mcp=mcp, logger=logger, session_id=sid, branch="dev")
        assert ctx.twin is twin
        assert ctx.mcp is mcp
        assert ctx.logger is logger
        assert ctx.session_id == sid
        assert ctx.branch == "dev"

    def test_context_default_branch_is_main(self) -> None:
        ctx = SkillContext(
            twin=MagicMock(),
            mcp=InMemoryMcpBridge(),
            logger=MagicMock(),
            session_id=uuid4(),
        )
        assert ctx.branch == "main"


# ---------------------------------------------------------------------------
# TestMcpBridge
# ---------------------------------------------------------------------------


class TestMcpBridge:
    async def test_in_memory_bridge_invoke(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool_response("calculix.run_fea", {"stress": 42.0})
        result = await bridge.invoke("calculix.run_fea", {"mesh": "test.inp"})
        assert result == {"stress": 42.0}

    async def test_in_memory_bridge_tool_not_found_raises(self) -> None:
        bridge = InMemoryMcpBridge()
        with pytest.raises(McpToolError) as exc_info:
            await bridge.invoke("nonexistent.tool", {})
        assert "nonexistent.tool" in str(exc_info.value)

    async def test_in_memory_bridge_is_available(self) -> None:
        bridge = InMemoryMcpBridge()
        assert await bridge.is_available("calculix.run_fea") is False
        bridge.register_tool_response("calculix.run_fea", {})
        assert await bridge.is_available("calculix.run_fea") is True

    async def test_in_memory_bridge_list_tools(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("tool_a", "fea", "Tool A")
        bridge.register_tool("tool_b", "cad", "Tool B")
        tools = await bridge.list_tools()
        assert len(tools) == 2

    async def test_in_memory_bridge_list_tools_by_capability(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("tool_a", "fea", "Tool A")
        bridge.register_tool("tool_b", "cad", "Tool B")
        bridge.register_tool("tool_c", "fea", "Tool C")
        fea_tools = await bridge.list_tools(capability="fea")
        assert len(fea_tools) == 2
        assert all(t["capability"] == "fea" for t in fea_tools)


# ---------------------------------------------------------------------------
# TestSchemaValidator
# ---------------------------------------------------------------------------

VALID_DEFINITION: dict[str, object] = {
    "name": "validate_stress",
    "version": "1.0.0",
    "domain": "mechanical",
    "agent": "mechanical_agent",
    "description": "Validates stress analysis results from FEA",
    "phase": 1,
    "input_schema": "domain_agents.mechanical.skills.validate_stress.schema.Input",
    "output_schema": "domain_agents.mechanical.skills.validate_stress.schema.Output",
    "tools_required": [
        {"tool_id": "calculix.run_fea", "capability": "fea_analysis", "required": True}
    ],
}


class TestSchemaValidator:
    def test_validate_valid_definition(self) -> None:
        defn = SchemaValidator.validate_definition(dict(VALID_DEFINITION))
        assert isinstance(defn, SkillDefinition)
        assert defn.name == "validate_stress"
        assert defn.version == "1.0.0"
        assert defn.phase == 1
        assert len(defn.tools_required) == 1

    def test_validate_definition_rejects_invalid_name(self) -> None:
        data = dict(VALID_DEFINITION)
        data["name"] = "InvalidName"
        with pytest.raises(ValidationError):
            SchemaValidator.validate_definition(data)

    def test_validate_definition_rejects_missing_fields(self) -> None:
        with pytest.raises(ValidationError):
            SchemaValidator.validate_definition({"name": "foo"})

    def test_validate_input_from_dict(self) -> None:
        result = SchemaValidator.validate_input(DummyInput, {"value": 10})
        assert isinstance(result, DummyInput)
        assert result.value == 10  # type: ignore[union-attr]

    def test_validate_input_from_model_instance(self) -> None:
        inp = DummyInput(value=5)
        result = SchemaValidator.validate_input(DummyInput, inp)
        assert result is inp

    def test_validate_output_from_dict(self) -> None:
        result = SchemaValidator.validate_output(DummyOutput, {"result": "ok"})
        assert isinstance(result, DummyOutput)
        assert result.result == "ok"  # type: ignore[union-attr]
