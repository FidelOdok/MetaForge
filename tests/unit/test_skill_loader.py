"""Tests for skill_registry.loader — dynamic skill loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from skill_registry.loader import LoadedSkill, SkillLoader, SkillLoadError
from skill_registry.schema_validator import SkillDefinition
from skill_registry.skill_base import SkillBase

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_skill_dir(
    base: Path,
    name: str = "test_skill",
    domain: str = "mechanical",
    definition_override: dict[str, object] | None = None,
    schema_content: str | None = None,
    handler_content: str | None = None,
    include_tests: bool = False,
    include_docs: bool = False,
) -> Path:
    """Create a minimal test skill directory structure."""
    skill_dir = base / domain / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    definition: dict[str, object] = {
        "name": name,
        "version": "0.1.0",
        "domain": domain,
        "agent": domain,
        "description": f"Test skill for {name} operations",
        "phase": 1,
        "tools_required": [],
        "input_schema": "schema.TestInput",
        "output_schema": "schema.TestOutput",
    }
    if definition_override:
        definition.update(definition_override)
    (skill_dir / "definition.json").write_text(json.dumps(definition))

    if schema_content is None:
        schema_content = (
            "from pydantic import BaseModel\n"
            "\n"
            "\n"
            "class TestInput(BaseModel):\n"
            "    value: str\n"
            "\n"
            "\n"
            "class TestOutput(BaseModel):\n"
            "    result: str\n"
        )
    (skill_dir / "schema.py").write_text(schema_content)

    if handler_content is None:
        handler_content = (
            "from pydantic import BaseModel\n"
            "from skill_registry.skill_base import SkillBase\n"
            "\n"
            "\n"
            "class TestInput(BaseModel):\n"
            "    value: str\n"
            "\n"
            "\n"
            "class TestOutput(BaseModel):\n"
            "    result: str\n"
            "\n"
            "\n"
            "class TestHandler(SkillBase):\n"
            "    input_type = TestInput\n"
            "    output_type = TestOutput\n"
            "\n"
            "    async def execute(self, input_data):\n"
            '        return TestOutput(result=f"processed: {input_data.value}")\n'
        )
    (skill_dir / "handler.py").write_text(handler_content)

    if include_tests:
        (skill_dir / "tests.py").write_text("# placeholder tests\n")
    if include_docs:
        (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    return skill_dir


# ---------------------------------------------------------------------------
# TestSkillLoader
# ---------------------------------------------------------------------------


class TestSkillLoader:
    def test_load_valid_skill(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(tmp_path)
        loader = SkillLoader()
        result = loader.load(str(skill_dir))

        assert isinstance(result, LoadedSkill)
        assert result.definition.name == "test_skill"
        assert result.definition.version == "0.1.0"
        assert result.definition.domain == "mechanical"
        assert result.skill_path == str(skill_dir)
        assert issubclass(result.input_schema, BaseModel)
        assert issubclass(result.output_schema, BaseModel)
        assert issubclass(result.handler_class, SkillBase)

    def test_load_missing_definition_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "empty_skill"
        skill_dir.mkdir(parents=True)

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "definition.json not found" in exc_info.value.reason
        assert exc_info.value.skill_name == "unknown"

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad_json"
        skill_dir.mkdir(parents=True)
        (skill_dir / "definition.json").write_text("{not valid json!!}")

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "Invalid JSON" in exc_info.value.reason

    def test_load_invalid_definition_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad_def"
        skill_dir.mkdir(parents=True)
        # Missing required fields
        (skill_dir / "definition.json").write_text(
            json.dumps({"name": "ok_name", "version": "1.0.0"})
        )

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "Definition validation failed" in exc_info.value.reason

    def test_load_missing_schema_raises(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(tmp_path, name="no_schema_skill")
        (skill_dir / "schema.py").unlink()

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "schema.py not found" in exc_info.value.reason
        assert exc_info.value.skill_name == "no_schema_skill"

    def test_load_unresolvable_input_schema_raises(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path,
            name="bad_input_ref",
            definition_override={"input_schema": "schema.NonExistent"},
        )

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "Cannot resolve input_schema" in exc_info.value.reason
        assert "NonExistent" in exc_info.value.reason

    def test_load_unresolvable_output_schema_raises(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path,
            name="bad_output_ref",
            definition_override={"output_schema": "schema.NonExistent"},
        )

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "Cannot resolve output_schema" in exc_info.value.reason
        assert "NonExistent" in exc_info.value.reason

    def test_load_missing_handler_raises(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(tmp_path, name="no_handler_skill")
        (skill_dir / "handler.py").unlink()

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "handler.py not found" in exc_info.value.reason
        assert exc_info.value.skill_name == "no_handler_skill"

    def test_load_no_skillbase_subclass_raises(self, tmp_path: Path) -> None:
        handler_no_skill = "from pydantic import BaseModel\n\n\nclass NotASkill:\n    pass\n"
        skill_dir = _create_skill_dir(
            tmp_path, name="no_subclass_skill", handler_content=handler_no_skill
        )

        loader = SkillLoader()
        with pytest.raises(SkillLoadError) as exc_info:
            loader.load(str(skill_dir))

        assert "No SkillBase subclass found" in exc_info.value.reason


# ---------------------------------------------------------------------------
# TestSkillLoaderLoadAll
# ---------------------------------------------------------------------------


class TestSkillLoaderLoadAll:
    def test_load_all_multiple_skills(self, tmp_path: Path) -> None:
        _create_skill_dir(tmp_path, name="skill_alpha", domain="mechanical")
        _create_skill_dir(tmp_path, name="skill_beta", domain="electronics")

        loader = SkillLoader()
        loaded, errors = loader.load_all(search_paths=[str(tmp_path)])

        assert len(loaded) == 2
        assert len(errors) == 0
        names = {s.definition.name for s in loaded}
        assert names == {"skill_alpha", "skill_beta"}

    def test_load_all_fail_soft(self, tmp_path: Path) -> None:
        # One good skill, one broken skill
        _create_skill_dir(tmp_path, name="good_skill", domain="mechanical")

        broken_dir = tmp_path / "electronics" / "skills" / "broken_skill"
        broken_dir.mkdir(parents=True)
        (broken_dir / "definition.json").write_text("{invalid json!!!}")

        loader = SkillLoader()
        loaded, errors = loader.load_all(search_paths=[str(tmp_path)])

        assert len(loaded) == 1
        assert loaded[0].definition.name == "good_skill"
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0].reason

    def test_load_all_empty_directory(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        loader = SkillLoader()
        loaded, errors = loader.load_all(search_paths=[str(empty_dir)])

        assert len(loaded) == 0
        assert len(errors) == 0

    def test_load_all_nonexistent_path(self) -> None:
        loader = SkillLoader()
        loaded, errors = loader.load_all(search_paths=["/nonexistent/path/that/does/not/exist"])

        assert len(loaded) == 0
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# TestSkillDirectoryValidation
# ---------------------------------------------------------------------------


class TestSkillDirectoryValidation:
    def test_validate_complete_directory(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path,
            name="complete_skill",
            include_tests=True,
            include_docs=True,
        )

        loader = SkillLoader()
        issues = loader.validate_skill_directory(str(skill_dir))

        assert issues == []

    def test_validate_missing_files(self, tmp_path: Path) -> None:
        # Create directory with only definition.json and schema.py
        skill_dir = _create_skill_dir(tmp_path, name="incomplete_skill")
        # tests.py and SKILL.md are not created by default

        loader = SkillLoader()
        issues = loader.validate_skill_directory(str(skill_dir))

        assert len(issues) == 2
        assert any("tests.py" in i for i in issues)
        assert any("SKILL.md" in i for i in issues)

    def test_validate_invalid_definition(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad_def_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "definition.json").write_text(json.dumps({"name": "x"}))
        (skill_dir / "schema.py").write_text("")
        (skill_dir / "handler.py").write_text("")
        (skill_dir / "tests.py").write_text("")
        (skill_dir / "SKILL.md").write_text("")

        loader = SkillLoader()
        issues = loader.validate_skill_directory(str(skill_dir))

        assert len(issues) == 1
        assert "Definition validation error" in issues[0]


# ---------------------------------------------------------------------------
# TestLoadedSkill
# ---------------------------------------------------------------------------


class TestLoadedSkill:
    def test_loaded_skill_holds_references(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(tmp_path, name="ref_skill")

        loader = SkillLoader()
        result = loader.load(str(skill_dir))

        assert result.definition is not None
        assert result.input_schema is not None
        assert result.output_schema is not None
        assert result.handler_class is not None
        assert result.skill_path == str(skill_dir)

    def test_loaded_skill_definition_fields(self, tmp_path: Path) -> None:
        skill_dir = _create_skill_dir(
            tmp_path,
            name="field_skill",
            domain="simulation",
            definition_override={
                "version": "2.3.1",
                "phase": 2,
                "timeout_seconds": 300,
                "retries": 3,
                "idempotent": True,
                "tags": ["stress", "fea"],
            },
        )

        loader = SkillLoader()
        result = loader.load(str(skill_dir))

        defn = result.definition
        assert isinstance(defn, SkillDefinition)
        assert defn.name == "field_skill"
        assert defn.version == "2.3.1"
        assert defn.domain == "simulation"
        assert defn.phase == 2
        assert defn.timeout_seconds == 300
        assert defn.retries == 3
        assert defn.idempotent is True
        assert defn.tags == ["stress", "fea"]
