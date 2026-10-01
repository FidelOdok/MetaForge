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
            # FORGE-410: stdio has no URL to hang `?profile=` on, so the cap
            # comes from the flag. Same 30-tool set either way; edit or remove
            # this to load everything.
            "args": ["--transport", "stdio", "--profile", "core"],
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
                    "Which tool set to load: core (project, twin reads, decisions), "
                    "mechanical, simulation, electronics or robotics. Each is 25-30 "
                    "tools. Leave as core unless you are working in one discipline; "
                    "every profile includes health.check so /metaforge:doctor always "
                    "works. Clear it to load everything, which some harnesses will "
                    "truncate without saying so."
                ),
                "default": "core",
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
                    "account, no network. Needs `pip install metaforge`."
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
                "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
                "category": "Engineering",
            }
        ],
    }
    (root / "marketplace.json").write_text(json.dumps(marketplace, indent=2) + "\n")

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

    for build in (build_claude_code, build_codex):
        root = build(default_gateway_url=args.gateway_url)
        print(f"wrote {root.relative_to(REPO)}")
    # FORGE-374: takes no gateway URL, deliberately -- it has nothing to
    # point at, and offering the option would imply otherwise.
    local = build_claude_code_local()
    print(f"wrote {local.relative_to(REPO)}")
    catalog = build_marketplace()
    print(f"wrote {catalog.relative_to(REPO)}")
    print(
        "verify: claude plugin validate --strict . integrations/claude-code "
        "integrations/claude-code-local"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
