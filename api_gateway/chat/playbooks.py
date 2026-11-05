"""The plugin's know-how for the dashboard chat: skill guides and workflows.

The Claude Code and Codex packages ship MetaForge's skill guides, its curated
workflows and a set of working rules (``scripts/build_integrations.py``). The
dashboard chat had the same tools but none of that guidance, so it worked out
every multi-step task from scratch. This module reads the **same sources** the
packages are built from, so the two cannot drift:

* each domain skill's ``PLUGIN.md`` (or ``SKILL.md`` when it has none), with
  its description from ``definition.json`` or the ``PLUGIN.md`` frontmatter;
* the plugin-only skills in ``mcp_core/plugin_skills``;
* the curated workflows in ``mcp_core.workflows`` (the plugin's slash
  commands), minus the ones about wiring up a client.

The system prompt lists each playbook in one line; the agent reads the one it
needs with ``chat.read_playbook``, the way Claude Code loads a skill only when
it is relevant. Listing every body inline would cost over 100k characters a
turn.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from mcp_core.workflows import WORKFLOWS
from observability.tracing import get_tracer
from orchestrator.harness import NativeToolDef

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.playbooks")

REPO = Path(__file__).resolve().parents[2]
#: A playbook body handed to the model is capped, so one long guide cannot fill the context.
MAX_BODY_CHARS = 24000
#: Workflows about connecting a client or driving a session from outside,
#: which mean nothing in the dashboard.
CLIENT_ONLY_WORKFLOWS = frozenset({"connect", "use", "drive", "doctor"})

# The plugin's session rules (mcp_core/plugin_hooks/metaforge_hook.py,
# SESSION_RULES), as they apply to a person talking to the agent in the
# dashboard: no slash commands or subagents here, and approvals are buttons.
WORKING_RULES = (
    "Working rules:\n"
    "- Never use an engineering value without a source. Find it (the person, "
    "project documents, standards, past work, components and stock, analysis, "
    "measurement) and record it with its source; use a default only as a stated "
    "assumption for the person to review. Budget, quantity, machines and look "
    "come only from the person; 'unknown' is a valid answer.\n"
    "- Proposals, patches, gates and waivers are approved by a person. No tool "
    "approves; say what is waiting and stop. Never report one as decided.\n"
    "- A design is done only when it is verified (flow.verify_completion says "
    "COMPLETED_VERIFIED). Phases finishing is not done.\n"
    "- 'unknown' is not 'pending', and no_data is a gap, not a pass."
)


@dataclass(frozen=True)
class Playbook:
    name: str
    description: str
    body: str


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``(fields, body)`` for markdown that may open with a flat ``---`` block."""
    text = text.replace("\r\n", "\n")
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


def _domain_skills(root: Path) -> list[Playbook]:
    out: list[Playbook] = []
    for definition_path in sorted(root.glob("domain_agents/*/skills/*/definition.json")):
        try:
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plugin_path = definition_path.parent / "PLUGIN.md"
        skill_path = definition_path.parent / "SKILL.md"
        front: dict[str, str] = {}
        if plugin_path.exists():
            front, body = _frontmatter(plugin_path.read_text(encoding="utf-8"))
        elif skill_path.exists():
            body = skill_path.read_text(encoding="utf-8")
        else:
            continue
        name = str(definition.get("name") or definition_path.parent.name)
        description = " ".join(
            (front.get("description") or str(definition.get("description") or "")).split()
        )
        if description and body.strip():
            out.append(Playbook(name, description, body.strip()))
    return out


def _plugin_skills(root: Path) -> list[Playbook]:
    out: list[Playbook] = []
    for path in sorted((root / "mcp_core" / "plugin_skills").glob("*/SKILL.md")):
        front, body = _frontmatter(path.read_text(encoding="utf-8"))
        if front.get("description") and body.strip():
            out.append(
                Playbook(front.get("name") or path.parent.name, front["description"], body.strip())
            )
    return out


def _workflows() -> list[Playbook]:
    return [
        Playbook(f"{name}-workflow", description, body)
        for name, (description, body) in WORKFLOWS.items()
        if name not in CLIENT_ONLY_WORKFLOWS
    ]


def load_playbooks(root: Path = REPO) -> dict[str, Playbook]:
    """Every playbook by name. A name is unique; the first source wins."""
    found: dict[str, Playbook] = {}
    for playbook in [*_domain_skills(root), *_plugin_skills(root), *_workflows()]:
        found.setdefault(playbook.name, playbook)
    return found


@lru_cache(maxsize=1)
def cached_playbooks() -> dict[str, Playbook]:
    """Process-wide copy: the files only change with a new image."""
    playbooks = load_playbooks()
    logger.info("chat_playbooks_loaded", count=len(playbooks))
    return playbooks


def playbook_index(playbooks: dict[str, Playbook], *, max_description: int = 160) -> str:
    """The system prompt's list: one line per playbook, and when to read one."""
    if not playbooks:
        return ""
    lines = [
        "Playbooks: MetaForge's vetted procedures, the same ones its Claude Code "
        "plugin uses. Before multi-step work (designing a part, running an "
        "analysis, planning a design flow, reviewing a gate, ordering, moving "
        "files), read the matching playbook with chat.read_playbook and follow it. "
        "Do not read one to answer a simple question."
    ]
    for p in sorted(playbooks.values(), key=lambda p: p.name):
        description = p.description
        if len(description) > max_description:
            description = description[: max_description - 1].rstrip() + "…"
        lines.append(f"- {p.name}: {description}")
    return "\n".join(lines)


def make_read_playbook_tool(playbooks: dict[str, Playbook]) -> NativeToolDef:
    """``chat.read_playbook``: the full procedure for one playbook. Read-only."""

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        with tracer.start_as_current_span("chat.read_playbook") as span:
            span.set_attribute("playbook.name", name)
            playbook = playbooks.get(name)
            if playbook is None:
                close = difflib.get_close_matches(name, list(playbooks), n=3, cutoff=0.4)
                hint = f" Did you mean: {', '.join(close)}?" if close else ""
                raise ValueError(f"chat.read_playbook: no playbook named {name!r}.{hint}")
            body = playbook.body
            truncated = len(body) > MAX_BODY_CHARS
            if truncated:
                body = body[:MAX_BODY_CHARS]
            logger.info("chat_playbook_read", playbook=name, chars=len(body), truncated=truncated)
            return {
                "name": playbook.name,
                "description": playbook.description,
                "procedure": body,
                "truncated": truncated,
            }

    return NativeToolDef(
        name="chat.read_playbook",
        description=(
            "Read one of MetaForge's playbooks (listed in the system prompt) before "
            "multi-step work, and follow it. Read-only."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The playbook's name, as listed."}
            },
            "required": ["name"],
        },
        handler=handler,
    )
