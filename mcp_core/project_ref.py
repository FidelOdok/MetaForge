"""Turn what someone typed into one project, or refuse (FORGE-335).

"Open the arm project" has to mean the same thing however it arrives — a
human patching a chat thread's scope, the agent calling
``chat.set_project_scope`` mid-turn, the TUI's ``--project`` flag, or an
MCP client calling ``project.open``. Three implementations of that already
existed (``api_gateway/chat/scope.py``, ``tui/src/lib/project.ts``, and the
CLI's own ``_resolve_project_ref``) and the MCP surface had none at all, so
an external harness could only pass a UUID.

The rule that matters, and the reason this is worth sharing rather than
reimplementing: **several matches is an error, never a guess.** Silently
picking one scopes a design change to the wrong project, and nothing
downstream can tell that happened.

Layer-1 module: stdlib only, no I/O. The caller supplies the candidates,
because where the project list comes from differs per surface and this
file having an opinion about that is what would make it un-shareable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "AmbiguousProjectRef",
    "NoProjectsExist",
    "ProjectRef",
    "ProjectRefError",
    "UnknownProjectRef",
    "resolve_project_ref",
]

#: How many matches to name before summarising. Enough to choose from,
#: short enough to read in a terminal or a tool-call error.
_MAX_SHOWN = 5


@dataclass(frozen=True)
class ProjectRef:
    """The two fields resolution needs. Deliberately not the full project."""

    id: str
    name: str


class ProjectRefError(ValueError):
    """A reference could not be turned into exactly one project."""


class NoProjectsExist(ProjectRefError):
    """There is nothing to match against, which is a different problem."""


class UnknownProjectRef(ProjectRefError):
    """Nothing matched."""


class AmbiguousProjectRef(ProjectRefError):
    """Several matched. Held separate so a caller can offer the choice."""

    def __init__(self, query: str, matches: Sequence[ProjectRef]) -> None:
        self.query = query
        self.matches = list(matches)
        shown = [f"{p.name} ({p.id[:8]})" for p in self.matches[:_MAX_SHOWN]]
        more = f", +{len(self.matches) - len(shown)} more" if len(self.matches) > len(shown) else ""
        super().__init__(
            f'"{query}" matches {len(self.matches)} projects: {", ".join(shown)}{more} '
            "— be more specific or use the id"
        )


def resolve_project_ref(query: str, candidates: Sequence[ProjectRef]) -> ProjectRef:
    """id → exact name (case-insensitive) → unique substring of the name.

    The order is not arbitrary. An id is unambiguous by construction, and an
    exact name should beat a substring even when the substring would also
    match something else — otherwise naming a project exactly becomes
    ambiguous the moment a longer name contains it.
    """
    q = query.strip()
    if not q:
        raise ProjectRefError("a project id or name is required")
    if not candidates:
        raise NoProjectsExist("no projects exist on this gateway")

    by_id = next((p for p in candidates if p.id == q), None)
    if by_id is not None:
        return by_id

    lower = q.lower()
    exact = [p for p in candidates if p.name.lower() == lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousProjectRef(q, exact)

    partial = [p for p in candidates if lower in p.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise AmbiguousProjectRef(q, partial)

    raise UnknownProjectRef(f'no project matches "{q}"')
