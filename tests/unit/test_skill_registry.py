"""Tests for the Skill Registry — auto-discovery, registration, lifecycle, and querying."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from skill_registry.registry import SkillLoadError, SkillRegistration, SkillRegistry
from skill_registry.skill_base import SkillBase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_skill(
    tmp_path: Path,
    name: str = "test_skill",
    domain: str = "mechanical",
    phase: int = 1,
    tags: list[str] | None = None,
    tools_required: list[dict[str, Any]] | None = None,
) -> str:
    """Create a minimal test skill directory under ``tmp_path/<domain>/skills/<name>/``."""
    skill_dir = tmp_path / domain / "skills" / name
    skill_dir.mkdir(parents=True)

    definition: dict[str, Any] = {
        "name": name,
        "version": "0.1.0",
        "domain": domain,
        "agent": domain,
        "description": f"Test skill for {name} validation",
        "phase": phase,
        "tools_required": tools_required or [],
        "input_schema": "schema.TestInput",
        "output_schema": "schema.TestOutput",
    }
    if tags is not None:
        definition["tags"] = tags
    (skill_dir / "definition.json").write_text(json.dumps(definition))

    (skill_dir / "schema.py").write_text(
        """\
from pydantic import BaseModel


class TestInput(BaseModel):
    value: str


class TestOutput(BaseModel):
    result: str
"""
    )

    (skill_dir / "handler.py").write_text(
        """\
from pydantic import BaseModel
from skill_registry.skill_base import SkillBase


class TestInput(BaseModel):
    value: str


class TestOutput(BaseModel):
    result: str


class TestHandler(SkillBase["TestInput", "TestOutput"]):
    input_type = TestInput
    output_type = TestOutput

    async def execute(self, input_data: "TestInput") -> "TestOutput":
        return TestOutput(result=f"processed: {input_data.value}")
"""
    )

    return str(skill_dir)


# ---------------------------------------------------------------------------
# TestSkillRegistration (model-level tests)
# ---------------------------------------------------------------------------


class TestSkillRegistration:
    """Tests for the SkillRegistration Pydantic model."""

    def test_registration_model_creation(self) -> None:
        """A SkillRegistration can be created with all required fields."""
        reg = SkillRegistration(
            name="test_skill",
            version="0.1.0",
            domain="mechanical",
            agent="mechanical",
            description="A test skill",
            phase=1,
            input_schema=BaseModel,
            output_schema=BaseModel,
            handler_class=SkillBase,  # type: ignore[arg-type]
            tools_required=[],
        )
        assert reg.name == "test_skill"
        assert reg.version == "0.1.0"
        assert reg.domain == "mechanical"

    def test_registration_default_status(self) -> None:
        """Default status should be REGISTERED."""
        reg = SkillRegistration(
            name="test_skill",
            version="0.1.0",
            domain="mechanical",
            agent="mechanical",
            description="A test skill",
            phase=1,
            input_schema=BaseModel,
            output_schema=BaseModel,
            handler_class=SkillBase,  # type: ignore[arg-type]
            tools_required=[],
        )
        assert reg.status == "REGISTERED"
        assert reg.deprecation_reason is None
        assert reg.tags == []
        assert reg.timeout_seconds == 120
        assert reg.retries == 0
        assert reg.idempotent is False


# ---------------------------------------------------------------------------
# TestSkillRegistry (registration & query tests)
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    """Tests for skill registration and querying."""

    async def test_register_skill_from_path(self, tmp_path: Path) -> None:
        """register() should load a valid skill from its directory."""
        skill_dir = _create_test_skill(tmp_path)
        registry = SkillRegistry()
        reg = await registry.register(skill_dir)

        assert reg.name == "test_skill"
        assert reg.version == "0.1.0"
        assert reg.domain == "mechanical"
        assert reg.agent == "mechanical"
        assert reg.status == "REGISTERED"
        assert reg.skill_path == skill_dir

    async def test_register_missing_definition_raises(self, tmp_path: Path) -> None:
        """register() should raise SkillLoadError if definition.json is missing."""
        empty_dir = tmp_path / "empty_skill"
        empty_dir.mkdir(parents=True)

        registry = SkillRegistry()
        with pytest.raises(SkillLoadError, match="definition.json not found"):
            await registry.register(str(empty_dir))

    async def test_register_invalid_json_raises(self, tmp_path: Path) -> None:
        """register() should raise SkillLoadError for malformed JSON."""
        skill_dir = tmp_path / "bad_json"
        skill_dir.mkdir(parents=True)
        (skill_dir / "definition.json").write_text("{invalid json")

        registry = SkillRegistry()
        with pytest.raises(SkillLoadError, match="Invalid JSON"):
            await registry.register(str(skill_dir))

    async def test_register_missing_schema_raises(self, tmp_path: Path) -> None:
        """register() should raise SkillLoadError when schema.py is absent."""
        skill_dir = tmp_path / "mechanical" / "skills" / "no_schema"
        skill_dir.mkdir(parents=True)
        definition = {
            "name": "no_schema",
            "version": "0.1.0",
            "domain": "mechanical",
            "agent": "mechanical",
            "description": "Skill without schema module",
            "phase": 1,
            "tools_required": [],
            "input_schema": "schema.TestInput",
            "output_schema": "schema.TestOutput",
        }
        (skill_dir / "definition.json").write_text(json.dumps(definition))

        registry = SkillRegistry()
        with pytest.raises(SkillLoadError, match="schema.py not found"):
            await registry.register(str(skill_dir))

    async def test_register_missing_handler_raises(self, tmp_path: Path) -> None:
        """register() should raise SkillLoadError when handler.py is absent."""
        skill_dir = tmp_path / "mechanical" / "skills" / "no_handler"
        skill_dir.mkdir(parents=True)
        definition = {
            "name": "no_handler",
            "version": "0.1.0",
            "domain": "mechanical",
            "agent": "mechanical",
            "description": "Skill without handler module",
            "phase": 1,
            "tools_required": [],
            "input_schema": "schema.TestInput",
            "output_schema": "schema.TestOutput",
        }
        (skill_dir / "definition.json").write_text(json.dumps(definition))
        (skill_dir / "schema.py").write_text(
            """\
from pydantic import BaseModel

class TestInput(BaseModel):
    value: str

class TestOutput(BaseModel):
    result: str
"""
        )

        registry = SkillRegistry()
        with pytest.raises(SkillLoadError, match="handler.py not found"):
            await registry.register(str(skill_dir))

    async def test_register_duplicate_raises(self, tmp_path: Path) -> None:
        """register() should raise SkillLoadError when a skill is already registered."""
        skill_dir = _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(skill_dir)

        with pytest.raises(SkillLoadError, match="Skill already registered"):
            await registry.register(skill_dir)

    async def test_get_registered_skill(self, tmp_path: Path) -> None:
        """get() should return a previously registered skill."""
        skill_dir = _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(skill_dir)

        result = await registry.get("test_skill")
        assert result is not None
        assert result.name == "test_skill"

    async def test_get_nonexistent_returns_none(self) -> None:
        """get() should return None for a skill that does not exist."""
        registry = SkillRegistry()
        result = await registry.get("nonexistent")
        assert result is None

    async def test_list_all_skills(self, tmp_path: Path) -> None:
        """list_skills() with no filters should return all registered skills."""
        _create_test_skill(tmp_path, name="skill_one", domain="mechanical")
        _create_test_skill(tmp_path, name="skill_two", domain="electronics")
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "skill_one"))
        await registry.register(str(tmp_path / "electronics" / "skills" / "skill_two"))

        results = await registry.list_skills()
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"skill_one", "skill_two"}

    async def test_list_skills_by_domain(self, tmp_path: Path) -> None:
        """list_skills(domain=...) should filter by engineering domain."""
        _create_test_skill(tmp_path, name="skill_mech", domain="mechanical")
        _create_test_skill(tmp_path, name="skill_elec", domain="electronics")
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "skill_mech"))
        await registry.register(
            str(tmp_path / "electronics" / "skills" / "skill_elec")
        )

        mech = await registry.list_skills(domain="mechanical")
        assert len(mech) == 1
        assert mech[0].name == "skill_mech"

    async def test_list_skills_by_phase(self, tmp_path: Path) -> None:
        """list_skills(phase=N) should return skills where skill.phase <= N."""
        _create_test_skill(tmp_path, name="skill_p1", domain="mechanical", phase=1)
        _create_test_skill(tmp_path, name="skill_p2", domain="electronics", phase=2)
        _create_test_skill(tmp_path, name="skill_p3", domain="firmware", phase=3)
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "skill_p1"))
        await registry.register(str(tmp_path / "electronics" / "skills" / "skill_p2"))
        await registry.register(str(tmp_path / "firmware" / "skills" / "skill_p3"))

        phase1 = await registry.list_skills(phase=1)
        assert len(phase1) == 1
        assert phase1[0].name == "skill_p1"

        phase2 = await registry.list_skills(phase=2)
        assert len(phase2) == 2
        names = {s.name for s in phase2}
        assert names == {"skill_p1", "skill_p2"}

        phase3 = await registry.list_skills(phase=3)
        assert len(phase3) == 3

    async def test_list_skills_by_status(self, tmp_path: Path) -> None:
        """list_skills(status=...) should filter by lifecycle status."""
        _create_test_skill(tmp_path, name="skill_reg", domain="mechanical")
        _create_test_skill(tmp_path, name="skill_act", domain="electronics")
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "skill_reg"))
        await registry.register(
            str(tmp_path / "electronics" / "skills" / "skill_act")
        )
        await registry.activate("skill_act")

        registered = await registry.list_skills(status="REGISTERED")
        assert len(registered) == 1
        assert registered[0].name == "skill_reg"

        active = await registry.list_skills(status="ACTIVE")
        assert len(active) == 1
        assert active[0].name == "skill_act"

    async def test_list_skills_by_tags(self, tmp_path: Path) -> None:
        """list_skills(tags=...) should only return skills with all required tags."""
        _create_test_skill(
            tmp_path,
            name="skill_tagged",
            domain="mechanical",
            tags=["fea", "stress"],
        )
        _create_test_skill(
            tmp_path,
            name="skill_other",
            domain="electronics",
            tags=["erc"],
        )
        registry = SkillRegistry()
        await registry.register(
            str(tmp_path / "mechanical" / "skills" / "skill_tagged")
        )
        await registry.register(
            str(tmp_path / "electronics" / "skills" / "skill_other")
        )

        fea = await registry.list_skills(tags=["fea"])
        assert len(fea) == 1
        assert fea[0].name == "skill_tagged"

        fea_stress = await registry.list_skills(tags=["fea", "stress"])
        assert len(fea_stress) == 1

        no_match = await registry.list_skills(tags=["nonexistent"])
        assert len(no_match) == 0


# ---------------------------------------------------------------------------
# TestSkillLifecycle
# ---------------------------------------------------------------------------


class TestSkillLifecycle:
    """Tests for the skill lifecycle transitions."""

    async def test_activate_registered_skill(self, tmp_path: Path) -> None:
        """activate() should transition REGISTERED -> ACTIVE."""
        _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "test_skill"))

        await registry.activate("test_skill")
        reg = await registry.get("test_skill")
        assert reg is not None
        assert reg.status == "ACTIVE"

    async def test_activate_nonexistent_raises(self) -> None:
        """activate() should raise KeyError for unknown skills."""
        registry = SkillRegistry()
        with pytest.raises(KeyError, match="not found"):
            await registry.activate("nonexistent")

    async def test_activate_wrong_status_raises(self, tmp_path: Path) -> None:
        """activate() should raise ValueError for non-REGISTERED skills."""
        _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "test_skill"))
        await registry.activate("test_skill")

        with pytest.raises(ValueError, match="must be REGISTERED"):
            await registry.activate("test_skill")

    async def test_deprecate_active_skill(self, tmp_path: Path) -> None:
        """deprecate() should transition ACTIVE -> DEPRECATED with reason."""
        _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "test_skill"))
        await registry.activate("test_skill")

        await registry.deprecate("test_skill", "replaced by v2")
        reg = await registry.get("test_skill")
        assert reg is not None
        assert reg.status == "DEPRECATED"
        assert reg.deprecation_reason == "replaced by v2"

    async def test_deprecate_registered_skill(self, tmp_path: Path) -> None:
        """deprecate() should also work from REGISTERED state."""
        _create_test_skill(tmp_path)
        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "test_skill"))

        await registry.deprecate("test_skill", "no longer needed")
        reg = await registry.get("test_skill")
        assert reg is not None
        assert reg.status == "DEPRECATED"

    async def test_deprecate_nonexistent_raises(self) -> None:
        """deprecate() should raise KeyError for unknown skills."""
        registry = SkillRegistry()
        with pytest.raises(KeyError, match="not found"):
            await registry.deprecate("nonexistent", "reason")


# ---------------------------------------------------------------------------
# TestSkillDiscovery
# ---------------------------------------------------------------------------


class TestSkillDiscovery:
    """Tests for the auto-discovery mechanism."""

    async def test_discover_from_directory(self, tmp_path: Path) -> None:
        """discover() should find and register all valid skills in the search path."""
        _create_test_skill(tmp_path, name="skill_a", domain="mechanical")
        _create_test_skill(tmp_path, name="skill_b", domain="electronics")

        registry = SkillRegistry()
        count = await registry.discover(search_paths=[str(tmp_path)])

        assert count == 2
        assert await registry.get("skill_a") is not None
        assert await registry.get("skill_b") is not None

    async def test_discover_empty_directory(self, tmp_path: Path) -> None:
        """discover() should return 0 when the search path has no skills."""
        empty = tmp_path / "empty"
        empty.mkdir()

        registry = SkillRegistry()
        count = await registry.discover(search_paths=[str(empty)])
        assert count == 0

    async def test_discover_nonexistent_directory(self) -> None:
        """discover() should return 0 and not raise for nonexistent paths."""
        registry = SkillRegistry()
        count = await registry.discover(search_paths=["/nonexistent/path"])
        assert count == 0

    async def test_discover_skips_invalid_skills(self, tmp_path: Path) -> None:
        """discover() should skip skills that fail to load and continue."""
        # Create one valid skill
        _create_test_skill(tmp_path, name="valid_skill", domain="mechanical")

        # Create an invalid skill (no handler.py)
        bad_dir = tmp_path / "electronics" / "skills" / "bad_skill"
        bad_dir.mkdir(parents=True)
        definition = {
            "name": "bad_skill",
            "version": "0.1.0",
            "domain": "electronics",
            "agent": "electronics",
            "description": "A skill that will fail to load",
            "phase": 1,
            "tools_required": [],
            "input_schema": "schema.TestInput",
            "output_schema": "schema.TestOutput",
        }
        (bad_dir / "definition.json").write_text(json.dumps(definition))
        (bad_dir / "schema.py").write_text(
            """\
from pydantic import BaseModel

class TestInput(BaseModel):
    value: str

class TestOutput(BaseModel):
    result: str
"""
        )
        # No handler.py -- this will fail

        registry = SkillRegistry()
        count = await registry.discover(search_paths=[str(tmp_path)])

        # Only the valid skill should be registered
        assert count == 1
        assert await registry.get("valid_skill") is not None
        assert await registry.get("bad_skill") is None


# ---------------------------------------------------------------------------
# TestRegistryHealth
# ---------------------------------------------------------------------------


class TestRegistryHealth:
    """Tests for the health report."""

    async def test_health_report(self, tmp_path: Path) -> None:
        """health() should report correct counts by status and domain."""
        _create_test_skill(tmp_path, name="skill_one", domain="mechanical")
        _create_test_skill(tmp_path, name="skill_two", domain="electronics")
        _create_test_skill(tmp_path, name="skill_three", domain="mechanical")

        registry = SkillRegistry()
        await registry.register(str(tmp_path / "mechanical" / "skills" / "skill_one"))
        await registry.register(
            str(tmp_path / "electronics" / "skills" / "skill_two")
        )
        await registry.register(
            str(tmp_path / "mechanical" / "skills" / "skill_three")
        )
        await registry.activate("skill_two")

        report = await registry.health()
        assert report["total"] == 3
        assert report["by_status"]["REGISTERED"] == 2
        assert report["by_status"]["ACTIVE"] == 1
        assert report["by_domain"]["mechanical"] == 2
        assert report["by_domain"]["electronics"] == 1

    async def test_health_empty_registry(self) -> None:
        """health() should return zeroes for an empty registry."""
        registry = SkillRegistry()
        report = await registry.health()
        assert report["total"] == 0
        assert report["by_status"] == {}
        assert report["by_domain"] == {}
