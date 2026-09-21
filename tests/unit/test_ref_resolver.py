"""Shared parent-ref resolver (FORGE-45, epic FORGE-35).

Exact-name-or-UUID resolution across Constraint (by name) and
EngineeringEntity (by title) nodes, scoped to a project -- loud on
zero/multiple matches, never a silently dropped link.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin._ref_resolver import resolve_ref, resolve_refs
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT_ID = "22222222-2222-4222-8222-222222222222"


async def _make_constraint(twin: InMemoryTwinAPI, name: str, project_id: str | None) -> Constraint:
    c = Constraint(
        name=name,
        expression="True",
        severity=ConstraintSeverity.INFO,
        domain="systems",
        source="test",
        project_id=UUID(project_id) if project_id else None,
    )
    return await twin.create_constraint(c)


async def _make_entity(
    twin: InMemoryTwinAPI, title: str, project_id: str | None
) -> EngineeringEntity:
    e = EngineeringEntity(
        entity_type="intent",
        statement="x",
        title=title,
        project_id=UUID(project_id) if project_id else None,
    )
    return await twin.create_engineering_entity(e)


@pytest.mark.asyncio
async def test_resolves_a_bare_uuid_directly_without_a_lookup() -> None:
    twin = InMemoryTwinAPI.create()
    some_id = uuid4()
    resolved = await resolve_ref(twin, str(some_id), project_id=None)
    assert resolved == some_id


@pytest.mark.asyncio
async def test_resolves_by_constraint_name() -> None:
    twin = InMemoryTwinAPI.create()
    c = await _make_constraint(twin, "system_mass_budget", PROJECT_ID)
    resolved = await resolve_ref(twin, "system_mass_budget", project_id=PROJECT_ID)
    assert resolved == c.id


@pytest.mark.asyncio
async def test_resolves_by_engineering_entity_title() -> None:
    twin = InMemoryTwinAPI.create()
    e = await _make_entity(twin, "Desktop quadruped intent", PROJECT_ID)
    resolved = await resolve_ref(twin, "Desktop quadruped intent", project_id=PROJECT_ID)
    assert resolved == e.id


@pytest.mark.asyncio
async def test_unresolvable_ref_raises_loudly() -> None:
    twin = InMemoryTwinAPI.create()
    with pytest.raises(ValueError, match="did not resolve"):
        await resolve_ref(twin, "nonexistent_requirement", project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_ambiguous_ref_raises_loudly_rather_than_picking_one() -> None:
    twin = InMemoryTwinAPI.create()
    await _make_constraint(twin, "dup_name", PROJECT_ID)
    await _make_entity(twin, "dup_name", PROJECT_ID)
    with pytest.raises(ValueError, match="ambiguous"):
        await resolve_ref(twin, "dup_name", project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_resolution_is_scoped_to_project() -> None:
    twin = InMemoryTwinAPI.create()
    await _make_constraint(twin, "scoped_constraint", PROJECT_ID)
    with pytest.raises(ValueError, match="did not resolve"):
        await resolve_ref(twin, "scoped_constraint", project_id=OTHER_PROJECT_ID)


@pytest.mark.asyncio
async def test_empty_ref_rejected() -> None:
    twin = InMemoryTwinAPI.create()
    with pytest.raises(ValueError, match="non-empty"):
        await resolve_ref(twin, "   ", project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_resolve_refs_resolves_each_in_order() -> None:
    twin = InMemoryTwinAPI.create()
    a = await _make_constraint(twin, "a", PROJECT_ID)
    b = await _make_entity(twin, "b", PROJECT_ID)
    resolved = await resolve_refs(twin, ["a", "b"], project_id=PROJECT_ID)
    assert resolved == [a.id, b.id]
