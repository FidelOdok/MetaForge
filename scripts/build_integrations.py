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
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_core.workflows import WORKFLOWS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "integrations"

#: Where a fresh `docker compose up gateway` puts the MCP endpoint. A default,
#: not a decision — see the module docstring.
DEFAULT_GATEWAY_URL = "http://localhost:8765/mcp"
#: The local-first package installs alongside the gateway one, so it
#: needs its own name -- components are namespaced under it.
LOCAL_PLUGIN_NAME = "metaforge-local"

PLUGIN_NAME = "metaforge"
VERSION = "0.1.0"


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
            "args": ["--transport", "stdio"],
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
            "api_token": {
                "type": "string",
                "title": "API token",
                "description": (
                    "Only needed for a gateway that requires one. A local "
                    "gateway started with no auth configured does not."
                ),
                "sensitive": True,
            },
        },
        "mcpServers": {
            "metaforge": {
                "type": "http",
                "url": "${user_config.gateway_url}",
                "headers": {"Authorization": "Bearer ${user_config.api_token}"},
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

        name = str(definition.get("name") or definition_path.parent.name)
        description = str(definition.get("description") or "").strip()
        if not description:
            print(f"  skipped {name}: definition.json has no description")
            continue

        domain = str(definition.get("domain") or "").strip()
        tools = [
            t.get("tool_id") for t in definition.get("tools_required") or [] if t.get("tool_id")
        ]

        front = [f"name: {name}", f"description: {_one_line(description)}"]
        if tools:
            front.append("tools: [" + ", ".join(sorted(tools)) + "]")
        if domain:
            front.append(f"domain: {domain}")

        target = skills_root / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            "---\n"
            + "\n".join(front)
            + "\n---\n\n"
            + body_path.read_text(encoding="utf-8").strip()
            + "\n"
        )
        written.append(name)
    return written


def _one_line(text: str) -> str:
    """Collapse to one line; YAML scalars here are deliberately plain."""
    collapsed = " ".join(text.split())
    # A colon-space in a plain scalar ends the key, so quote when present.
    return (
        f'"{collapsed}"'
        if ": " in collapsed or collapsed.startswith(("[", "{", "&", "*"))
        else collapsed
    )


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
    print(f"  {len(skills)} skill(s), {len(commands)} command(s)")

    (root / "README.md").write_text(
        "# MetaForge for Claude Code\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "## Install\n\n"
        "```\n/plugin marketplace add FidelOdok/MetaForge\n"
        f"/plugin install {PLUGIN_NAME}\n```\n\n"
        "Claude Code asks for your gateway URL on first enable. The default "
        "suits a gateway running on your own machine; a team or hosted gateway "
        "is the same package with a different URL.\n\n"
        "## Commands\n\n" + "\n".join(f"- `{c}`" for c in commands) + "\n\n"
        f"## Skills\n\n{len(skills)} engineering skills are bundled — the same "
        "procedures the MetaForge agents follow, with their metadata taken "
        "from each skill's `definition.json`.\n\n"
        "## Writes wait for a human\n\n"
        "A tool that writes is held for approval when the gateway sees you as a "
        "remote caller. Held calls appear on the dashboard's Approvals page. A "
        "refusal comes back naming which happened — `rejected`, `timed_out` or "
        "`not_configured` — and none is worth retrying without a person doing "
        "something first.\n"
    )
    return root


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
    print(f"  {len(skills)} skill(s), {len(commands)} command(s)")

    (root / "README.md").write_text(
        "# MetaForge for Claude Code — local\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "The same plugin as `claude-code/`, run as a local process instead of "
        "reached over HTTP. No gateway to start, no URL, no token, no network.\n\n"
        "## Install\n\n"
        "```\npip install metaforge\n"
        "/plugin marketplace add FidelOdok/MetaForge\n"
        f"/plugin install {LOCAL_PLUGIN_NAME}\n```\n\n"
        "`pip install` first: the plugin launches `metaforge-mcp` as a "
        "subprocess, so it has to be on your PATH.\n\n"
        "## Which package\n\n"
        "Install **one**. The manifest format has no way to switch a server "
        "entry off, so a package that declared both an HTTP and a stdio "
        "server would connect to both — every tool twice.\n\n"
        "| | `metaforge` | `metaforge-local` |\n"
        "|---|---|---|\n"
        "| Reaches | a gateway over HTTP | a process on this machine |\n"
        "| Needs | a gateway running, and its URL | `pip install metaforge` |\n"
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


# ---------------------------------------------------------------------------
# Codex (FORGE-383)
# ---------------------------------------------------------------------------
#
# Codex CLI supports plugins -- `/plugins` opens a browser, and a plugin can
# carry skills, MCP servers and hooks. The *packaging* format is another
# matter: the public docs describe installing plugins but not the manifest
# filename, its location or its fields, and the developer guide they point to
# 404s.
#
# So this generates what can be verified rather than a manifest shaped like a
# guess. An invented plugin.json that happens to validate against nothing is
# worse than no plugin.json: it looks authoritative, someone builds on it, and
# the first real format check is a rewrite.
#
# What is here works today: the MCP block for ~/.codex/config.toml (the shape
# docs/integrations/codex.md documents and this repo has running), an
# AGENTS.md, and the same skills. When the manifest format is confirmed, it
# slots in beside them.


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
        f'url  = "{default_gateway_url}"\n'
        '# authorization = "Bearer ${METAFORGE_MCP_API_KEY}"\n'
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
        "- Pin evidence to the revision it came from with "
        "`twin.record_evidence`. A result that outlives its design is stale, "
        "not supporting.\n\n"
        "## Reading the answers honestly\n\n"
        "- A requirement with status `no_data` has no evidence at all. That is "
        "a gap, not a pass.\n"
        "- A write may be **held for approval**. That is the system working: "
        "tell the user it is waiting in the dashboard rather than retrying.\n"
        "- If `tools/list` or `resources/list` returns `_meta.unavailableAdapters`, "
        "some capability is missing because a container is down. Say which, "
        "rather than describing what is left as if it were everything.\n"
    )

    skills = write_skills(root)

    (root / "README.md").write_text(
        "# MetaForge for Codex\n\n"
        "**Generated by `scripts/build_integrations.py` — do not edit by hand.**\n\n"
        "## Install\n\n"
        "1. Append `config.toml` to `~/.codex/config.toml` and set the `url` to "
        "your gateway.\n"
        "2. Copy `AGENTS.md` into your project root.\n"
        "3. Restart Codex CLI.\n\n"
        f"## Skills\n\n{len(skills)} engineering skills are included under "
        "`skills/`, the same procedures the MetaForge agents follow.\n\n"
        "## Why there is no plugin manifest here\n\n"
        "Codex CLI supports plugins, but the packaging format is not publicly "
        "documented — the install flow is described, the manifest is not, and "
        "the developer guide the docs link to returns 404. Rather than ship a "
        "manifest shaped like a guess, this package contains the parts that are "
        "verified and work today. The manifest slots in beside them once the "
        "format is confirmed (FORGE-383).\n"
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

    for build in (build_claude_code, build_codex):
        root = build(default_gateway_url=args.gateway_url)
        print(f"wrote {root.relative_to(REPO)}")
    # FORGE-374: takes no gateway URL, deliberately -- it has nothing to
    # point at, and offering the option would imply otherwise.
    local = build_claude_code_local()
    print(f"wrote {local.relative_to(REPO)}")
    print(
        "verify: claude plugin validate --strict integrations/claude-code "
        "integrations/claude-code-local"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
