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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "integrations"

#: Where a fresh `docker compose up gateway` puts the MCP endpoint. A default,
#: not a decision — see the module docstring.
DEFAULT_GATEWAY_URL = "http://localhost:8765/mcp"

PLUGIN_NAME = "metaforge"
VERSION = "0.1.0"

SLASH_COMMANDS: dict[str, tuple[str, str]] = {
    "use": (
        "Pick the project to work in for this session",
        "Set the active MetaForge project.\n\n"
        "Call `project.list` to show the projects on this gateway, ask which one "
        "if the user has not said, then call `session.start` with that "
        "`project_id` so everything recorded afterwards is attributed to it.\n\n"
        "Then read `metaforge://twin/brief/<project_id>` and summarise where the "
        "project stands — newest work first. Do not restate the whole brief.",
    ),
    "status": (
        "Where this project stands right now",
        "Summarise the active project's state.\n\n"
        "Read `metaforge://twin/brief/<project_id>` and "
        "`metaforge://twin/requirements/<project_id>`.\n\n"
        "Report what is built, and which requirements are unverified. A "
        "requirement with `no_data` has no evidence at all — say so plainly. It "
        "is a gap, not a pass, and it is the thing most worth surfacing.",
    ),
    "design": (
        "Design or revise a part",
        "Design a part in the active project.\n\n"
        "Read the brief first so the part fits what already exists. Author "
        "geometry through the CAD tools, give every part a meaningful name "
        "(never `Part_1`), and commit with `twin.commit_geometry`.\n\n"
        "A write may be held for approval — that is expected, not an error. Tell "
        "the user it is waiting in the dashboard rather than retrying.",
    ),
    "fea": (
        "Run a load case and record the evidence",
        "Run structural analysis on a committed part.\n\n"
        "Stage the geometry with `twin.stage_work_product_file`, set up the load "
        "case, run `calculix.run_fea`, then check convergence with "
        "`calculix.check_mesh_convergence` and cross-check against a hand "
        "calculation where one applies.\n\n"
        "Record the result with `twin.record_evidence`, pinned to the exact "
        "revision it came from. A number with no evidence behind it is not a "
        "result — say what you could not establish rather than rounding it into "
        "a claim.",
    ),
    "gate": (
        "Review a maturity gate",
        "Review whether the active project can be promoted.\n\n"
        "Read `metaforge://twin/requirements/<project_id>` and report each "
        "required claim's status. `uncertain`, `stale` and `no_data` all block; "
        "only an approved waiver naming that requirement overrides a `fail`.\n\n"
        "`twin.attempt_promotion` refuses rather than warns, and it needs a named "
        "human in `decided_by`. Do not supply one on the user's behalf.",
    ),
    "doctor": (
        "Check the connection and what is reachable",
        "Diagnose this MetaForge connection.\n\n"
        "Call `health/check`, then `tools/list` and `resources/list`. Report the "
        "gateway version and auth mode.\n\n"
        "Check `_meta.unavailableAdapters` on both listings — an adapter whose "
        "container is down contributes no tools and no resources, and the list "
        "simply looks shorter. Name any that are missing rather than describing "
        "what is left as if it were everything.",
    ),
}


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


def write_commands(root: Path) -> list[str]:
    commands = root / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (description, body) in SLASH_COMMANDS.items():
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
        "## Writes wait for a human\n\n"
        "A tool that writes is held for approval when the gateway sees you as a "
        "remote caller. Held calls appear on the dashboard's Approvals page. A "
        "refusal comes back naming which happened — `rejected`, `timed_out` or "
        "`not_configured` — and none is worth retrying without a person doing "
        "something first.\n"
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

    root = build_claude_code(default_gateway_url=args.gateway_url)
    print(f"wrote {root.relative_to(REPO)}")
    print("verify: claude plugin validate integrations/claude-code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
