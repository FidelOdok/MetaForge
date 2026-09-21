"""Shared parent-ref resolution for the Engineering Intent & Requirements
Harness (FORGE-45, epic FORGE-35).

A ``parent_ref`` names the entity a new Constraint/EngineeringEntity derives
from / implements / satisfies / etc. -- either a bare UUID string used
directly, or an exact ``name`` (Constraint) / ``title`` (EngineeringEntity)
scoped to the same project. Resolution is loud: zero or multiple matches
raise ``ValueError`` naming the ambiguous/missing ref -- a link is never
silently dropped, the same discipline as ``geometry_recorder.py``'s
SUPERSEDES exact-name-match precedent (MET-630). Shared by
``engineering_entity_recorder.py`` (FORGE-45) and ``constraint_recorder.py``
(FORGE-46) so both use one implementation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def resolve_ref(twin: Any, ref: str, *, project_id: str | None) -> UUID:
    """Resolve one ``ref`` to a node id.

    A ref that parses as a UUID is used directly (not validated against the
    graph -- the caller's ``add_edge`` will fail loudly on a non-existent
    target). Otherwise ``ref`` is matched exactly against Constraint.name and
    EngineeringEntity.title among nodes in ``project_id`` (unscoped when
    ``project_id`` is ``None`` -- matches across the whole graph, which is
    only safe for callers that already know that's what they want).
    """
    cleaned = ref.strip()
    if not cleaned:
        raise ValueError("parent ref must be a non-empty string")
    try:
        return UUID(cleaned)
    except ValueError:
        pass

    pid = UUID(project_id) if project_id else None
    matches: list[UUID] = [
        c.id for c in await twin.list_constraints(project_id=pid) if c.name == cleaned
    ]
    matches += [
        e.id for e in await twin.list_engineering_entities(project_id=pid) if e.title == cleaned
    ]

    if not matches:
        raise ValueError(
            f"parent ref {cleaned!r} did not resolve to any Constraint (by name) or "
            "EngineeringEntity (by title) -- record the parent first, or pass its id directly"
        )
    if len(matches) > 1:
        raise ValueError(
            f"parent ref {cleaned!r} is ambiguous -- {len(matches)} nodes match; "
            "pass the id directly instead of the name"
        )
    return matches[0]


async def resolve_refs(twin: Any, refs: list[str], *, project_id: str | None) -> list[UUID]:
    """Resolve each of ``refs`` in order. Raises on the first unresolvable one."""
    return [await resolve_ref(twin, ref, project_id=project_id) for ref in refs]
