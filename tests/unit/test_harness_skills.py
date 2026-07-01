"""Unit tests for the SKILL.md loader (MET-547, Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness.skills import (
    SkillNotFoundError,
    SkillParseError,
    SkillRegistry,
    parse_skill,
)

SKILL = """---
name: enclosure-designer
description: Generate a parametric enclosure and validate fit.
tools: [mcp_freecad_generate_enclosure, twin_search]
required_gates: [approval]
model: generator
owner: mechanical
---
# Playbook
1. Read target dimensions from the spec.
2. Generate the enclosure.
"""


def test_parse_full_skill() -> None:
    skill = parse_skill(SKILL, source="SKILL.md")
    assert skill.name == "enclosure-designer"
    assert skill.description.startswith("Generate a parametric")
    assert skill.tools == ("mcp_freecad_generate_enclosure", "twin_search")
    assert skill.required_gates == ("approval",)
    assert skill.model == "generator"
    assert skill.metadata == {"owner": "mechanical"}
    assert skill.body.startswith("# Playbook")
    assert "Generate the enclosure." in skill.body


def test_minimal_skill_defaults() -> None:
    skill = parse_skill("---\nname: bare\n---\nbody")
    assert skill.name == "bare"
    assert skill.description == ""
    assert skill.tools == ()
    assert skill.required_gates == ()
    assert skill.model is None


def test_empty_list_field() -> None:
    skill = parse_skill("---\nname: x\ntools: []\n---\n")
    assert skill.tools == ()


def test_missing_frontmatter_raises() -> None:
    with pytest.raises(SkillParseError, match="must start with"):
        parse_skill("# just markdown, no frontmatter")


def test_unclosed_frontmatter_raises() -> None:
    with pytest.raises(SkillParseError, match="not closed"):
        parse_skill("---\nname: x\nbody without close")


def test_missing_name_raises() -> None:
    with pytest.raises(SkillParseError, match="must define a string 'name'"):
        parse_skill("---\ndescription: no name here\n---\nbody")


def test_malformed_frontmatter_line_raises() -> None:
    with pytest.raises(SkillParseError, match="not 'key: value'"):
        parse_skill("---\nname: x\nthis line has no colon\n---\n")


def test_registry_register_and_get() -> None:
    reg = SkillRegistry()
    reg.register(parse_skill(SKILL))
    assert reg.get("enclosure-designer").model == "generator"
    assert reg.names() == ["enclosure-designer"]
    with pytest.raises(SkillNotFoundError):
        reg.get("nope")


def test_registry_load_dir(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("---\nname: skill-a\n---\nA", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "SKILL.md").write_text("---\nname: skill-b\n---\nB", encoding="utf-8")

    reg = SkillRegistry()
    count = reg.load_dir(tmp_path)
    assert count == 2
    assert reg.names() == ["skill-a", "skill-b"]
    assert [s.name for s in reg.all_skills()] == ["skill-a", "skill-b"]
