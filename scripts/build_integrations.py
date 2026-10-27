#!/usr/bin/env python3
"""Generate the per-harness plugin packages from one source (FORGE-382/383).

Claude Code and Codex want the same things described in different files. The
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
HOSTED_GATEWAY_URL = "https://mcp.metaforge.uk/mcp"

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
        if path.name == "plugin.json":
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
                    "reads the machine library), mechanical (stateless CAD), "
                    "mechanical_product (CAD authored in a FreeCAD session), "
                    "manufacturing (process selection and DFM review; reads the "
                    "machine library), industrial_design (form factor, "
                    "ergonomics and finishes; renders and the appearance "
                    "library), simulation, electronics (checks, sourcing, "
                    "power, firmware), pcb_design (generate the schematic, "
                    "board, routing and 3D model) or robotics, "
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


def write_skills(root: Path) -> list[str]:
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
    """
    skills_root = root / "skills"
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
        f"why `{CLOUD_PLUGIN_NAME}` exists; it is not in this marketplace, and "
        "metaforge.uk says how to get it.\n\n"
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

    Two packages, not three. ``metaforge-cloud`` is still built by
    :func:`build_claude_code_cloud`, but it belongs to the hosted service
    rather than to open core -- its whole content is the hosted gateway's
    address written in as a literal, and this catalog is what someone
    installing MetaForge itself reads. Listing it here offered the hosted
    product as a third way to install the open-source one.

    It needs a home on metaforge.uk, which the docs now point at. Nothing
    serves it yet: the Cloud repo carries no marketplace of its own, so
    until one exists the package is built here and published nowhere.
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
        "This package is **not** listed in the `FidelOdok/MetaForge` marketplace. "
        "That catalog is open core, and all this package adds to `metaforge` is "
        "the hosted gateway's address; metaforge.uk says how to get it. What "
        "follows is how it is installed once you have it.\n\n"
        "Run `/mcp`, choose `metaforge`, and sign in with the access secret "
        "your administrator gave you. There is no token field: the gateway answers "
        "401 and Claude Code runs OAuth. The gateway needs "
        "`METAFORGE_OAUTH_LOGIN_SECRET` set.\n\n"
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
