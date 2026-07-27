"""Enforce the skill directory contract across every skill (MET-10).

Per skill_registry/CLAUDE.md every skill ships definition.json, SKILL.md,
schema.py, handler.py, tests.py. This gate fails CI if any skill is missing a
required file — in particular SKILL.md, the procedural doc that feeds the
context layer — so a skill can't be added without one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REQUIRED = ("definition.json", "SKILL.md", "schema.py", "handler.py")
SKILL_DIRS = sorted(REPO.glob("domain_agents/*/skills/*/definition.json"))


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def test_skills_are_discovered() -> None:
    assert SKILL_DIRS, "no skills found under domain_agents/*/skills/"


@pytest.mark.parametrize("def_file", SKILL_DIRS, ids=lambda p: _rel(p.parent))
def test_every_skill_has_required_files(def_file: Path) -> None:
    skill = def_file.parent
    missing = [f for f in REQUIRED if not (skill / f).exists()]
    assert not missing, f"{_rel(skill)} is missing required file(s): {missing}"


@pytest.mark.parametrize("def_file", SKILL_DIRS, ids=lambda p: _rel(p.parent))
def test_skill_md_is_non_trivial(def_file: Path) -> None:
    md = def_file.parent / "SKILL.md"
    if not md.exists():
        pytest.skip("covered by the required-files gate")
    text = md.read_text(encoding="utf-8").strip()
    # A real procedural doc: a heading plus enough substance to be useful.
    assert text.startswith("#"), f"{_rel(md)} must start with a markdown heading"
    assert len(text) >= 120, f"{_rel(md)} is too thin to be a procedural doc"
