"""The plugin's playbooks in the dashboard chat (api_gateway/chat/playbooks.py)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api_gateway.chat.harness_backend import build_system_prompt
from api_gateway.chat.playbooks import (
    CLIENT_ONLY_WORKFLOWS,
    MAX_BODY_CHARS,
    WORKING_RULES,
    load_playbooks,
    make_read_playbook_tool,
    playbook_index,
)
from mcp_core.workflows import WORKFLOWS

REPO = Path(__file__).resolve().parents[2]


def _skill(
    root: Path, domain: str, name: str, *, plugin: str | None = None, skill: str = "Body"
) -> None:
    d = root / "domain_agents" / domain / "skills" / name
    d.mkdir(parents=True)
    (d / "definition.json").write_text(json.dumps({"name": name, "description": f"{name} desc"}))
    (d / "SKILL.md").write_text(skill)
    if plugin is not None:
        (d / "PLUGIN.md").write_text(plugin)


def test_reads_the_same_sources_as_the_plugin_packages() -> None:
    playbooks = load_playbooks(REPO)
    shipped = {
        p.name for p in (REPO / "integrations" / "claude-code" / "skills").iterdir() if p.is_dir()
    }
    # Every skill the Claude Code package ships is a playbook here too.
    assert shipped <= set(playbooks), shipped - set(playbooks)
    for name in WORKFLOWS:
        assert (f"{name}-workflow" in playbooks) is (name not in CLIENT_ONLY_WORKFLOWS)


def test_prefers_plugin_md_and_its_description(tmp_path: Path) -> None:
    _skill(tmp_path, "mechanical", "a", skill="short")
    _skill(
        tmp_path,
        "mechanical",
        "b",
        plugin="---\nname: b\ndescription: When to use b\n---\nDetailed b",
    )
    (tmp_path / "mcp_core" / "plugin_skills").mkdir(parents=True)
    found = load_playbooks(tmp_path)
    assert found["a"].body == "short"
    assert found["a"].description == "a desc"
    assert found["b"].body == "Detailed b"
    assert found["b"].description == "When to use b"


def test_index_is_one_line_each_and_trims_long_descriptions(tmp_path: Path) -> None:
    _skill(tmp_path, "m", "x")
    found = load_playbooks(tmp_path)
    found["x"] = found["x"].__class__("x", "d" * 500, "b")
    index = playbook_index(found)
    assert "chat.read_playbook" in index
    line = next(row for row in index.splitlines() if row.startswith("- x:"))
    assert len(line) < 180
    assert playbook_index({}) == ""


@pytest.mark.asyncio
async def test_read_playbook_returns_the_procedure_and_suggests_on_a_typo(tmp_path: Path) -> None:
    _skill(tmp_path, "m", "generate_cad", skill="x" * (MAX_BODY_CHARS + 10))
    tool = make_read_playbook_tool(load_playbooks(tmp_path))
    result = await tool.handler({"name": "generate_cad"})
    assert result["truncated"] is True
    assert len(result["procedure"]) == MAX_BODY_CHARS
    with pytest.raises(ValueError, match="generate_cad"):
        await tool.handler({"name": "generate-cad"})


def _runtime(names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(tools=SimpleNamespace(names=lambda: names, all_tools=lambda: []))


def test_system_prompt_carries_rules_and_index_only_with_the_tool() -> None:
    with_tool = build_system_prompt(_runtime(["chat.read_playbook", "chat.render_ui"]))
    assert WORKING_RULES in with_tool
    assert "- generate_cad:" in with_tool
    without = build_system_prompt(_runtime([]))
    assert WORKING_RULES not in without
    assert "chat.read_playbook" not in without
