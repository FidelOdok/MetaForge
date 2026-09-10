"""Unit tests for the skill-layer -> harness NativeToolDef bridge."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from api_gateway.chat.skill_tools import skill_tools_from_registry
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.registry import SkillRegistration, SkillRegistry
from skill_registry.skill_base import SkillBase
from twin_core.api import InMemoryTwinAPI


class _EchoInput(BaseModel):
    """A minimal skill input for a stub skill."""

    message: str


class _EchoOutput(BaseModel):
    """A minimal skill output for a stub skill."""

    echoed: str


class _EchoSkill(SkillBase[_EchoInput, _EchoOutput]):
    """A stub skill that just echoes its input, for bridge testing."""

    input_type = _EchoInput
    output_type = _EchoOutput

    async def execute(self, input_data: _EchoInput) -> _EchoOutput:
        return _EchoOutput(echoed=input_data.message)


class _FailingSkill(SkillBase[_EchoInput, _EchoOutput]):
    """A stub skill that always fails a precondition, for error-path testing."""

    input_type = _EchoInput
    output_type = _EchoOutput

    async def validate_preconditions(self, input_data: _EchoInput) -> list[str]:
        return ["always fails"]

    async def execute(self, input_data: _EchoInput) -> _EchoOutput:
        raise AssertionError("should never reach execute()")


def _registration(
    name: str, handler_class: type[SkillBase], domain: str = "mechanical"
) -> SkillRegistration:
    return SkillRegistration(
        name=name,
        version="0.1.0",
        domain=domain,
        agent=domain,
        description=f"Stub skill {name}",
        phase=1,
        input_schema=_EchoInput,
        output_schema=_EchoOutput,
        handler_class=handler_class,
        tools_required=[],
    )


async def _fake_registry(*regs: SkillRegistration) -> SkillRegistry:
    registry = SkillRegistry()
    for reg in regs:
        registry._skills[reg.name] = reg  # noqa: SLF001 - test seeding, no public setter exists
    return registry


class TestSkillToolsFromRegistry:
    """Bridge behavior: naming, schema pass-through, domain filtering."""

    async def test_produces_namespaced_tool_per_matching_domain_skill(self):
        registry = await _fake_registry(
            _registration("echo", _EchoSkill, domain="mechanical"),
            _registration("other_echo", _EchoSkill, domain="electronics"),
        )
        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id=str(uuid4()),
            domain="mechanical",
            registry=registry,
        )
        assert [t.name for t in tools] == ["skill_mechanical_echo"]
        assert tools[0].description == "Stub skill echo"
        assert tools[0].input_schema["properties"]["message"]["type"] == "string"

    async def test_handler_round_trips_through_skill_context_on_success(self):
        registry = await _fake_registry(_registration("echo", _EchoSkill))
        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id=str(uuid4()),
            registry=registry,
        )
        result = await tools[0].handler({"message": "hello"})
        assert result == {"success": True, "echoed": "hello"}

    async def test_handler_surfaces_skill_result_failure_without_raising(self):
        registry = await _fake_registry(_registration("failing", _FailingSkill))
        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id=str(uuid4()),
            registry=registry,
        )
        result = await tools[0].handler({"message": "hello"})
        assert result == {"success": False, "errors": ["always fails"]}

    async def test_invalid_session_id_falls_back_to_nil_uuid_instead_of_raising(self):
        registry = await _fake_registry(_registration("echo", _EchoSkill))
        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id="not-a-uuid",
            registry=registry,
        )
        result = await tools[0].handler({"message": "still works"})
        assert result == {"success": True, "echoed": "still works"}

    async def test_domain_outside_scope_is_excluded(self):
        registry = await _fake_registry(_registration("echo", _EchoSkill, domain="firmware"))
        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id=str(uuid4()),
            domain="mechanical",
            registry=registry,
        )
        assert tools == []


class TestRealMechanicalSkillBridging:
    """Integration-style: a real skill's registration bridges cleanly."""

    async def test_generate_cad_ir_produces_valid_json_schema_tool(self):
        registry = SkillRegistry()
        await registry.register("domain_agents/mechanical/skills/generate_cad_ir")

        tools = await skill_tools_from_registry(
            twin=InMemoryTwinAPI.create(),
            mcp_bridge=InMemoryMcpBridge(),
            session_id=str(uuid4()),
            registry=registry,
        )

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "skill_mechanical_generate_cad_ir"
        assert tool.input_schema["type"] == "object"
        assert "entities" in tool.input_schema["properties"]
