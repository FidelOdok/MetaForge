"""MetaForge Skill Registry — foundational classes for the skill system."""

from skill_registry.loader import LoadedSkill, SkillLoader
from skill_registry.loader import SkillLoadError as _LoaderSkillLoadError
from skill_registry.mcp_bridge import (
    InMemoryMcpBridge,
    McpBridge,
    McpTimeoutError,
    McpToolError,
)
from skill_registry.registry import (
    SkillRegistration,
    SkillRegistry,
)
from skill_registry.schema_validator import (
    SchemaValidator,
    SkillDefinition,
    ToolRef,
)
from skill_registry.skill_base import SkillBase, SkillContext, SkillResult

# Use the loader's SkillLoadError as the canonical export
SkillLoadError = _LoaderSkillLoadError

__all__ = [
    "InMemoryMcpBridge",
    "LoadedSkill",
    "McpBridge",
    "McpTimeoutError",
    "McpToolError",
    "SchemaValidator",
    "SkillBase",
    "SkillContext",
    "SkillDefinition",
    "SkillLoadError",
    "SkillLoader",
    "SkillRegistration",
    "SkillRegistry",
    "SkillResult",
    "ToolRef",
]
