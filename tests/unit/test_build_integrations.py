"""The generated plugin package, and the invariants behind it (FORGE-382).

Two kinds of test here. The first checks the package the generator writes.
The second checks the repo it reads from — because the generator surfaced a
skill whose ``definition.json`` was an empty object, which made it invisible
to every domain-scoped selection while looking perfectly fine on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcp_core.workflows import WORKFLOWS
from scripts.build_integrations import (
    DEFAULT_GATEWAY_URL,
    PLUGIN_SKILLS,
    build_claude_code,
    build_claude_code_local,
    build_codex,
    plugin_manifest,
    split_frontmatter,
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
        # The gateway is a substitution, never a literal -- that is the point
        # of the test. FORGE-410 appended the profile, also a substitution, so
        # this asserts both are placeholders rather than freezing the string.
        assert "${user_config.gateway_url}" in server["url"]
        assert "${user_config.tool_profile}" in server["url"]
        assert "localhost" not in server["url"]
        assert "localhost" not in json.dumps(server)

    def test_the_default_can_point_anywhere(self) -> None:
        manifest = plugin_manifest(default_gateway_url="https://mcp.example.com/mcp")
        assert manifest["userConfig"]["gateway_url"]["default"] == "https://mcp.example.com/mcp"
        # ...and still not into the server config.
        assert manifest["mcpServers"]["metaforge"]["url"].startswith("${user_config.gateway_url}")

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


class TestPluginSkills:
    """Skills written for MCP clients rather than for the harness (FORGE-533)."""

    @pytest.fixture(scope="class")
    def packages(self) -> list[Path]:
        return [
            build_claude_code(default_gateway_url=DEFAULT_GATEWAY_URL),
            build_claude_code_local(),
            build_codex(default_gateway_url=DEFAULT_GATEWAY_URL),
        ]

    def test_the_lifecycle_skill_exists(self) -> None:
        assert (PLUGIN_SKILLS / "intent-to-verified-design" / "SKILL.md").is_file()

    def test_every_plugin_skill_ships_in_every_package(self, packages: list[Path]) -> None:
        for source in PLUGIN_SKILLS.glob("*/SKILL.md"):
            for root in packages:
                shipped = root / "skills" / source.parent.name / "SKILL.md"
                assert shipped.read_bytes() == source.read_bytes(), shipped

    def test_a_plugin_skill_ships_its_whole_folder_and_its_links_resolve(
        self, packages: list[Path]
    ) -> None:
        # workflow-lifecycle links its contract under references/; a skill
        # whose link points nowhere is a broken skill.
        for source_dir in (p.parent for p in PLUGIN_SKILLS.glob("*/SKILL.md")):
            files = [
                f for f in source_dir.rglob("*") if f.is_file() and "__pycache__" not in f.parts
            ]
            for root in packages:
                target = root / "skills" / source_dir.name
                for f in files:
                    assert (target / f.relative_to(source_dir)).read_bytes() == f.read_bytes()
                text = (target / "SKILL.md").read_text(encoding="utf-8")
                for link in re.findall(r"\]\((?!https?:)([^)#]+)(?:#[^)]*)?\)", text):
                    assert (target / link).is_file(), f"{root.name}/{source_dir.name}: {link}"

    def test_plugin_skill_names_match_their_folders(self) -> None:
        # A harness loads a skill by its frontmatter name; docs and the folder
        # use the folder name. Disagreeing means the skill is unreachable by
        # the name everything else uses.
        for source in PLUGIN_SKILLS.glob("*/SKILL.md"):
            front, _ = split_frontmatter(source.read_text(encoding="utf-8"))
            assert front.get("name") == source.parent.name, source
            assert "Use when" in front.get("description", ""), (
                f"{source}: the description is what a client matches on, so it "
                "must say when to use the skill"
            )

    def test_plugin_md_ships_instead_of_skill_md(self, packages: list[Path]) -> None:
        for plugin_md in REPO.glob("domain_agents/*/skills/*/PLUGIN.md"):
            _, body = split_frontmatter(plugin_md.read_text(encoding="utf-8"))
            for root in packages:
                shipped = (root / "skills" / plugin_md.parent.name / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                assert body.strip() in shipped, f"{root.name}: {plugin_md.parent.name}"

    def test_plugin_md_does_not_reach_the_harness(self) -> None:
        # The harness budget is why PLUGIN.md exists. If load_skill_cards ever
        # started reading it, mechanical's procedures would be trimmed again.
        from skill_registry.skill_context import load_skill_cards

        cards = {c.name: c for c in load_skill_cards([str(REPO / "domain_agents")])}
        for plugin_md in REPO.glob("domain_agents/*/skills/*/PLUGIN.md"):
            source = (plugin_md.parent / "SKILL.md").read_text(encoding="utf-8").strip()
            assert cards[plugin_md.parent.name].skill_md == source

    def test_every_tool_a_shipped_skill_names_is_registered(self, packages: list[Path]) -> None:
        # The bug this exists for: skills told clients to call tools that no
        # adapter registers, and a client reads a skill as the truth.
        registered = _registered_tool_ids()
        # Namespaces the registry has, plus every one a skill definition
        # claims as a tool: `spice.run_simulation` belongs to a namespace
        # nothing registers (the spice adapter is empty), and checking only
        # registered namespaces let it through. Dotted field paths such as
        # `field.file` have no tool namespace and are left alone.
        namespaces = {t.split(".", 1)[0] for t in registered}
        for definition in REPO.glob("domain_agents/*/skills/*/definition.json"):
            for tool in (
                json.loads(definition.read_text(encoding="utf-8")).get("tools_required") or []
            ):
                if "." in str(tool.get("tool_id") or ""):
                    namespaces.add(str(tool["tool_id"]).split(".", 1)[0])
        unknown: set[str] = set()
        for root in packages:
            for skill in (root / "skills").glob("*/SKILL.md"):
                text = skill.read_text(encoding="utf-8")
                for ref in re.findall(r"`([a-z_]+\.[a-z_]+)`", text):
                    if ref.split(".", 1)[0] in namespaces and ref not in registered:
                        unknown.add(f"{skill.parent.name}: {ref}")
        assert sorted(unknown) == []


def _registered_tool_ids() -> set[str]:
    """Every tool id this repo can register, the way test_mcp_tool_annotations
    builds it: a source scan, the distributor ids an adapter assembles, and
    the bootstrapped registry for ids registered in a loop (freecad.*)."""
    import asyncio

    from tool_registry.bootstrap import bootstrap_tool_registry
    from tool_registry.tools.distributors.mcp_adapter import distributor_tool_ids

    ids: set[str] = set()
    for root in ("tool_registry", "metaforge"):
        for path in (REPO / root).rglob("*.py"):
            ids.update(
                re.findall(
                    r'tool_id="([a-z0-9_.]+)"', path.read_text(encoding="utf-8", errors="replace")
                )
            )
    ids |= distributor_tool_ids()
    ids |= {m.tool_id for m in asyncio.run(bootstrap_tool_registry()).list_tools()}
    return ids


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
        plugin_only = {p.parent.name for p in PLUGIN_SKILLS.glob("*/SKILL.md")}
        workflows = {f"{name}-workflow" for name in WORKFLOWS}  # FORGE-539: no slash commands
        bundled = {p.name for p in (root / "skills").iterdir() if p.is_dir()}
        assert bundled == defined | plugin_only | workflows

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
        # FORGE-410: stdio has no URL to carry `?profile=`, so the cap is a
        # flag. The transport is still what this test is about.
        assert servers["metaforge"]["args"][:2] == ["--transport", "stdio"]
        assert servers["metaforge"]["args"][-2:] == ["--profile", "core"]
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

    def test_the_readme_says_how_the_claim_was_checked(self) -> None:
        """It has now been loaded end to end (FORGE-383), so the README says
        so -- but a bare "verified" is the claim with nothing behind it that
        this file exists to prevent. It has to name the version it was
        checked against and the calls that did the checking, so a reader who
        doubts it can repeat them rather than take it on faith."""
        readme = (self._pkg() / "README.md").read_text()
        for version in ("0.118.0", "0.159.2"):
            assert version in readme, f"README claims verification without naming {version}"
        for method in ("plugin/list", "plugin/read", "plugin/install"):
            assert method in readme, f"README claims verification without citing {method}"
        # The MCP server is the half a manifest check alone would miss.
        assert "metaforge-mcp" in readme
        # And the agent turn is the half an installation check misses. The
        # write being *held* is the strongest claim on the page, so it is
        # the one that has to carry its own evidence: the error code, and
        # that nothing was created.
        assert "project.list" in readme and "project.create" in readme
        assert "approval_required" in readme
        assert "did not run" in readme


class TestLifecycleSurface:
    """Hooks, agents and Codex workflow skills (FORGE-539)."""

    def test_claude_code_packages_ship_hooks_and_agents(self) -> None:
        for root in (
            build_claude_code(default_gateway_url=DEFAULT_GATEWAY_URL),
            build_claude_code_local(),
        ):
            config = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            assert set(config["hooks"]) == {"SessionStart", "PostToolUse"}
            for groups in config["hooks"].values():
                for group in groups:
                    for hook in group["hooks"]:
                        assert "${CLAUDE_PLUGIN_ROOT}/hooks/metaforge_hook.py" in hook["command"]
            assert (root / "hooks" / "metaforge_hook.py").is_file()
            agents = {p.stem for p in (root / "agents").glob("*.md")}
            assert agents == {"metaforge-flow-planner", "metaforge-run-verifier"}
            manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
            assert "hooks" not in manifest, "the standard location loads by itself"

    def test_every_agent_says_when_to_use_it(self) -> None:
        for source in (REPO / "mcp_core" / "plugin_agents").glob("*.md"):
            front, body = split_frontmatter(source.read_text(encoding="utf-8"))
            assert front.get("name") == source.stem
            assert "Use when" in front.get("description", "")
            assert "never" in body.lower() or "do not" in body.lower()

    def test_codex_gets_every_workflow_as_a_skill(self) -> None:
        root = build_codex(default_gateway_url=DEFAULT_GATEWAY_URL)
        for name, (_description, body) in WORKFLOWS.items():
            text = (root / "skills" / f"{name}-workflow" / "SKILL.md").read_text(encoding="utf-8")
            front, _ = split_frontmatter(text)
            assert front["name"] == f"{name}-workflow"
            assert "Use when" in front["description"]
            assert body.split("\n", 1)[0] in text


class TestHookScript:
    def _hook(self):  # type: ignore[no-untyped-def]
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "metaforge_hook", REPO / "mcp_core" / "plugin_hooks" / "metaforge_hook.py"
        )
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def test_a_held_proposal_gets_a_reminder(self) -> None:
        hook = self._hook()
        envelope = {
            "tool_id": "flow.propose",
            "status": "success",
            "data": {"status": "proposed", "approval_id": "run_9"},
        }
        message = hook.post_tool_use(
            {
                "tool_name": "mcp__plugin_metaforge_metaforge__flow_propose",
                "tool_response": {"content": [{"type": "text", "text": json.dumps(envelope)}]},
            }
        )
        assert message and "run_9" in message and "do not poll" in message

    def test_a_patch_names_what_reruns(self) -> None:
        hook = self._hook()
        message = hook.post_tool_use(
            {
                "tool_name": "mcp__metaforge__flow_patch",
                "tool_response": {"status": "proposed", "approval_id": "a", "rerun": ["design"]},
            }
        )
        assert message and "re-run: design" in message

    def test_anything_else_adds_nothing(self) -> None:
        hook = self._hook()
        assert hook.post_tool_use({"tool_name": "mcp__metaforge__flow_list"}) is None
        assert (
            hook.post_tool_use(
                {
                    "tool_name": "mcp__metaforge__flow_propose",
                    "tool_response": {"status": "needs_input"},
                }
            )
            is None
        )

    def test_it_never_fails_and_can_be_turned_off(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import io

        hook = self._hook()
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        assert hook.main(["x", "post-tool-use"]) == 0
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        assert hook.main(["x", "session-start"]) == 0
        assert "MetaForge plugin rules" in capsys.readouterr().out
        monkeypatch.setenv("METAFORGE_PLUGIN_HOOKS", "off")
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        assert hook.main(["x", "session-start"]) == 0
        assert capsys.readouterr().out == ""
