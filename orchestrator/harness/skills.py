"""SKILL.md markdown-playbook loader (MET-547, Phase 3).

A harness skill is a Markdown file with a small frontmatter block::

    ---
    name: enclosure-designer
    description: Generate a parametric enclosure and validate fit.
    tools: [mcp_freecad_generate_enclosure, twin_search]
    required_gates: [approval]
    model: generator
    ---
    # Playbook
    1. Read the target dimensions from the spec...

The frontmatter is a deliberately small subset (scalar ``key: value`` and
inline ``[a, b]`` lists) so the loader needs no YAML dependency; the Markdown
body is the playbook the agent follows. Skills compose existing tools +
instructions without new code paths -- the MET-547 criterion.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_FENCE = "---"
# Fields promoted to typed attributes; everything else lands in metadata.
_LIST_FIELDS = frozenset({"tools", "required_gates"})


class SkillParseError(ValueError):
    """A SKILL.md file was malformed."""


class SkillNotFoundError(KeyError):
    """No registered skill with the given name."""


@dataclass(frozen=True)
class HarnessSkill:
    """A parsed markdown-playbook skill."""

    name: str
    description: str
    body: str
    tools: tuple[str, ...] = ()
    required_gates: tuple[str, ...] = ()
    model: str | None = None
    source: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def _parse_value(raw: str) -> str | list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",") if item.strip()]
    return raw


def parse_skill(text: str, *, source: str | None = None) -> HarnessSkill:
    """Parse SKILL.md content into a :class:`HarnessSkill`."""
    if not text.lstrip().startswith(_FENCE):
        raise SkillParseError("SKILL.md must start with a '---' frontmatter block")

    # Split into frontmatter and body on the second fence line.
    after_open = text.split(_FENCE, 1)[1]
    if _FENCE not in after_open:
        raise SkillParseError("SKILL.md frontmatter is not closed with '---'")
    front_raw, body = after_open.split(_FENCE, 1)

    fields: dict[str, str | list[str]] = {}
    for line_no, line in enumerate(front_raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise SkillParseError(f"frontmatter line {line_no} is not 'key: value': {stripped!r}")
        key, _, value = stripped.partition(":")
        fields[key.strip()] = _parse_value(value)

    name = fields.get("name")
    if not isinstance(name, str) or not name:
        raise SkillParseError("SKILL.md frontmatter must define a string 'name'")
    description = fields.get("description")
    description = description if isinstance(description, str) else ""

    def _as_tuple(key: str) -> tuple[str, ...]:
        value = fields.get(key, [])
        if isinstance(value, str):
            return (value,) if value else ()
        return tuple(value)

    model = fields.get("model")
    metadata = {
        k: v
        for k, v in fields.items()
        if k not in {"name", "description", "model"} | _LIST_FIELDS and isinstance(v, str)
    }

    return HarnessSkill(
        name=name,
        description=description,
        body=body.strip(),
        tools=_as_tuple("tools"),
        required_gates=_as_tuple("required_gates"),
        model=model if isinstance(model, str) else None,
        source=source,
        metadata=metadata,
    )


def load_skill(path: Path) -> HarnessSkill:
    """Read and parse a SKILL.md file."""
    return parse_skill(path.read_text(encoding="utf-8"), source=str(path))


class SkillRegistry:
    """A name-indexed catalog of harness skills."""

    def __init__(self) -> None:
        self._skills: dict[str, HarnessSkill] = {}

    def register(self, skill: HarnessSkill) -> HarnessSkill:
        self._skills[skill.name] = skill
        logger.info("skill_registered", skill=skill.name, tools=len(skill.tools))
        return skill

    def get(self, name: str) -> HarnessSkill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillNotFoundError(name) from exc

    def names(self) -> list[str]:
        return sorted(self._skills)

    def all_skills(self) -> list[HarnessSkill]:
        return [self._skills[n] for n in self.names()]

    def load_dir(self, root: Path, *, pattern: str = "**/SKILL.md") -> int:
        """Discover and register every SKILL.md under ``root``. Returns the count."""
        loaded = 0
        for path in sorted(root.glob(pattern)):
            self.register(load_skill(path))
            loaded += 1
        logger.info("skills_loaded", root=str(root), count=loaded)
        return loaded

    def load_all(self, paths: Iterable[Path]) -> int:
        return sum(self.load_dir(p) for p in paths)
