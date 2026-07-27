"""Skill → procedural context (the SKILL.md "select" pillar of context engineering).

Reads each skill's ``definition.json`` (metadata: name / domain / description /
tools_required) and ``SKILL.md`` (the procedure) into selectable
:class:`SkillCard`s. The design-flow phase brain uses these to inject the
relevant discipline's *procedures* and *tool scope* into a phase's context —
turning the enforced SKILL.md corpus into procedural memory the reasoning model
actually sees, instead of a generic prompt.

Lightweight on purpose: file reads only, no handler/schema imports (unlike
``SkillLoader``), so it is cheap to call per phase and has no import side
effects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillCard:
    """The context-relevant view of a skill (its frontmatter + procedure)."""

    name: str
    domain: str
    description: str
    tool_ids: tuple[str, ...]
    capabilities: tuple[str, ...]
    tags: tuple[str, ...]
    skill_md: str


def load_skill_cards(search_paths: list[str] | None = None) -> list[SkillCard]:
    """Read every skill's metadata + SKILL.md into cards (sorted by path)."""
    paths = search_paths or ["domain_agents"]
    cards: list[SkillCard] = []
    for base in paths:
        for def_file in sorted(Path(base).glob("*/skills/*/definition.json")):
            try:
                d = json.loads(def_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            md_path = def_file.parent / "SKILL.md"
            md = md_path.read_text(encoding="utf-8").strip() if md_path.exists() else ""
            tools = d.get("tools_required") or []
            cards.append(
                SkillCard(
                    name=d.get("name") or def_file.parent.name,
                    domain=(d.get("domain") or "").lower(),
                    description=d.get("description") or "",
                    tool_ids=tuple(t.get("tool_id", "") for t in tools if t.get("tool_id")),
                    capabilities=tuple(
                        t.get("capability", "") for t in tools if t.get("capability")
                    ),
                    tags=tuple(d.get("tags") or ()),
                    skill_md=md,
                )
            )
    return cards


def cards_for_domains(cards: list[SkillCard], domains: tuple[str, ...]) -> list[SkillCard]:
    """The cards whose skill ``domain`` matches one of ``domains`` (case-insensitive)."""
    wanted = {d.lower() for d in domains}
    return [c for c in cards if c.domain in wanted]


def procedural_overlay(cards: list[SkillCard], *, budget_chars: int = 12000) -> str:
    """Concatenate the cards' SKILL.md into a procedural reference block, trimmed
    to ``budget_chars`` so a phase with many skills can't blow the context."""
    if not cards:
        return ""
    header = (
        "## Reference procedures (from the skill library)\n"
        "These are the vetted how-to procedures for this phase. Prefer these "
        "procedures and the tools they name; follow their inputs/outputs.\n"
    )
    parts = [header]
    used = len(header)
    omitted = 0
    for c in cards:
        if not c.skill_md:
            continue
        block = f"\n### {c.name}\n{c.skill_md}\n"
        if used + len(block) > budget_chars:
            omitted += 1
            continue
        parts.append(block)
        used += len(block)
    if omitted:
        parts.append(f"\n(+{omitted} more skill procedure(s) omitted for context budget)\n")
    return "".join(parts)


def tool_ids_for_domains(cards: list[SkillCard], domains: tuple[str, ...]) -> list[str]:
    """Deduplicated tool ids the selected disciplines' skills declare (tool scope)."""
    ids: list[str] = []
    for c in cards_for_domains(cards, domains):
        for t in c.tool_ids:
            if t and t not in ids:
                ids.append(t)
    return ids
