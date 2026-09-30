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

from mcp_core.workflows import WORKFLOWS
from scripts.build_integrations import (
    DEFAULT_GATEWAY_URL,
    build_claude_code,
    build_codex,
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
        for name in WORKFLOWS:
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


class TestCodexPackage:
    """What is generated is what is verified (FORGE-383)."""

    @pytest.fixture(scope="class")
    def root(self) -> Path:
        return build_codex(default_gateway_url=DEFAULT_GATEWAY_URL)

    def test_the_mcp_block_uses_the_shape_this_repo_documents(self, root: Path) -> None:
        text = (root / "config.toml").read_text(encoding="utf-8")
        assert "[[mcp_servers]]" in text
        assert 'name = "metaforge"' in text
        assert DEFAULT_GATEWAY_URL in text

    def test_the_authorization_line_is_commented_out(self, root: Path) -> None:
        # An empty bearer reads as a malformed credential rather than as no
        # credential, which is a worse failure than omitting the line.
        line = next(
            ln
            for ln in (root / "config.toml").read_text(encoding="utf-8").splitlines()
            if "authorization" in ln
        )
        assert line.lstrip().startswith("#")

    def test_the_same_skills_ship(self, root: Path) -> None:
        defined = {p.parent.name for p in REPO.glob("domain_agents/*/skills/*/definition.json")}
        assert {p.name for p in (root / "skills").iterdir() if p.is_dir()} == defined

    def test_the_manifest_is_sourced_not_guessed(self, root: Path) -> None:
        # This used to assert the manifest's *absence*: Codex plugins
        # existed, the packaging format was not publicly documented, and a
        # manifest shaped like a guess would have looked authoritative and
        # been wrong. The note said "if this starts failing because someone
        # added a real manifest, good -- delete it and say where the format
        # came from."
        #
        # It came from codex-cli 0.118.0: `codex features list` reports
        # `plugins  stable  true`, and the binary embeds the scaffolding
        # script that writes `.codex-plugin/plugin.json`. Still one version,
        # so TestCodexPlugin pins the field names.
        assert (root / ".codex-plugin" / "plugin.json").is_file()
        assert not (root / "plugin.json").exists()  # not at the root

    def test_agents_md_states_what_the_server_will_do(self, root: Path) -> None:
        text = (root / "AGENTS.md").read_text(encoding="utf-8")
        assert "no_data" in text and "gap, not a pass" in text
        assert "held for approval" in text
        assert "unavailableAdapters" in text


# ---------------------------------------------------------------------------
# Local-first: a package that needs no gateway (FORGE-374)
# ---------------------------------------------------------------------------


class TestLocalFirstPackage:
    """I1 asks for a local gateway *plus a stdio connector*.

    The stdio transport worked -- it boots on an empty environment and
    serves the full tool set -- but nothing shipped it. The only package
    was HTTP-only, so an installer had no way to reach the local-first
    path and had to run a sidecar even to work alone.
    """

    def _local(self) -> dict:
        from scripts.build_integrations import stdio_plugin_manifest

        return stdio_plugin_manifest()

    def test_it_launches_a_process_rather_than_calling_a_url(self) -> None:
        servers = self._local()["mcpServers"]
        assert servers["metaforge"]["command"] == "metaforge-mcp"
        assert servers["metaforge"]["args"] == ["--transport", "stdio"]
        assert "url" not in servers["metaforge"]

    def test_the_launch_command_is_a_real_console_script(self) -> None:
        """`python -m metaforge.mcp` depends on the interpreter and working
        directory the harness happens to spawn with. A packaged plugin
        cannot assume either."""
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
        assert scripts["metaforge-mcp"] == "metaforge.mcp.__main__:main"

    def test_it_asks_for_nothing(self) -> None:
        """A URL or a token here would imply this package can reach a
        remote gateway, which is the other package's job."""
        assert "userConfig" not in self._local()

    def test_exactly_one_server_is_declared(self) -> None:
        """The manifest format has no conditional -- no `enabled`, no
        `when`. A package declaring both an http and a stdio server
        connects to both, so every tool appears twice and an HTTP user
        also spawns a Python process. The choice has to be made by
        installing one package or the other, which is why this is a
        second package rather than a second entry."""
        assert len(self._local()["mcpServers"]) == 1

    def test_the_two_packages_can_be_installed_alongside_each_other(self) -> None:
        from scripts.build_integrations import DEFAULT_GATEWAY_URL, plugin_manifest

        gateway = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)
        assert gateway["name"] != self._local()["name"]

    def test_the_exporter_is_not_left_retrying_a_collector_nobody_ran(self) -> None:
        """Measured, not assumed: with export on, a bare stdio boot logged
        5 OTLP connection failures to localhost:4317 before finishing. On a
        laptop that is noise on stderr and nothing else."""
        env = self._local()["mcpServers"]["metaforge"]["env"]
        assert env["METAFORGE_OTEL_EXPORT"] == "false"

    def test_it_carries_the_same_skills_and_commands(self) -> None:
        """Not a stripped-down copy. Working locally is the same work."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "integrations"
        hosted = root / "claude-code"
        local = root / "claude-code-local"
        if not local.exists():  # pragma: no cover - generated artefact
            pytest.skip("integrations/ not generated in this checkout")
        assert sorted(p.name for p in (local / "skills").iterdir()) == sorted(
            p.name for p in (hosted / "skills").iterdir()
        )
        assert sorted(p.name for p in (local / "commands").iterdir()) == sorted(
            p.name for p in (hosted / "commands").iterdir()
        )


# ---------------------------------------------------------------------------
# One-step install (FORGE-328)
# ---------------------------------------------------------------------------


class TestMarketplace:
    """Both READMEs have told people to run

        /plugin marketplace add FidelOdok/MetaForge

    since the packages existed, and there was no marketplace file, so the
    command failed. A documented install path with nothing behind it is
    worse than none: the first thing a new user does is the thing that
    does not work.
    """

    def _catalog(self) -> dict:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / ".claude-plugin" / "marketplace.json"
        if not path.exists():  # pragma: no cover - generated artefact
            pytest.skip("marketplace.json not generated in this checkout")
        return json.loads(path.read_text())

    def test_it_exists_where_claude_code_looks(self) -> None:
        """`.claude-plugin/marketplace.json` at the repo root. Anywhere
        else and each user has to declare it in extraKnownMarketplaces by
        hand, which is not a one-step install."""
        assert self._catalog()["name"] == "metaforge"

    def test_the_required_fields_are_there(self) -> None:
        catalog = self._catalog()
        assert catalog["owner"]["name"]
        assert catalog["plugins"]

    def test_it_lists_both_packages(self) -> None:
        from scripts.build_integrations import LOCAL_PLUGIN_NAME, PLUGIN_NAME

        names = {p["name"] for p in self._catalog()["plugins"]}
        assert names == {PLUGIN_NAME, LOCAL_PLUGIN_NAME}

    def test_each_source_is_a_relative_path_that_exists(self) -> None:
        """A relative source resolves from the marketplace root, and must
        start with './' or it matches no source type at all."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        for entry in self._catalog()["plugins"]:
            source = entry["source"]
            assert source.startswith("./"), f"{entry['name']}: {source}"
            assert ".." not in source
            assert (repo / source).is_dir(), f"{entry['name']} points at nothing"
            assert (repo / source / ".claude-plugin" / "plugin.json").is_file()

    def test_every_entry_describes_itself(self) -> None:
        """Both packages install the same tools; the description is the
        only thing telling someone which one they want."""
        for entry in self._catalog()["plugins"]:
            assert entry.get("description")

    def test_the_readmes_name_a_marketplace_that_exists(self) -> None:
        """The pairing that broke. If either side is renamed without the
        other, the advertised command silently stops working again."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        catalog = self._catalog()
        for package in ("claude-code", "claude-code-local"):
            readme = repo / "integrations" / package / "README.md"
            if not readme.exists():  # pragma: no cover
                continue
            text = readme.read_text()
            assert "/plugin marketplace add" in text
            installed = [p["name"] for p in catalog["plugins"] if f"install {p['name']}" in text]
            assert installed, f"{package} README installs no plugin this marketplace lists"


# ---------------------------------------------------------------------------
# Codex plugin package (FORGE-383)
# ---------------------------------------------------------------------------


class TestCodexPlugin:
    """The format came from codex-cli 0.118.0 itself, not from the docs.

    `codex features list` reports `plugins  stable  true`, and the binary
    embeds the scaffolding script every field name here was taken from.
    That is a stronger source than the published docs -- which describe
    installing plugins but not the manifest -- and still one version, so
    these pin the names rather than assuming they are eternal.

    Not verified end to end: loading it needs a signed-in Codex, and the
    CLI on the machine this was written on has an expired token.
    """

    def _pkg(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "integrations" / "codex"
        if not (root / ".codex-plugin" / "plugin.json").exists():  # pragma: no cover
            pytest.skip("integrations/ not generated in this checkout")
        return root

    def test_the_manifest_is_where_codex_looks(self) -> None:
        manifest = json.loads((self._pkg() / ".codex-plugin" / "plugin.json").read_text())
        assert manifest["name"] == "metaforge"

    def test_the_name_is_lowercase_hyphen_case(self) -> None:
        """Codex normalises to that, and a name it has to rewrite is a name
        that will not match the marketplace entry."""
        import re

        name = json.loads((self._pkg() / ".codex-plugin" / "plugin.json").read_text())["name"]
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), name

    def test_component_paths_point_at_things_that_exist(self) -> None:
        pkg = self._pkg()
        manifest = json.loads((pkg / ".codex-plugin" / "plugin.json").read_text())
        assert (pkg / manifest["skills"]).is_dir()
        assert (pkg / manifest["mcpServers"]).is_file()

    def test_the_marketplace_entry_uses_a_local_source(self) -> None:
        """`{"source": "local", "path": "./plugins/<name>"}` -- the shape the
        scaffolder writes."""
        market = json.loads((self._pkg() / "marketplace.json").read_text())
        entry = market["plugins"][0]
        assert entry["source"] == {"source": "local", "path": "./plugins/metaforge"}

    @pytest.mark.parametrize(
        ("field", "allowed"),
        [
            ("installation", {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}),
            ("authentication", {"ON_INSTALL", "ON_USE"}),
        ],
    )
    def test_the_policies_are_values_codex_accepts(self, field: str, allowed: set) -> None:
        market = json.loads((self._pkg() / "marketplace.json").read_text())
        assert market["plugins"][0]["policy"][field] in allowed

    def test_the_entry_names_the_plugin_the_manifest_declares(self) -> None:
        """A mismatch here installs nothing, and says nothing about why."""
        pkg = self._pkg()
        manifest = json.loads((pkg / ".codex-plugin" / "plugin.json").read_text())
        market = json.loads((pkg / "marketplace.json").read_text())
        assert market["plugins"][0]["name"] == manifest["name"]

    def test_the_mcp_endpoint_matches_the_hand_written_config(self) -> None:
        """Two ways in, one gateway. If they drift, whichever the user did
        not use is the one that breaks."""
        pkg = self._pkg()
        mcp = json.loads((pkg / ".mcp.json").read_text())
        url = mcp["mcpServers"]["metaforge"]["url"]
        assert url in (pkg / "config.toml").read_text()

    def test_the_readme_does_not_claim_it_was_verified(self) -> None:
        """It has not been loaded end to end. Saying otherwise is the kind
        of claim that costs somebody an afternoon."""
        readme = (self._pkg() / "README.md").read_text()
        assert "has not been loaded end to end" in readme
