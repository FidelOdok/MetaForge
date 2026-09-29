"""The generated plugin package, and the invariants behind it (FORGE-382).

Two kinds of test here. The first checks the package the generator writes.
The second checks the repo it reads from — because the generator surfaced a
skill whose ``definition.json`` was an empty object, which made it invisible
to every domain-scoped selection while looking perfectly fine on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_integrations import (
    DEFAULT_GATEWAY_URL,
    SLASH_COMMANDS,
    build_claude_code,
    plugin_manifest,
)

REPO = Path(__file__).resolve().parents[2]


class TestTheConnectionIsAsked_Not_Assumed:
    """The point of the manifest: one package, any gateway."""

    def test_the_gateway_url_is_a_user_config_field(self) -> None:
        manifest = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)
        field = manifest["userConfig"]["gateway_url"]
        assert field["required"] is True
        assert field["default"] == DEFAULT_GATEWAY_URL

    def test_the_mcp_server_reads_that_field_rather_than_a_literal(self) -> None:
        # A hardcoded URL would make the package a deployment decision and
        # force a rebuild to change it.
        server = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)["mcpServers"]["metaforge"]
        assert server["url"] == "${user_config.gateway_url}"
        assert "localhost" not in json.dumps(server)

    def test_the_default_can_point_anywhere(self) -> None:
        manifest = plugin_manifest(default_gateway_url="https://mcp.example.com/mcp")
        assert manifest["userConfig"]["gateway_url"]["default"] == "https://mcp.example.com/mcp"
        # ...and still not into the server config.
        assert manifest["mcpServers"]["metaforge"]["url"] == "${user_config.gateway_url}"

    def test_the_token_is_marked_sensitive(self) -> None:
        # Otherwise it lands in settings.json in the clear.
        manifest = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)
        assert manifest["userConfig"]["api_token"]["sensitive"] is True


class TestGeneratedPackage:
    @pytest.fixture(scope="class")
    def root(self) -> Path:
        return build_claude_code(default_gateway_url=DEFAULT_GATEWAY_URL)

    def test_the_manifest_lands_where_claude_code_looks(self, root: Path) -> None:
        assert (root / ".claude-plugin" / "plugin.json").is_file()

    def test_every_command_is_written(self, root: Path) -> None:
        for name in SLASH_COMMANDS:
            assert (root / "commands" / f"{name}.md").is_file(), name

    def test_every_skill_with_a_definition_is_bundled(self, root: Path) -> None:
        defined = {p.parent.name for p in REPO.glob("domain_agents/*/skills/*/definition.json")}
        bundled = {p.name for p in (root / "skills").iterdir() if p.is_dir()}
        missing = sorted(defined - bundled)
        assert missing == [], f"skills with a definition that did not ship: {missing}"

    def test_each_bundled_skill_carries_frontmatter(self, root: Path) -> None:
        for skill in sorted((root / "skills").iterdir()):
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            assert text.startswith("---\n"), skill.name
            front = text.split("---", 2)[1]
            assert "name:" in front and "description:" in front, skill.name

    def test_regenerating_changes_nothing(self, root: Path) -> None:
        # Build output committed to the repo has to be reproducible, or every
        # run shows a diff and people stop reading them.
        before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
        rebuilt = build_claude_code(default_gateway_url=DEFAULT_GATEWAY_URL)
        after = {p: p.read_bytes() for p in sorted(rebuilt.rglob("*")) if p.is_file()}
        assert before == after


class TestTheSourceSkillsAreLeftAlone:
    def test_no_domain_skill_md_gained_frontmatter(self) -> None:
        # load_skill_cards reads these files as raw body text and injects them
        # as procedural context. Frontmatter here would land in the model's
        # prompt as if it were part of the procedure.
        offenders = [
            str(p.relative_to(REPO))
            for p in REPO.glob("domain_agents/*/skills/*/SKILL.md")
            if p.read_text(encoding="utf-8").lstrip().startswith("---")
        ]
        assert offenders == [], f"frontmatter leaked into source skills: {offenders}"


class TestEverySkillIsFindable:
    """The invariant the empty definition.json broke."""

    def test_every_skill_declares_a_domain_and_a_description(self) -> None:
        # A skill whose definition.json is {} still loads: load_skill_cards
        # falls back to the directory name and leaves domain and description
        # blank, so cards_for_domains cannot match it and it contributes no
        # tools. It has a handler, tests and a written procedure, and it is
        # excluded from every domain-scoped selection -- silently.
        broken: list[str] = []
        for path in sorted(REPO.glob("domain_agents/*/skills/*/definition.json")):
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                broken.append(f"{path.parent.name}: unparseable")
                continue
            if not str(definition.get("description") or "").strip():
                broken.append(f"{path.parent.name}: no description")
            if not str(definition.get("domain") or "").strip():
                broken.append(f"{path.parent.name}: no domain")
        assert broken == [], "skills invisible to domain scoping: " + ", ".join(broken)

    def test_the_scan_finds_the_skills(self) -> None:
        # Guard against the assertion above passing because the glob broke.
        assert len(list(REPO.glob("domain_agents/*/skills/*/definition.json"))) >= 25
