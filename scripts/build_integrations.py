#!/usr/bin/env python3
"""Generate the per-harness plugin packages from one source (FORGE-382/383).

Claude Code, Codex and opencode want the same things described in different files. The
source of truth is this script plus the repo it reads; the packages under
``integrations/`` are build output, regenerated rather than hand-edited.

The connection target is deliberately **not** decided here. Claude Code's
``userConfig`` prompts the installer for it and substitutes it into the MCP
server config, so one package serves a local gateway, a team gateway or a
hosted one — the person installing picks. Baking a URL in would have forced
a choice this repo has not made, and forced a rebuild to change it.

Run ``python scripts/build_integrations.py`` and commit what changes.
Verify with ``claude plugin validate integrations/claude-code``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_core.workflows import WORKFLOWS  # noqa: E402

# FORGE-576: one definition of the portable manifest shape, shared with the
# archive builder, so an installed plugin and an uploaded bundle cannot
# describe themselves differently.
from scripts.package_plugin_bundle import to_portable_manifest  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "integrations"

#: Where a fresh `docker compose up gateway` puts the MCP endpoint. A default,
#: not a decision — see the module docstring.
DEFAULT_GATEWAY_URL = "http://localhost:8765/mcp"
#: The local-first package installs alongside the gateway one, so it
#: needs its own name -- components are namespaced under it.
LOCAL_PLUGIN_NAME = "metaforge-local"

# FORGE-658, FORGE-660, FORGE-661. The package for a *hosted* gateway.
#
# FORGE-661 is an experiment. FORGE-660 removed the hosted address so nothing of
# ours was in the public repo, but then claude.ai chat ignores the connector (it
# ignores any URL with a `${user_config.*}` reference), and a custom connector at
# the same address showed Connected while the plugin's own row did not recognise
# it. The literal address is back to see whether claude.ai matches a plugin
# connector to a connector on the account by URL. If it does not help, revert to
# the FORGE-660 form (address from user config, no literal).
CLOUD_PLUGIN_NAME = "metaforge-cloud"
#: The bare URL, no query string: the connector claude.ai signed in to had none,
#: and an earlier plugin build with `?profile=all` stayed at "Not added".
#: The hosted staging stack. Production is not serving yet, and the bare
#: ``mcp.metaforge.uk`` name is a tunnel to a self-hosted server, so the cloud
#: plugin connects to staging until a production MCP endpoint is live.
HOSTED_GATEWAY_URL = "https://mcp.staging.metaforge.uk/mcp"

# FORGE-648. Both of these READMEs used to say `pip install metaforge`, and
# `metaforge` on PyPI is an unrelated job-shop scheduling library by someone
# else -- so the documented first step installed a stranger's package and
# left `metaforge-mcp` nowhere on PATH. The distribution is `metaforge-hw`;
# until it is published, installing from the repository is the command that
# actually works, so that is the command the READMEs give.
DISTRIBUTION_NAME = "metaforge-hw"
LOCAL_INSTALL_COMMAND = 'pipx install "git+https://github.com/FidelOdok/MetaForge.git"'


PLUGIN_NAME = "metaforge"

# FORGE-657. The version was a literal "0.1.0" that nothing ever changed. Claude
# Code decides whether to update an installed plugin by comparing this string,
# so skills, PLUGIN.md and tool lists changed for weeks and no installed copy
# saw any of it. The version now lives in a committed lock beside the output,
# next to a digest of the content it was cut for. The generator bumps the patch
# when (and only when) the content digest moves, and a test fails when content
# changed without the bump, so "changed but not versioned" cannot be merged.
#
# It is not the platform release number: that tag is created by CI after merge
# and never committed back, so the source tree cannot carry it.
VERSION_LOCK = OUT / "plugin-version.json"


def read_version_lock(lock: Path = VERSION_LOCK) -> dict:
    return json.loads(lock.read_text(encoding="utf-8"))


VERSION = read_version_lock()["version"]


def content_digest(root: Path = OUT, lock: Path = VERSION_LOCK) -> str:
    """Hash everything the plugins ship, ignoring the version they carry.

    A version field is dropped before hashing, otherwise bumping the version
    would itself change the digest and the bump would never settle.
    """
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == lock:
            continue
        data = path.read_bytes()
        # package.json is the opencode package's npm manifest (plugin add).
        if path.name in ("plugin.json", "package.json"):
            manifest = json.loads(data)
            manifest.pop("version", None)
            data = json.dumps(manifest, sort_keys=True).encode()
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def next_version(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def bump_if_changed(root: Path = OUT, lock: Path = VERSION_LOCK) -> str | None:
    """Record the content digest, bumping the patch if it moved.

    Returns the new version when the lock changed, else None. An empty recorded
    digest adopts the current content at the version already written, which is
    how the lock is first created without inventing a bump.
    """
    recorded = read_version_lock(lock)
    digest = content_digest(root, lock)
    if digest == recorded["content_digest"]:
        return None
    version = (
        recorded["version"] if not recorded["content_digest"] else next_version(recorded["version"])
    )
    lock.write_text(
        json.dumps({"version": version, "content_digest": digest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return version


def stdio_plugin_manifest() -> dict:
    """The local-first package (FORGE-374).

    Same plugin, launched as a subprocess instead of reached over HTTP. It
    needs no gateway running, no URL, no token and no network: the stdio
    server boots on an empty environment and serves the full tool set.

    Why a second package rather than a second server entry in the first
    one: the manifest format has no conditional. Declaring both an ``http``
    and a ``stdio`` server means Claude Code connects to both, so every
    tool appears twice and an HTTP user also spawns a Python process they
    did not ask for. There is no ``enabled`` or ``when`` field to switch
    one off -- so the choice has to be made at install time, by installing
    one package or the other.
    """
    manifest = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)
    manifest["name"] = LOCAL_PLUGIN_NAME
    manifest["displayName"] = "MetaForge (local)"
    manifest["description"] = (
        "MetaForge on your own machine: the same digital-twin tools, run as a "
        "local process with no gateway, no account and no network."
    )
    # Nothing to ask for. A URL or a token here would imply this package can
    # reach a remote gateway, which is the other package's job.
    manifest.pop("userConfig", None)
    manifest["mcpServers"] = {
        "metaforge": {
            "command": "metaforge-mcp",
            # FORGE-410: stdio has no URL to hang `?profile=` on, so the
            # choice comes from the flag. FORGE-588: Claude Code loads tools
            # lazily, so this package asks for every tool; a capped set left
            # a client-mode run without its phase, gate and CAD tools.
            "args": ["--transport", "stdio", "--profile", "all"],
            "env": {
                # Without a collector listening, the OTLP exporter retries on
                # a timer and logs a connection failure every few seconds --
                # on a laptop that is noise on stderr and nothing else. Local
                # instrumentation stays on; only the export is off, and
                # setting this to `true` in the environment turns it back on.
                "METAFORGE_OTEL_EXPORT": "false",
            },
        }
    }
    return manifest


def cloud_plugin_manifest() -> dict:
    """The hosted-gateway package (FORGE-658, FORGE-661).

    A literal connector URL and no ``userConfig`` anywhere in ``mcpServers``, so
    claude.ai chat does not ignore it. No bearer header either; the sign-in is
    OAuth (FORGE-659): claude.ai follows the gateway's 401, and Claude Code
    signs in through ``/mcp``.
    """
    manifest = plugin_manifest(default_gateway_url=DEFAULT_GATEWAY_URL)
    manifest["name"] = CLOUD_PLUGIN_NAME
    manifest["displayName"] = "MetaForge (cloud)"
    manifest["description"] = (
        "MetaForge on a hosted gateway: the same digital-twin tools, skills, "
        "commands, agents and hooks, connected with nothing to enter. Sign in "
        "once when asked."
    )
    manifest.pop("userConfig", None)
    manifest["mcpServers"] = {"metaforge": {"type": "http", "url": HOSTED_GATEWAY_URL}}
    return manifest


def plugin_manifest(*, default_gateway_url: str) -> dict:
    return {
        "$schema": "https://json.schemastore.org/claude-code-plugin.json",
        "name": PLUGIN_NAME,
        "displayName": "MetaForge",
        "version": VERSION,
        "description": (
            "Engineer hardware against a digital twin: requirements, CAD, "
            "simulation and evidence, with writes held for human approval."
        ),
        "author": {"name": "MetaForge", "url": "https://www.metaforge.uk"},
        "homepage": "https://fidelodok.github.io/MetaForge/",
        "repository": "https://github.com/FidelOdok/MetaForge",
        "license": "Apache-2.0",
        "keywords": ["hardware", "cad", "simulation", "digital-twin", "engineering"],
        # The whole point of asking rather than hardcoding: the same package
        # serves a laptop, a team gateway and a hosted one.
        "userConfig": {
            "gateway_url": {
                "type": "string",
                "title": "Gateway URL",
                "description": (
                    "Your MetaForge gateway's MCP endpoint. Leave the default if "
                    "you are running one locally; enter your team or hosted URL "
                    "otherwise."
                ),
                "default": default_gateway_url,
                "required": True,
            },
            # FORGE-659: there is no token field. A configured Authorization
            # header that the server rejects is reported as a failed connection
            # and never falls back to OAuth, so a bearer header here would stop
            # Claude Code ever offering the sign-in. Without one, a gateway that
            # answers 401 is flagged in /mcp and the person signs in there.
            # FORGE-410: the deployment plugins connect to serves every tool it
            # has -- 108 at the last count -- because it runs with no
            # `--profile`. Claude Code copes by loading tools lazily; a harness
            # with a hard cap truncates, and C1's whole point is that a profile
            # is chosen rather than a list silently cut off.
            #
            # Per connection rather than per deployment, so one sidecar serves
            # a capped set here and the dashboard's full set at the same time.
            "tool_profile": {
                "type": "string",
                "title": "Tool profile",
                "description": (
                    "Which tool set to load. all (the default) loads every tool: "
                    "Claude Code defers tools behind its tool search, and a "
                    "client-mode run needs phase, gate, CAD, simulation and "
                    "value-sourcing tools together, which no capped set holds. "
                    "On a harness with a hard tool limit pick a capped set "
                    "instead: core (project, twin reads, decisions, flows), "
                    "client_run (drive a client-mode run and source values; "
                    "reads the machine library), review (read-only: what a gate "
                    "reviewer reads), mechanical (stateless CAD), "
                    "mechanical_product (CAD authored in a FreeCAD session), "
                    "manufacturing (process selection and DFM review; reads the "
                    "machine library), industrial_design (form factor, "
                    "ergonomics and finishes; renders and the appearance "
                    "library), simulation, electronics (checks, sourcing, "
                    "power, firmware), pcb_design (generate the schematic, "
                    "board, routing and 3D model), prototyping (a printable "
                    "part from a prompt, image or supplier file, sliced and "
                    "priced at a print farm) or robotics, "
                    "each 25-40 tools. Registering, discovering or sending a "
                    "job to a machine needs all. Every set includes "
                    "health.check so /metaforge:doctor always works."
                ),
                # FORGE-588: was core, which carried none of phase.*,
                # flow.await_gate or knowledge.extract.
                "default": "all",
            },
        },
        "mcpServers": {
            "metaforge": {
                "type": "http",
                # The profile rides on the URL because a plugin manifest has no
                # way to pass a command-line flag. A gateway_url that already
                # carries a query string makes this a no-op -- the server then
                # sees no profile and serves everything, which is the previous
                # behaviour rather than a failure.
                "url": "${user_config.gateway_url}?profile=${user_config.tool_profile}",
            }
        },
    }


def write_skills(root: Path, *, skills_root: Path | None = None) -> list[str]:
    """Copy the domain skills in, with the frontmatter a harness needs.

    The frontmatter is *generated from* ``definition.json`` rather than added
    to the source ``SKILL.md`` files, which matters more than it looks.
    ``skill_registry.skill_context.load_skill_cards`` reads ``SKILL.md`` as
    raw body text with no frontmatter parsing, and injects it as procedural
    context. Writing ``---\nname: ...`` into those files would put the
    frontmatter into the model's prompt as if it were part of the procedure.

    The metadata is already in ``definition.json``. Two copies would be the
    usual problem; this reads the one that exists.

    FORGE-533: a skill may also carry a ``PLUGIN.md``, the detailed version
    written for an MCP client (when to use it, the exact tool sequence,
    checks, failure handling). When present it ships instead of ``SKILL.md``.
    The harness keeps reading ``SKILL.md``: ``procedural_overlay`` packs every
    card of a discipline into a fixed character budget, and mechanical alone
    already fills most of it, so growing ``SKILL.md`` would make the harness
    drop procedures without saying which. ``PLUGIN.md`` may open with a
    frontmatter block whose ``description`` replaces the definition's, so the
    plugin copy can say *when* to use the skill as well as what it does.

    ``skills_root`` overrides ``<root>/skills`` for a harness that groups them
    (opencode reads ``skills/metaforge/<id>/SKILL.md`` under the same ids).
    """
    skills_root = skills_root or root / "skills"
    written: list[str] = []
    for definition_path in sorted(REPO.glob("domain_agents/*/skills/*/definition.json")):
        try:
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A malformed definition is a real problem, but it is the skill
            # registry's problem to report -- silently shipping the skill
            # without its metadata would be worse than leaving it out.
            print(f"  skipped {definition_path.parent.name}: unreadable definition.json")
            continue

        body_path = definition_path.parent / "SKILL.md"
        if not body_path.exists():
            print(f"  skipped {definition_path.parent.name}: no SKILL.md")
            continue

        plugin_path = definition_path.parent / "PLUGIN.md"
        plugin_front: dict[str, str] = {}
        if plugin_path.exists():
            plugin_front, body = split_frontmatter(plugin_path.read_text(encoding="utf-8"))
        else:
            body = body_path.read_text(encoding="utf-8")

        name = str(definition.get("name") or definition_path.parent.name)
        description = (
            plugin_front.get("description") or str(definition.get("description") or "")
        ).strip()
        if not description:
            print(f"  skipped {name}: definition.json has no description")
            continue

        domain = str(definition.get("domain") or "").strip()

        # FORGE-533: no `tools:` line. It is not a field any harness reads, and
        # copied from tools_required it advertised ids no server serves
        # (spice.run_simulation, distributor_search). The body names the real
        # tools, and a test checks every one of them.
        front = [f"name: {name}", f"description: {_one_line(description)}"]
        if domain:
            front.append(f"domain: {domain}")

        target = skills_root / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            "---\n" + "\n".join(front) + "\n---\n\n" + body.strip() + "\n"
        )
        written.append(name)
    written.extend(write_plugin_skills(skills_root))
    return written


#: Skills that exist only for plugin clients (FORGE-533). They drive the MCP
#: tools across several disciplines, so they have no domain agent, handler
#: or ``definition.json``; each ``SKILL.md`` carries its own frontmatter and
#: ships verbatim.
PLUGIN_SKILLS = REPO / "mcp_core" / "plugin_skills"


def write_plugin_skills(skills_root: Path) -> list[str]:
    written: list[str] = []
    for source in sorted(PLUGIN_SKILLS.glob("*/SKILL.md")):
        front, _body = split_frontmatter(source.read_text(encoding="utf-8"))
        name = front.get("name", "")
        if name != source.parent.name or not front.get("description"):
            # A plugin skill whose name and folder disagree is one a harness
            # loads under a different name than its docs use; refuse it here
            # rather than shipping something that fails on install.
            raise SystemExit(
                f"{source}: frontmatter needs a description and a name matching its folder"
            )
        target = skills_root / name
        if target.exists():
            shutil.rmtree(target)
        # The whole folder: a skill may carry references it links to
        # (workflow-lifecycle's contract), and a dangling link is a broken skill.
        shutil.copytree(source.parent, target, ignore=shutil.ignore_patterns("__pycache__"))
        written.append(name)
    return written


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``(fields, body)`` for a markdown file that may open with ``---``.

    Only flat ``key: value`` lines are read; that is all these files use.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip().strip('"')
    return fields, text[end + 5 :]


def _one_line(text: str) -> str:
    """Collapse to one line; YAML scalars here are deliberately plain."""
    collapsed = " ".join(text.split())
    # A colon-space in a plain scalar ends the key, so quote when present.
    return (
        f'"{collapsed}"'
        if ": " in collapsed or collapsed.startswith(("[", "{", "&", "*"))
        else collapsed
    )


#: FORGE-539: lifecycle hooks and subagents the Claude Code packages ship.
PLUGIN_HOOKS = REPO / "mcp_core" / "plugin_hooks"
PLUGIN_AGENTS = REPO / "mcp_core" / "plugin_agents"


def write_hooks(root: Path) -> list[str]:
    """``hooks/hooks.json`` plus the script it runs (Claude Code only).

    The standard location, not a manifest field: Claude Code loads
    ``hooks/hooks.json`` by itself, and naming it in the manifest as well
    loads it twice.
    """
    target = root / "hooks"
    target.mkdir(parents=True, exist_ok=True)
    events: list[str] = []
    for name in ("hooks.json", "metaforge_hook.py"):
        shutil.copyfile(PLUGIN_HOOKS / name, target / name)
    config = json.loads((PLUGIN_HOOKS / "hooks.json").read_text(encoding="utf-8"))
    events.extend(sorted(config.get("hooks", {})))
    return events


def write_agents(root: Path) -> list[str]:
    """Plugin subagents (Claude Code only): ``agents/<name>.md``."""
    target = root / "agents"
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for source in sorted(PLUGIN_AGENTS.glob("*.md")):
        shutil.copyfile(source, target / source.name)
        written.append(source.stem)
    return written


def write_workflow_skills(skills_root: Path) -> list[str]:
    """Each curated workflow as a skill, for a harness with no slash commands.

    Codex has no command surface, so ``/metaforge:flow`` and the rest were
    simply absent there (FORGE-533 P6). The same instruction text ships as a
    ``<name>-workflow`` skill whose description says when to use it.
    """
    written: list[str] = []
    for name, (description, body) in WORKFLOWS.items():
        skill = f"{name}-workflow"
        target = skills_root / skill
        target.mkdir(parents=True, exist_ok=True)
        when = description[0].lower() + description[1:]
        summary = _one_line(f"{description} in MetaForge. Use when the user asks to {when}.")
        (target / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: {summary}\n---\n\n# {description}\n\n{body}\n"
        )
        written.append(skill)
    return written


def write_commands(root: Path) -> list[str]:
    commands = root / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (description, body) in WORKFLOWS.items():
        path = commands / f"{name}.md"
        path.write_text(f"---\ndescription: {description}\n---\n\n{body}\n")
        written.append(f"/{PLUGIN_NAME}:{name}")
    return written


def build_claude_code(*, default_gateway_url: str) -> Path:
    root = OUT / "claude-code"
    if root.exists():
        shutil.rmtree(root)
    (root / ".claude-plugin").mkdir(parents=True)

    manifest = plugin_manifest(default_gateway_url=default_gateway_url)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    commands = write_commands(root)
    skills = write_skills(root)
    hooks = write_hooks(root)
    agents = write_agents(root)
    print(
        f"  {len(skills)} skill(s), {len(commands)} command(s), "
        f"{len(hooks)} hook event(s), {len(agents)} agent(s)"
    )

    (root / "README.md").write_text(
        "# MetaForge for Claude Code\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "## Install\n\n"
        "```\n/plugin marketplace add FidelOdok/MetaForge\n"
        f"/plugin install {PLUGIN_NAME}\n```\n\n"
        "Claude Code asks for your gateway URL on first enable. The default "
        "suits a gateway running on your own machine; a team or hosted gateway "
        "is the same package with a different URL.\n\n"
        "### Signing in\n\n"
        "There is no token field. When the gateway needs a login it answers 401, "
        "Claude Code flags `metaforge` in `/mcp`, and you sign in there with OAuth "
        "(run `/mcp`, choose `metaforge`, and follow the browser prompt). A gateway "
        "with no auth configured needs nothing.\n\n"
        "The gateway has to offer OAuth: set `METAFORGE_OAUTH_LOGIN_SECRET` on it. "
        "A gateway with only `METAFORGE_MCP_API_KEY` has no sign-in to offer. "
        "Claude Code will not fall back to OAuth after a rejected static header, "
        "so for a key-only gateway skip this plugin's connector and add the server "
        "yourself with a header:\n\n"
        "```\n"
        "claude mcp add --transport http metaforge https://your-gateway.example.com/mcp "
        '--header "Authorization: Bearer $METAFORGE_MCP_API_KEY"\n'
        "```\n\n"
        "### On claude.ai\n\n"
        "claude.ai ignores a connector whose URL contains a `${user_config.*}` "
        "reference, so this package's connector never appears there. Install "
        "this package for the skills and commands, and add a custom connector "
        "with your gateway's address under **Customize \u2192 Connectors** for "
        "the tools. The hosted service writes its address in instead, which is "
        f"why `{CLOUD_PLUGIN_NAME}` exists, listed in this marketplace beside "
        "this package.\n\n"
        "## Commands\n\n" + "\n".join(f"- `{c}`" for c in commands) + "\n\n"
        f"## Skills\n\n{len(skills)} skills are bundled: the engineering skills "
        "MetaForge's agents follow (each shipped from its detailed `PLUGIN.md`), "
        "`intent-to-verified-design` (the lifecycle procedure) and "
        "`workflow-lifecycle` (the reasoning contract).\n\n"
        "## Agents\n\n" + "\n".join(f"- `{a}`" for a in agents) + "\n\n"
        "## Hooks\n\n"
        f"{', '.join(hooks)}: the working rules at session start, and a reminder when a "
        "flow proposal or patch is held for a person. Python 3 on the PATH; "
        "`METAFORGE_PLUGIN_HOOKS=off` disables them.\n\n"
        "## Writes wait for a human\n\n"
        "A tool that writes is held for approval when the gateway sees you as a "
        "remote caller. Held calls appear on the dashboard's Approvals page. A "
        "refusal comes back naming which happened — `rejected`, `timed_out` or "
        "`not_configured` — and none is worth retrying without a person doing "
        "something first.\n"
    )
    return root


def build_marketplace() -> Path:
    """The catalog that makes `/plugin marketplace add` work (FORGE-328).

    Both generated READMEs have told people to run

        /plugin marketplace add FidelOdok/MetaForge

    since the packages existed, and there was no marketplace file, so the
    command failed. A documented install path with nothing behind it is
    worse than no install path: the first thing a new user does is the
    thing that does not work.

    Written at the repo root rather than under ``integrations/`` because
    that is where Claude Code looks -- ``.claude-plugin/marketplace.json``
    at the marketplace root, with every relative plugin source resolving
    from the same root. Putting it anywhere else would need each user to
    declare it in ``extraKnownMarketplaces`` by hand, which is not a
    one-step install.

    Three packages for now. ``metaforge-cloud`` belongs to the hosted
    service rather than to open core -- its whole content is the hosted
    gateway's address written in as a literal -- and FORGE-707 moves it to
    a marketplace of its own in the Cloud repo. Until that marketplace is
    published it stays listed here, because a package built and served
    from nowhere is one nobody can install.
    """
    root = REPO / ".claude-plugin"
    root.mkdir(exist_ok=True)
    catalog = {
        "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
        "name": "metaforge",
        "owner": {"name": "MetaForge", "url": "https://www.metaforge.uk"},
        "description": (
            "Engineer hardware against a digital twin from your harness: "
            "requirements, CAD, simulation and evidence, with writes held "
            "for human approval."
        ),
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": "./integrations/claude-code",
                "description": (
                    "Connects to a MetaForge gateway over HTTP. Choose this for a "
                    "team or hosted gateway, or a local one you already run."
                ),
                "category": "engineering",
                "tags": ["hardware", "cad", "simulation", "digital-twin"],
            },
            {
                "name": LOCAL_PLUGIN_NAME,
                "source": "./integrations/claude-code-local",
                "description": (
                    "Runs MetaForge as a local process over stdio. No gateway, no "
                    "account, no network. Needs the `metaforge-mcp` command on "
                    "your PATH; the package README gives the one line that "
                    "installs it."
                ),
                "category": "engineering",
                "tags": ["hardware", "cad", "simulation", "local-first"],
            },
            {
                "name": CLOUD_PLUGIN_NAME,
                "source": "./integrations/claude-code-cloud",
                "description": (
                    "Connects to the hosted MetaForge gateway, with its address "
                    "written in, so there is nothing to enter. Sign in once when "
                    "asked."
                ),
                "category": "engineering",
                "tags": ["hardware", "cad", "simulation", "hosted"],
            },
        ],
    }
    (root / "marketplace.json").write_text(json.dumps(catalog, indent=2) + "\n")
    return root / "marketplace.json"


def build_claude_code_local() -> Path:
    """The no-gateway package (FORGE-374)."""
    root = OUT / "claude-code-local"
    if root.exists():
        shutil.rmtree(root)
    (root / ".claude-plugin").mkdir(parents=True)

    manifest = stdio_plugin_manifest()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    commands = write_commands(root)
    skills = write_skills(root)
    hooks = write_hooks(root)
    agents = write_agents(root)
    print(
        f"  {len(skills)} skill(s), {len(commands)} command(s), "
        f"{len(hooks)} hook event(s), {len(agents)} agent(s)"
    )

    (root / "README.md").write_text(
        "# MetaForge for Claude Code — local\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "The same plugin as `claude-code/`, run as a local process instead of "
        "reached over HTTP. No gateway to start, no URL, no token, no network.\n\n"
        "## Install\n\n"
        f"```\n{LOCAL_INSTALL_COMMAND}\n"
        "/plugin marketplace add FidelOdok/MetaForge\n"
        f"/plugin install {LOCAL_PLUGIN_NAME}\n```\n\n"
        "Install the package first: the plugin launches `metaforge-mcp` as "
        "a subprocess, so it has to be on your PATH.\n\n"
        f"The distribution is `{DISTRIBUTION_NAME}`, not `metaforge` — that "
        "name on PyPI belongs to an unrelated project. Until it is "
        "published there, install from this repository with the line "
        "above. `pip install` works too; pipx is suggested because it "
        "keeps the CLI in its own environment and still puts "
        "`metaforge-mcp` on your PATH.\n\n"
        "## Which package\n\n"
        "Install **one**. The manifest format has no way to switch a server "
        "entry off, so a package that declared both an HTTP and a stdio "
        "server would connect to both — every tool twice.\n\n"
        "| | `metaforge` | `metaforge-local` |\n"
        "|---|---|---|\n"
        "| Reaches | a gateway over HTTP | a process on this machine |\n"
        "| Needs | a gateway running, and its URL | `metaforge-mcp` on your PATH |\n"
        "| Suits | a team or hosted gateway | working on your own |\n\n"
        "## What runs locally\n\n"
        "Everything the gateway package exposes. The server starts with no "
        "environment set at all and registers its adapters; anything it "
        "cannot reach is named by `/metaforge:doctor` rather than quietly "
        "missing from the tool list.\n\n"
        "Persistent stores (Neo4j, Postgres) are optional — without them the "
        "twin is in-memory and does not survive a restart. That is a real "
        "limit, not a degraded mode to discover later: `health/check` says "
        "what is reachable.\n\n"
        "## Commands\n\n" + "\n".join(f"- `{c}`" for c in commands) + "\n\n"
        f"## Skills\n\n{len(skills)} engineering skills are bundled.\n\n"
        "## Writes wait for a human\n\n"
        "A local stdio session is treated as the engineer at the machine, so "
        "writes run without being held — unless your client supports MCP "
        "elicitation, in which case it asks you inline. Either way the rule "
        "is enforced server-side, not by the client.\n"
    )
    return root


def build_claude_code_cloud() -> Path:
    """The hosted-gateway package (FORGE-658, FORGE-661).

    The full set, like ``claude-code/``: skills, commands, agents and hooks. The
    only difference is the connector, which has the hosted address as a literal.
    """
    root = OUT / "claude-code-cloud"
    if root.exists():
        shutil.rmtree(root)
    (root / ".claude-plugin").mkdir(parents=True)

    manifest = cloud_plugin_manifest()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    commands = write_commands(root)
    skills = write_skills(root)
    hooks = write_hooks(root)
    agents = write_agents(root)
    print(
        f"  {len(skills)} skill(s), {len(commands)} command(s), "
        f"{len(hooks)} hook event(s), {len(agents)} agent(s)"
    )

    (root / "README.md").write_text(
        "# MetaForge for a hosted gateway\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "The same skills, commands, agents and hooks as `claude-code/`, with the "
        f"connector already pointing at the hosted gateway (`{HOSTED_GATEWAY_URL}`). "
        "There is nothing to enter. This is an experiment (FORGE-661): the address "
        "is a literal so claude.ai does not ignore the connector, and so it can "
        "match a connector you added with the same address.\n\n"
        "## Install\n\n"
        "```\n/plugin marketplace add FidelOdok/MetaForge\n"
        f"/plugin install {CLOUD_PLUGIN_NAME}\n```\n\n"
        "It is listed in the open-core marketplace until the hosted service "
        "publishes a marketplace of its own (FORGE-707).\n\n"
        "Run `/mcp`, choose `metaforge`, and sign in with your MetaForge account "
        "(the email and password you use on the dashboard). There is no token "
        "field: the gateway answers 401 and Claude Code runs OAuth. You reach only "
        "your own account's projects.\n\n"
        "### On claude.ai\n\n"
        "Open the plugin's **Connectors** tab. Adding a plugin does not add or sign "
        "in its connector: **Add** it, then **Connect**. If it cannot be added, add "
        "a custom connector under **Customize → Connectors** with the same address, "
        "typed with no punctuation after it. Chat ignores agents and hooks; Cowork "
        "loads agents.\n\n"
        "## Which package\n\n"
        "| | `metaforge-cloud` | `metaforge` | `metaforge-local` |\n"
        "|---|---|---|---|\n"
        "| For | the hosted gateway | any gateway, local by default | no gateway |\n"
        "| Address | written in | you enter it, local default | none |\n\n"
        "Install **one**. The manifest has no way to switch a server entry off, so "
        "two packages would offer every tool twice.\n\n"
        "## Commands\n\n" + "\n".join(f"- `{c}`" for c in commands) + "\n\n"
        f"## Skills\n\n{len(skills)} skills are bundled.\n\n"
        "## Agents\n\n" + "\n".join(f"- `{a}`" for a in agents) + "\n\n"
        "## Hooks\n\n"
        f"{', '.join(hooks)}: the working rules at session start, and a reminder when a "
        "flow proposal or patch is held for a person. Python 3 on the PATH; "
        "`METAFORGE_PLUGIN_HOOKS=off` disables them.\n\n"
        "## Writes wait for a human\n\n"
        "A tool that writes is held for approval, because the hosted gateway sees "
        "you as a remote caller. Held calls appear on the dashboard's Approvals "
        "page.\n"
    )
    return root


# ---------------------------------------------------------------------------
# Codex (FORGE-383)
# ---------------------------------------------------------------------------
#
# FORGE-383. The public docs describe installing Codex plugins but not the
# manifest's filename, location or fields, and the developer guide they link
# 404s -- so this shipped without one for a long time rather than with a
# guess.
#
# The format came from the shipped CLI instead. `codex features list` reports
# `plugins  stable  true`, and the binary embeds its own scaffolding script,
# which is where every name below comes from:
#
#   plugin manifest   <plugin-root>/.codex-plugin/plugin.json
#   marketplace       .agents/plugins/marketplace.json  (cwd- or home-rooted)
#   entry source      {"source": "local", "path": "./plugins/<name>"}
#   install policy    NOT_AVAILABLE | AVAILABLE | INSTALLED_BY_DEFAULT
#   auth policy       ON_INSTALL | ON_USE
#   plugin name       lowercase hyphen-case
#
# Verified against codex-cli 0.118.0. That is a stronger source than the
# docs -- it is what the program reads -- but it is still one version, so the
# generator writes the manifest and a test pins the field names, rather than
# anything here assuming the shape is eternal.


def build_codex(*, default_gateway_url: str) -> Path:
    root = OUT / "codex"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    (root / "config.toml").write_text(
        "# Append to ~/.codex/config.toml, then restart Codex CLI.\n"
        "#\n"
        "# Codex expands ${VAR} from the calling shell's environment. A gateway\n"
        "# started without auth needs no `authorization` line at all -- leave it\n"
        "# out rather than sending an empty bearer, which reads as a malformed\n"
        "# credential instead of as no credential.\n"
        "[[mcp_servers]]\n"
        'name = "metaforge"\n'
        # FORGE-410: the same `?profile=core` as .mcp.json. These are two
        # ways in to one gateway, and whichever the user did not take is the
        # one that breaks when they drift.
        f'url  = "{default_gateway_url}?profile=core"\n'
        '# authorization = "Bearer ${METAFORGE_MCP_API_KEY}"\n'
    )

    manifest = {
        "name": PLUGIN_NAME,
        "version": VERSION,
        "description": (
            "Engineer hardware against a digital twin: requirements, CAD, "
            "simulation and evidence, with writes held for human approval."
        ),
        "author": {"name": "MetaForge", "url": "https://www.metaforge.uk"},
        "homepage": "https://fidelodok.github.io/MetaForge/",
        "repository": "https://github.com/FidelOdok/MetaForge",
        "license": "Apache-2.0",
        "keywords": ["hardware", "cad", "simulation", "digital-twin", "engineering"],
        # Paths, relative to the plugin root. Codex reads skills from a
        # directory and MCP servers from a file.
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "MetaForge",
            "shortDescription": "Hardware engineering against a digital twin",
            "longDescription": (
                "Turn intent into reviewable, manufacturable deliverables: typed "
                "requirements, CAD through a design IR, simulation, component "
                "selection with margins, and evidence pinned to the revision it "
                "came from. Writes are held for a human."
            ),
            "developerName": "MetaForge",
            "category": "Engineering",
            "websiteURL": "https://www.metaforge.uk",
            "defaultPrompt": [
                "Open my arm project and tell me where it stands.",
                "Record the requirements from this brief, with units and how each is verified.",
            ],
        },
    }
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # FORGE-576: a manifest only under ``.codex-plugin/`` gets "missing
    # plugin.json" from codex-cli 0.161.0. The portable layout wants it at the
    # plugin root, so write both -- the docs name the dotted one as the
    # compatibility path for older hosts. The shape comes from
    # ``package_plugin_bundle`` rather than a second copy here, so the
    # installed plugin and the uploaded archive cannot describe themselves
    # differently (FORGE-575).
    (root / "plugin.json").write_text(json.dumps(to_portable_manifest(manifest), indent=2) + "\n")

    # The MCP server the plugin brings with it. Same endpoint as the
    # config.toml block below, which stays for anyone wiring Codex up by
    # hand rather than installing the plugin.
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "metaforge": {
                        # FORGE-410: `?profile=core` caps the served set at
                        # ~30 tools. Codex has no userConfig equivalent, so
                        # this is edited in place; removing the query loads
                        # everything, which a harness with a hard cap will
                        # truncate without saying so.
                        "url": f"{default_gateway_url}?profile=core",
                    }
                }
            },
            indent=2,
        )
        + "\n"
    )

    marketplace = {
        "name": "metaforge",
        "interface": {"displayName": "MetaForge"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                # The plugin *is* the marketplace root; there is no plugins/ subtree.
                "source": {"source": "local", "path": "./"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
                "category": "Engineering",
            }
        ],
    }
    # FORGE-576: this was written to ``<root>/marketplace.json`` with a
    # ``source.path`` of ``./plugins/metaforge`` -- a directory that has never
    # existed here, since the plugin content sits at the marketplace root.
    # codex-cli 0.161.0 answers both mistakes the same way:
    #
    #   marketplace root does not contain a supported manifest
    #
    # It looks for ``.agents/plugins/marketplace.json``, which the comment
    # above this function already recorded and the writer did not follow.
    # ``./`` is accepted for a plugin that *is* the marketplace root, verified
    # by installing one.
    (root / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(marketplace, indent=2) + "\n"
    )

    (root / "AGENTS.md").write_text(
        "# MetaForge\n\n"
        "This project is tracked in a MetaForge digital twin. Read before you "
        "build, and record what you decide.\n\n"
        "## Start here\n\n"
        "Call `project.list`, pick the project, then `session.start` with its "
        "`project_id` so your work is attributed to it. Read "
        "`metaforge://twin/brief/<project_id>` before designing anything — it "
        "lists what already exists, newest first.\n\n"
        "## What the twin expects\n\n"
        "- Give every CAD part a meaningful name. Never `Part_1`.\n"
        "- Record decisions with `twin.record_decision`, including the "
        "alternatives you rejected.\n"
        "- Pin evidence to the revision it came from: record a result with "
        "`twin.record_document` (`document_type: simulation_result`, with "
        "`analysed_geometry_node_id` and its revision), and with "
        "`twin.record_evidence` when that tool is in your list. A result that "
        "outlives its design is stale, not supporting.\n\n"
        # FORGE-533: Codex has no slash commands, so the lifecycle has to be
        # findable from here and from the bundled skill.
        "## Taking a product from intent to a verified design\n\n"
        "Follow the bundled `intent-to-verified-design` skill. In short:\n\n"
        # FORGE-586: values come from a source, recorded with the value.
        "- Never use an engineering value without a source. Find missing "
        "values with the `source-engineering-values` skill (the user, project "
        "documents, standards, past work, components and stock, analysis, "
        "measurement) and record each as a `design_value` with its source; a "
        "default only as a stated assumption for the person to review. Budget, "
        "quantity, machines and look come only from the user. Unknown is a "
        "valid answer; an unsourced guess is not.\n"
        "- Before custom hardware design, check project/library reuse and complete "
        "commercial or open hardware modules. Compare requirements and interfaces; "
        "record why unsuitable modules were rejected before building from parts. "
        "Unknown specs and missing executable models are gaps, not passes.\n"
        "- When a selected library component has models, read its pinned usage and "
        "limitations with library.resolve_component_model before loading it into an "
        "engineering tool. Fetch binaries only for the operation that needs them; "
        "record the lock hash with the resulting evidence.\n"
        "- Only create a project when the user asked for one. A flow can be "
        "proposed without one.\n"
        "- Propose a flow with `flow.propose`, passing `template` and "
        "`operations` together. Never `template` alone: that means 'use it "
        "unchanged'. Then stop: a person approves it, and there is no tool "
        "that lets you.\n\n"
        "## Reading the answers honestly\n\n"
        "- A requirement with status `no_data` has no evidence at all. That is "
        "a gap, not a pass.\n"
        "- A write may be **held for approval**. That is the system working: "
        "tell the user it is waiting in the dashboard rather than retrying. "
        "If an approval times out or is refused, ask the user before trying "
        "again.\n"
        "- A design-run phase reading `unknown` could not be read. Say "
        "unknown, never 'not started'.\n"
        "- If `tools/list` or `resources/list` returns `_meta.unavailableAdapters`, "
        "some capability is missing because a container is down. Say which, "
        "rather than describing what is left as if it were everything. A "
        "shorter tool list can also be the connection's profile: check "
        "`health.check` before concluding anything is missing.\n"
    )

    skills = write_skills(root)
    # FORGE-539: Codex has no slash commands and no plugin hooks, so the
    # workflows ship as skills and the session rules live in AGENTS.md.
    skills += write_workflow_skills(root / "skills")

    (root / "README.md").write_text(
        "# MetaForge for Codex\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "## As a plugin\n\n"
        "Codex reads a marketplace at `.agents/plugins/marketplace.json`, and an\n"
        "entry's `./plugins/<name>` resolves from the directory that *contains*\n"
        "`.agents/`, not from the marketplace file:\n\n"
        "```\n"
        "<root>/.agents/plugins/marketplace.json   <- copy marketplace.json here\n"
        "<root>/plugins/metaforge/                 <- copy this directory here\n"
        "```\n\n"
        "`<root>` is either your project or your home directory. Restart Codex,\n"
        "then `/plugins`.\n\n"
        "## Or wire the MCP server up by hand\n\n"
        "Append `config.toml` to `~/.codex/config.toml` and restart. This needs no\n"
        "plugin and is the path this repo has been running.\n\n"
        "## What is verified\n\n"
        "Loaded and driven end to end against codex-cli **0.118.0 and\n"
        "0.159.2** — the manifest format survived that upgrade unchanged,\n"
        "which is the thing most worth knowing, since the format was read\n"
        "out of the binary rather than from a published spec.\n\n"
        "Installation, checked by driving `codex app-server` over stdio\n"
        "rather than by eyeballing `/plugins`:\n\n"
        "- `plugin/list` finds the marketplace and returns `metaforge@metaforge`\n"
        "  with `marketplaceLoadErrors: []` — the manifest parses, `installPolicy`\n"
        "  and `authPolicy` survive, and every `interface` field lands where the\n"
        "  curated plugins put theirs.\n"
        f'- `plugin/read` resolves all {len(skills)} skills and `mcpServers: ["metaforge"]`.\n'
        '- `plugin/install` succeeds and writes `[plugins."metaforge@metaforge"]`\n'
        "  into `~/.codex/config.toml`.\n"
        "- Codex connects to the MCP server itself: its client logs\n"
        '  `server_info: Implementation { name: "metaforge-mcp" }` at protocol\n'
        "  `2025-06-18`, and `tools/list` returns the MetaForge tools.\n\n"
        "And the agent actually uses them:\n\n"
        "- A read goes through. Codex called `project.list` and got back a\n"
        "  `status: success` envelope.\n"
        "- **A write is held.** Codex called `project.create`; the server\n"
        "  refused with `-32001` / `code: approval_required`,\n"
        "  `outcome: not_configured`, `retryable: false`, naming the caller\n"
        "  as untrusted. A follow-up `project.list` came back empty, so the\n"
        "  write did not run — which is the point. A guardrail that returns\n"
        "  an error *after* doing the write is worse than none, because the\n"
        "  error makes it look like it held.\n\n"
        "That second one is why the guardrails exist at all: the same tool\n"
        "was gated when a person asked in the dashboard and ungated when an\n"
        "external harness asked over MCP. It is gated now, and this is an\n"
        "external harness asking.\n\n"
        "One cosmetic wart: Codex opens `GET /mcp` for a server-initiated SSE\n"
        "stream, the sidecar answers `405` (which the Streamable HTTP spec\n"
        "permits), and Codex logs that as an `ERROR` line before carrying on\n"
        "normally. It is not a failure — ignore it.\n\n"
        "If the plugin ever stops loading, re-check the manifest format against\n"
        "your Codex version first: it is read from the binary, not from a\n"
        "published spec, so a Codex upgrade can move it.\n\n"
        "## Skills\n\n"
        f"{len(skills)} engineering skills are bundled. Codex prefixes a plugin's\n"
        "skills with its name, so they appear as `metaforge:<skill>`.\n"
    )

    return root


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------
#
# Formats taken from opencode v2.2.5 itself and the V2 docs it points at
# (https://opencode.ai/v2/docs/), not from the V1 docs:
#
#   MCP server      opencode.json  mcp.servers.<name> = {type: "remote", url}
#                   (OAuth is on by default for a remote server; sign in with
#                   `opencode mcp auth <name>`)
#   tool names      <server>_<tool>, anything outside [A-Za-z0-9_-] -> "_",
#                   so `flow.propose` is `metaforge_flow_propose`
#   commands        {command,commands}/**/*.md; the path below the folder is
#                   the name, so commands/metaforge/flow.md is /metaforge/flow.
#                   Frontmatter: description, agent, model, subagent. The body
#                   is the template; !`cmd` in it runs a shell command
#   agents          {agent,agents}/**/*.md; frontmatter description, mode
#                   (primary | subagent | all), model, permissions. `name`
#                   and `tools` are V1 fields
#   skills          skills/**/SKILL.md, id from the folder name, natively
#   rules           AGENTS.md, or files listed in `instructions`
#
# The Claude Code PostToolUse reminder becomes one more line of the rules. A
# V2 plugin could append it from `ctx.tool.hook("execute.after")`, but MCP
# tools run inside Code Mode by default, and whether that hook sees the inner
# `flow.propose` call cannot be checked without a signed-in session. A plugin
# whose one job cannot be shown to work is not shipped.

OPENCODE_DIR = REPO / "mcp_core" / "plugin_opencode"
OPENCODE_RULES_FILE = "metaforge-rules.md"
#: Where the rules live in a project install, relative to the project root.
OPENCODE_PROJECT_RULES = f".opencode/{OPENCODE_RULES_FILE}"
#: The opencode version this package was loaded and checked against.
OPENCODE_VERIFIED_VERSION = "2.2.5"

#: Claude Code wording in the shared workflow text, and what it becomes in
#: opencode. Each source string must still exist: the build fails when one
#: has gone, so a reworded workflow is looked at rather than shipped half
#: translated.
OPENCODE_REWRITES: dict[str, list[tuple[str, str]]] = {
    "connect": [
        (
            "put the URL in the plugin's `gateway_url` setting",
            "put the URL in the `url` of the `metaforge` server under `mcp.servers` in "
            "their opencode.json (`opencode mcp add metaforge --url <url>` writes it)",
        ),
        (
            "or install the `metaforge-local` package, which runs MetaForge as a local "
            "process and needs no gateway at all",
            "or run MetaForge as a local process with no gateway at all: a `local` "
            'server whose `command` is `["metaforge-mcp", "--transport", "stdio", '
            '"--profile", "all"]`',
        ),
    ],
    "drive": [
        ("`/metaforge:flow`", "`/metaforge/flow`"),
        ("`/metaforge:approvals`", "`/metaforge/approvals`"),
        (
            "To keep reporting, the person can run `/loop 10m /metaforge:drive <run id>`.",
            "To report again later, the person can run `/metaforge/drive <run id>` again.",
        ),
    ],
    "doctor": [
        (
            "a plugin plus a user-scope server entry",
            "this package's `metaforge` server plus a second entry under another "
            "name in some other opencode.json",
        ),
    ],
}


def _yaml_scalar(text: str) -> str:
    """One line, double-quoted when a plain YAML scalar would misread it."""
    collapsed = " ".join(text.split())
    if ": " in collapsed or " #" in collapsed or collapsed[:1] in "[{&*!|>'\"%@`":
        return json.dumps(collapsed)
    return collapsed


def opencode_command_body(name: str, body: str) -> str:
    for old, new in OPENCODE_REWRITES.get(name, []):
        if old not in body:
            raise SystemExit(f"opencode rewrite for /{name} no longer matches: {old!r}")
        body = body.replace(old, new)
    # `!` followed by a backtick is a shell command opencode runs before the
    # prompt is sent, outside the permission flow. Nothing here should be one.
    if "!`" in body:
        raise SystemExit(f"/{name} would run a shell command in opencode")
    return body


def load_session_rules() -> str:
    """The SessionStart hook's rules, read from the hook script itself."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "metaforge_hook", PLUGIN_HOOKS / "metaforge_hook.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load mcp_core/plugin_hooks/metaforge_hook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.SESSION_RULES)


def opencode_rules() -> str:
    rules = load_session_rules().replace("/metaforge:approvals", "/metaforge/approvals")
    bullets, _, skills_line = rules.rpartition("\n")
    return (
        "# MetaForge working rules\n\n"
        "From the MetaForge package for opencode: the plugin attaches it to each "
        "new session, and an install.sh install lists it under `instructions` in "
        "opencode.json. Generated by "
        "`scripts/build_integrations.py`; do not edit by hand.\n\n"
        f"{bullets}\n"
        # The PostToolUse reminder (see the note above OPENCODE_DIR).
        "- When `flow.propose` or `flow.patch` comes back with status `proposed`, "
        "the approval it names is held for a person. Nothing runs or changes until "
        "they answer it. Report the approval id and stop; do not poll, and do not "
        "look for a way to approve it.\n"
        f"{skills_line}\n\n"
        "## Tool names in opencode\n\n"
        "Skills, commands and agents name MetaForge tools by their id, such as "
        "`flow.propose` or `twin.get_node`. opencode names the `metaforge` server's "
        "tools `metaforge_<id>` with the dot turned into an underscore: "
        "`metaforge_flow_propose`, `metaforge_twin_get_node`. Under Code Mode they "
        "are `tools.metaforge.flow_propose(...)`. Read a resource such as "
        "`metaforge://twin/brief/<project_id>` with `read_mcp_resource` on the "
        "`metaforge` server.\n\n"
        "## Writes wait for a human\n\n"
        "The gateway sees opencode as a remote caller, so a tool that writes is "
        "held for approval and appears on the dashboard's Approvals page. Tell the "
        "person it is waiting there rather than retrying. A refusal names what "
        "happened (`rejected`, `timed_out` or `not_configured`); none is worth "
        "retrying until a person has done something.\n"
    )


#: The npm name of the server plugin `opencode plugin add` installs.
OPENCODE_PACKAGE_NAME = "metaforge-opencode"
OPENCODE_PLUGIN_ADD = (
    "opencode plugin add 'github:FidelOdok/MetaForge#main::path:integrations/opencode'"
)


def write_opencode_plugin(root: Path, server: dict) -> None:
    """The V2 server plugin, so `opencode plugin add` installs the package.

    ``server.js`` is plain JavaScript with no dependencies and no build step.
    It reads ``metaforge-index.json``, written here from the files already in
    the package, so the descriptions are parsed once, in Python, where the
    tests can check them.
    """
    package = {
        "name": OPENCODE_PACKAGE_NAME,
        "version": VERSION,
        "description": (
            "MetaForge for opencode: the hosted gateway as an MCP server, with "
            "commands, skills, subagents and working rules."
        ),
        "type": "module",
        "exports": {".": "./server.js"},
        "license": "Apache-2.0",
        "homepage": "https://fidelodok.github.io/MetaForge/",
        "repository": {
            "type": "git",
            "url": "git+https://github.com/FidelOdok/MetaForge.git",
            "directory": "integrations/opencode",
        },
        "keywords": ["opencode-plugin", "hardware", "cad", "digital-twin"],
    }
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    shutil.copyfile(OPENCODE_DIR / "server.js", root / "server.js")

    commands = []
    for path in sorted((root / "commands" / PLUGIN_NAME).glob("*.md")):
        front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        commands.append(
            {
                "name": f"{PLUGIN_NAME}/{path.stem}",
                "description": WORKFLOWS[path.stem][0],
                "file": path.relative_to(root).as_posix(),
            }
        )
    agents = []
    for path in sorted((root / "agents").glob("*.md")):
        front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        description = front["description"]
        if description.startswith('"'):
            description = json.loads(description)
        agents.append(
            {"id": path.stem, "description": description, "file": path.relative_to(root).as_posix()}
        )
    skills = []
    for path in sorted((root / "skills" / PLUGIN_NAME).glob("*/SKILL.md")):
        front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        skills.append(
            {
                "id": path.parent.name,
                "name": front.get("name") or path.parent.name,
                "description": front.get("description", ""),
                "file": path.relative_to(root).as_posix(),
            }
        )
    index = {
        "mcp": {"metaforge": server},
        "commands": commands,
        "agents": agents,
        "skills": skills,
        "rules": OPENCODE_RULES_FILE,
    }
    (root / "metaforge-index.json").write_text(json.dumps(index, indent=2) + "\n")


def build_opencode() -> Path:
    """The package for opencode: config, commands, agents, skills and rules.

    It points at the hosted gateway, like ``claude-code-cloud``, and signs in
    with OAuth. There is no header and no key: an API key bypasses the
    per-account scope the sign-in gives.
    """
    root = OUT / "opencode"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {"servers": {"metaforge": {"type": "remote", "url": HOSTED_GATEWAY_URL}}},
        "instructions": [OPENCODE_PROJECT_RULES],
    }
    (root / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")

    # opencode reads no manifest from this package. This one carries the
    # version (content_digest strips it from any plugin.json) and says what
    # the folder is.
    manifest = {
        "name": PLUGIN_NAME,
        "version": VERSION,
        "description": (
            "MetaForge for opencode: the hosted gateway as an MCP server, with the "
            "commands, agents, skills and working rules of the Claude Code package."
        ),
        "homepage": "https://fidelodok.github.io/MetaForge/",
        "repository": "https://github.com/FidelOdok/MetaForge",
        "license": "Apache-2.0",
        "opencode": OPENCODE_VERIFIED_VERSION,
        "mcp": {"metaforge": HOSTED_GATEWAY_URL},
    }
    (root / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")

    commands_dir = root / "commands" / PLUGIN_NAME
    commands_dir.mkdir(parents=True)
    commands: list[str] = []
    for name, (description, body) in WORKFLOWS.items():
        (commands_dir / f"{name}.md").write_text(
            f"---\ndescription: {_yaml_scalar(description)}\n---\n\n"
            f"{opencode_command_body(name, body)}\n"
        )
        commands.append(f"/{PLUGIN_NAME}/{name}")

    agents_dir = root / "agents"
    agents_dir.mkdir()
    agents: list[str] = []
    for source in sorted(PLUGIN_AGENTS.glob("*.md")):
        front, body = split_frontmatter(source.read_text(encoding="utf-8"))
        # Only V2 fields. `name` is V1 (the file name is the id), and
        # Claude Code's `model: opus` is not a provider/model opencode knows.
        (agents_dir / source.name).write_text(
            f"---\ndescription: {_yaml_scalar(front['description'])}\nmode: subagent\n---\n\n"
            f"{body.strip()}\n"
        )
        agents.append(source.stem)

    skills = write_skills(root, skills_root=root / "skills" / PLUGIN_NAME)

    (root / OPENCODE_RULES_FILE).write_text(opencode_rules())
    shutil.copyfile(OPENCODE_DIR / "install.sh", root / "install.sh")
    (root / "install.sh").chmod(0o755)
    write_opencode_plugin(root, config["mcp"]["servers"]["metaforge"])

    print(f"  {len(skills)} skill(s), {len(commands)} command(s), {len(agents)} agent(s)")

    (root / "README.md").write_text(
        "# MetaForge for opencode\n\n"
        "**Generated by `scripts/build_integrations.py`. Do not edit by hand.**\n\n"
        "The hosted MetaForge gateway as an MCP server in opencode, with the same "
        "commands, agents and skills as the Claude Code package and its working "
        f"rules. The server is `metaforge`, at `{HOSTED_GATEWAY_URL}`.\n\n"
        "## Install\n\n"
        f"```\n{OPENCODE_PLUGIN_ADD}\n```\n\n"
        "This installs the package as an opencode server plugin and adds it to "
        "your global config (`~/.config/opencode/opencode.json`), so it applies to "
        "every project: `opencode plugin add` has no per-project option. The "
        "plugin adds the `metaforge` server (unless you already configured one "
        "with that name), the commands, the skills, the subagents, and the rules "
        "as an instruction on each new session. `opencode plugin list` shows it; "
        "`opencode plugin update` picks up a newer `main`.\n\n"
        "### Or with install.sh\n\n"
        "For one project only, or to have the files themselves to read and "
        "commit, use the install script instead:\n\n"
        "```\n"
        "git clone --depth 1 https://github.com/FidelOdok/MetaForge.git\n"
        "sh MetaForge/integrations/opencode/install.sh --project /path/to/your/project\n"
        "# or, for every project:\n"
        "sh MetaForge/integrations/opencode/install.sh --global\n"
        "```\n\n"
        "`--project` installs into the project's `.opencode/` folder, `--global` "
        "into `~/.config/opencode/` (or `$XDG_CONFIG_HOME/opencode`). The script "
        "copies the commands, agents, skills and rules, then adds two entries to "
        "the `opencode.json` in that folder, creating it if there is none:\n\n"
        "```json\n"
        + json.dumps(
            {
                "mcp": {"servers": {"metaforge": config["mcp"]["servers"]["metaforge"]}},
                "instructions": [OPENCODE_PROJECT_RULES],
            },
            indent=2,
        )
        + "\n```\n\n"
        "Everything else in the file is kept, and the old file is saved as "
        "`opencode.json.bak`. A `metaforge` server you already have is left as it "
        "is. A file with comments (`opencode.jsonc`) is not rewritten; the script "
        "prints the entries for you to add. A global install writes the rules "
        "file's absolute path instead of the relative one. It needs `python3`.\n\n"
        "By hand: copy `commands/`, `agents/`, `skills/` and "
        f"`{OPENCODE_RULES_FILE}` into `.opencode/`, and add the entries above. "
        "In a project with no `.opencode/opencode.json` yet, copying "
        "`opencode.json` there as well does the same.\n\n"
        "## Sign in\n\n"
        "```\nopencode mcp auth metaforge\n```\n\n"
        "Sign in with your MetaForge email and password, the ones you use on the "
        "dashboard. There is no token or API key to enter: the gateway answers 401 "
        "and opencode runs OAuth, then keeps the credentials outside your config. "
        "You reach only your own account's projects. Inside opencode, `/mcps` does "
        "the same. `opencode mcp logout metaforge` signs out.\n\n"
        "Do not add an `Authorization` header or set `oauth: false`. An API key "
        "skips the per-account scope the sign-in gives you.\n\n"
        "## Writes wait for a human\n\n"
        "A tool that writes is held for approval, because the hosted gateway sees "
        "opencode as a remote caller. Held calls appear on the dashboard's "
        "Approvals page, and `/metaforge/approvals` lists them.\n\n"
        "## Commands\n\n" + "\n".join(f"- `{c}`" for c in commands) + "\n\n"
        "When signed in, the gateway's own MCP prompts also appear, as "
        "`/metaforge:<name>`. They are the same workflows with wording for any "
        "client; the `/metaforge/<name>` commands are written for opencode and "
        "work before you sign in.\n\n"
        "## Agents\n\n"
        "Subagents (`mode: subagent`), launched by the main agent or by asking for "
        "one by name:\n\n" + "\n".join(f"- `{a}`" for a in agents) + "\n\n"
        "## Skills\n\n"
        f"{len(skills)} skills, in `skills/{PLUGIN_NAME}/<id>/SKILL.md`. opencode "
        "reads `SKILL.md` skills natively, takes the id from the folder name, and "
        "lists each to the model by its description; the agent loads one with the "
        "`skill` tool, or you can with `@<id>`.\n\n"
        "## Rules\n\n"
        f"`{OPENCODE_RULES_FILE}` holds the working rules the Claude Code package "
        "adds at session start, loaded through `instructions`. It also says how "
        "opencode names the tools: `flow.propose` is `metaforge_flow_propose`.\n\n"
        "Not carried over as a hook: Claude Code's reminder after a held "
        "`flow.propose` or `flow.patch`. It is a line in the rules instead. An "
        "opencode plugin could add it after the tool returns, but MCP tools run "
        "inside Code Mode by default, and whether the plugin hook sees that call "
        "could not be checked without signing in, so the plugin does not add it.\n\n"
        "## What is verified\n\n"
        f"With opencode **{OPENCODE_VERIFIED_VERSION}** in a scratch home directory, "
        "installed with `opencode plugin add` from this repository:\n\n"
        "- `opencode plugin list` shows `metaforge`, and the server's plugin list "
        "has it `active`.\n"
        "- `mcp list` shows `metaforge  needs authentication`. With a `metaforge` "
        "server already in a project's config, that one is used instead.\n"
        f"- `GET /api/command` lists the {len(commands)} `metaforge/<name>` "
        f"commands, `GET /api/skill` the {len(skills)} skills (paths inside the "
        "installed package), and `GET /api/agent` the three subagents.\n"
        "- A new session (`POST /api/session`) carries the `metaforge-rules` "
        "instruction entry, and running `metaforge/drive` with an argument admits "
        "the command text with the argument after it.\n\n"
        "Installed with `install.sh`, once with `--project` and once with "
        "`--global`:\n\n"
        "- `opencode mcp list` prints `metaforge  needs authentication`: the server "
        "is configured, reached the hosted address and got its 401. On a freshly "
        "started opencode the first call can print `No MCP servers configured` "
        "before the connection starts; run it again.\n"
        "- `opencode debug agents` lists the three agents with `mode: subagent`.\n"
        f"- The server's `GET /api/command` lists all {len(commands)} "
        f"`/metaforge/<name>` commands and `GET /api/skill` all {len(skills)} "
        "skills (through `opencode api get`).\n"
        f"- The log shows `{OPENCODE_RULES_FILE}` loaded from `instructions`, and "
        "no warning about any file in this package.\n\n"
        "Not verified here: signing in and calling a tool, which needs a MetaForge "
        "account. The hosted gateway publishes OAuth metadata with dynamic client "
        "registration, which is the path opencode uses for a server that does not "
        "offer a client metadata document.\n\n"
        "The formats come from that binary and the V2 documentation it links "
        "(https://opencode.ai/v2/docs/). If a later opencode stops loading them, "
        "re-check against your version first.\n"
    )
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-url",
        default=DEFAULT_GATEWAY_URL,
        help=(
            "Default offered to the installer. They can change it; this only "
            "sets what the prompt starts with."
        ),
    )
    args = parser.parse_args()

    def build_all() -> None:
        for build in (build_claude_code, build_codex):
            root = build(default_gateway_url=args.gateway_url)
            print(f"wrote {root.relative_to(REPO)}")
        # FORGE-374: takes no gateway URL, deliberately -- it has nothing to
        # point at, and offering the option would imply otherwise.
        local = build_claude_code_local()
        print(f"wrote {local.relative_to(REPO)}")
        # FORGE-658: takes no gateway URL either -- the hosted address is a constant.
        cloud = build_claude_code_cloud()
        print(f"wrote {cloud.relative_to(REPO)}")
        # The hosted address is a constant here too.
        opencode = build_opencode()
        print(f"wrote {opencode.relative_to(REPO)}")
        catalog = build_marketplace()
        print(f"wrote {catalog.relative_to(REPO)}")

    global VERSION
    build_all()
    if args.gateway_url == DEFAULT_GATEWAY_URL:
        # FORGE-657: version the content. Manifests are written before the
        # digest is taken, and again if it moved, so they carry the new number.
        bumped = bump_if_changed()
        if bumped is not None and bumped != VERSION:
            print(f"plugin version {VERSION} -> {bumped}")
            VERSION = bumped
            build_all()
        else:
            print(f"plugin version {VERSION}")
    else:
        print("custom --gateway-url: version not recorded; this tree is not the committed output")
    print(
        "verify: claude plugin validate --strict . integrations/claude-code "
        "integrations/claude-code-local"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
