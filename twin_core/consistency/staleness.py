"""StalenessEngine (FORGE-59, spec sections 20 Staleness and Invalidation,
21 Dependency-Directed Invalidation).

"Evidence and artefacts declare dependencies... `depends_on: [REQ-PAYLOAD-
001@1, CAD-BODY-003@7, ...]`" -- the ``@N`` is a revision pin, which maps
directly onto FORGE-50's revision counter and FORGE-51's RevisionSnapshot
history. A dependency is stale exactly when its pinned revision no longer
matches the referenced entity's CURRENT revision.

Scope, stated plainly: ``Dependency.entity_kind`` is
``ControlledEntityKind`` (``"constraint" | "engineering_entity"``) -- the
two node types FORGE-50/51 gave a real, tracked ``revision`` field to.
WorkProduct ("artefact") has no revision field anywhere in this codebase;
the doc's own artefact-dependency examples (``CAD-BODY-003@7``) can't be
automatically staleness-checked until WorkProduct gains one, which is real,
separate work this sub-task doesn't invent. A Constraint/EngineeringEntity
depending on another Constraint/EngineeringEntity -- exactly the doc's own
``REQ-PAYLOAD-001@1`` case -- is fully supported today.

``propagate`` marks staleness; it always transitions to STALE, never
INVALID/SUPERSEDED (those are judgment calls about WHY something changed,
not something a dependency-graph walk can determine on its own -- a caller
sets those directly via ``set_status``). "Stale shall NOT mean deleted":
nothing here deletes or hides a node, only tags ``metadata["staleness"]``.

Known, deliberate consequence rather than a bug: marking an entity stale
(or declaring its dependencies) is itself a write through ``TwinAPI.
update_constraint``/``update_engineering_entity``, which -- per FORGE-50's
universal "every write increments revision" rule, the same precedent
FORGE-51 already accepted for baselining rather than forking a narrower
non-revision-bumping write path -- bumps that entity's OWN revision too.
So marking X stale can make anything pinned to X's pre-mark revision look
stale on the NEXT ``propagate`` call, even though X's substantive content
didn't change. Over-marking stale is the safe direction (a false "check
this again" costs a re-validation; a false "still current" costs
correctness) and is consistent with spec section 20's "never a blind
nothing-changed" principle -- not corrected away here.

FORGE-314: a dependency (what something else was declared FROM) can now
also be a WorkProduct -- Evidence pinning the exact CAD revision it
validated is the doc's own ``CAD-BODY-003@7`` example, previously
impossible here because WorkProduct has no ``revision`` int field the way
Constraint/EngineeringEntity do (FORGE-50/51). Rather than inventing a
fourth "what revision is this artefact at" concept (three already exist,
disjointly: the revision int; ``VersionService``'s dashboard-only
``metadata["_revisions"]`` list; the SUPERSEDES-chain
``geometry_recorder.py`` already builds on every ``commit_geometry`` call),
this reads a signal that already exists and is already produced by the real
CAD-edit path: a WorkProduct's "revision" here is 0 while nothing
supersedes it (it's the current tip -- the same "no incoming SUPERSEDES
edge" test ``_find_current_work_product`` already uses) and 1 the moment
something does. Evidence always pins the EXACT node id it validated, so a
single supersession hop is all that needs detecting -- no multi-hop chain
walk. ``DependencyEntityKind`` is deliberately its own, wider type rather
than widening ``ControlledEntityKind`` (``twin_core.models.patch``):  that
type specifically means "what ``TransactionEngine.commit`` can mutate via a
Patch", which a WorkProduct still can't be, so keeping them separate means
a work_product-kind ``Dependency`` can never be mistaken for something
Patch/Baseline/RevisionSnapshot already promise to handle. A WorkProduct
itself is still never staleness-MARKED (``StaleMarking``/``_get``/
``set_status`` etc. stay on the narrower ``ControlledEntityKind`` unchanged
-- only Constraint/EngineeringEntity nodes ever carry a ``staleness`` tag).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from twin_core.api import TwinAPI
from twin_core.models.enums import EdgeType
from twin_core.models.patch import ControlledEntityKind

_STALENESS_KEY = "staleness"
_DEPENDS_ON_KEY = "depends_on"

DependencyEntityKind = Literal["constraint", "engineering_entity", "work_product"]


class StalenessStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    INVALID = "invalid"
    SUPERSEDED = "superseded"
    REVALIDATED = "revalidated"


class Dependency(BaseModel):
    entity_kind: DependencyEntityKind
    entity_id: UUID
    revision: int


class StaleMarking(BaseModel):
    entity_kind: ControlledEntityKind
    entity_id: UUID
    reason: str


def _get_dependencies(metadata: dict) -> list[Dependency]:
    raw = metadata.get(_DEPENDS_ON_KEY) or []
    return [Dependency.model_validate(item) for item in raw]


def _get_staleness(metadata: dict) -> StalenessStatus:
    return StalenessStatus(metadata.get(_STALENESS_KEY, StalenessStatus.CURRENT))


async def work_product_current_revision(twin: TwinAPI, work_product_id: UUID) -> int:
    """A WorkProduct's "revision" for staleness-pin purposes: 0 while it's
    the current tip (nothing supersedes it -- the same test
    ``geometry_recorder._find_current_work_product`` already uses), 1 the
    moment something does. Public: used both here (current-revision lookup
    during ``propagate``) and by ``evidence_recorder.py`` (to pin the
    revision a new Evidence entity declares at record time)."""
    incoming = await twin.get_edges(
        work_product_id, direction="incoming", edge_type=EdgeType.SUPERSEDES
    )
    return 1 if incoming else 0


class StalenessEngine:
    def __init__(self, twin: TwinAPI) -> None:
        self._twin = twin

    async def _get(self, kind: ControlledEntityKind, entity_id: UUID):
        if kind == "constraint":
            return await self._twin.get_constraint(entity_id)
        return await self._twin.get_engineering_entity(entity_id)

    async def _set_metadata_field(
        self, kind: ControlledEntityKind, entity_id: UUID, key: str, value: object
    ) -> None:
        current = await self._get(kind, entity_id)
        if current is None:
            raise KeyError(f"{kind} {entity_id} not found")
        metadata = dict(current.metadata)
        metadata[key] = value
        if kind == "constraint":
            await self._twin.update_constraint(entity_id, {"metadata": metadata})
        else:
            await self._twin.update_engineering_entity(entity_id, {"metadata": metadata})

    async def declare_dependencies(
        self, kind: ControlledEntityKind, entity_id: UUID, dependencies: list[Dependency]
    ) -> None:
        """Record what `entity_id` was derived from, pinned at each
        dependency's CURRENT revision at declaration time."""
        await self._set_metadata_field(
            kind, entity_id, _DEPENDS_ON_KEY, [d.model_dump(mode="json") for d in dependencies]
        )

    async def set_status(
        self, kind: ControlledEntityKind, entity_id: UUID, status: StalenessStatus
    ) -> None:
        await self._set_metadata_field(kind, entity_id, _STALENESS_KEY, status.value)

    async def get_status(self, kind: ControlledEntityKind, entity_id: UUID) -> StalenessStatus:
        current = await self._get(kind, entity_id)
        if current is None:
            raise KeyError(f"{kind} {entity_id} not found")
        return _get_staleness(current.metadata)

    async def _load_graph_state(
        self, project_id: UUID
    ) -> tuple[
        list[tuple[ControlledEntityKind, UUID, dict]],
        dict[tuple[DependencyEntityKind, UUID], int],
    ]:
        constraints = await self._twin.list_constraints(project_id=project_id)
        entities = await self._twin.list_engineering_entities(project_id=project_id)
        all_nodes: list[tuple[ControlledEntityKind, UUID, dict]] = [
            ("constraint", c.id, c.metadata) for c in constraints
        ] + [("engineering_entity", e.id, e.metadata) for e in entities]

        current_revisions: dict[tuple[DependencyEntityKind, UUID], int] = {
            ("constraint", c.id): c.revision for c in constraints
        }
        current_revisions.update({("engineering_entity", e.id): e.revision for e in entities})

        # FORGE-314: any work_product referenced by an entity's own
        # depends_on pins needs its current revision resolved too -- lazily,
        # only for wp ids actually pinned (never eagerly loading every
        # WorkProduct in the project).
        wp_ids: set[UUID] = set()
        for _, _, metadata in all_nodes:
            for dep in _get_dependencies(metadata):
                if dep.entity_kind == "work_product":
                    wp_ids.add(dep.entity_id)
        for wp_id in wp_ids:
            current_revisions[("work_product", wp_id)] = await work_product_current_revision(
                self._twin, wp_id
            )

        return all_nodes, current_revisions

    @staticmethod
    def _walk(
        all_nodes: list[tuple[ControlledEntityKind, UUID, dict]],
        current_revisions: dict[tuple[DependencyEntityKind, UUID], int],
        changed_kind: DependencyEntityKind,
        changed_id: UUID,
    ) -> list[StaleMarking]:
        """Pure BFS over `depends_on` pins -- no reads, no writes. Shared by
        `propagate` (real current revisions, writes STALE as it walks) and
        FORGE-67's `ImpactEngine` (a PROJECTED revision substituted for the
        entity a not-yet-committed Patch would revise, no writes at all --
        `propagate` itself can't answer "what would this affect" before a
        commit, since marking STALE as it goes IS its whole point).
        """
        markings: list[StaleMarking] = []
        visited: set[tuple[DependencyEntityKind, UUID]] = set()
        frontier: list[tuple[DependencyEntityKind, UUID]] = [(changed_kind, changed_id)]

        while frontier:
            target_kind, target_id = frontier.pop()
            for node_kind, node_id, metadata in all_nodes:
                key = (node_kind, node_id)
                if key in visited:
                    continue
                deps = _get_dependencies(metadata)
                for dep in deps:
                    if dep.entity_kind != target_kind or dep.entity_id != target_id:
                        continue
                    current_rev = current_revisions.get((dep.entity_kind, dep.entity_id))
                    if current_rev is not None and dep.revision < current_rev:
                        visited.add(key)
                        reason = (
                            f"depends_on {dep.entity_kind} {dep.entity_id}@{dep.revision}, "
                            f"now at revision {current_rev}"
                        )
                        markings.append(
                            StaleMarking(entity_kind=node_kind, entity_id=node_id, reason=reason)
                        )
                        frontier.append(key)
                        break

        return markings

    async def propagate(
        self, project_id: UUID, changed_kind: DependencyEntityKind, changed_id: UUID
    ) -> list[StaleMarking]:
        """`changed_id` just moved to a new revision (REVISE/SUPERSEDE). Find
        every entity in `project_id` whose recorded `depends_on` pin for
        `changed_id` is now behind its current revision, mark it STALE, and
        do the same for whatever just went stale -- transitively, so a
        downstream artefact of a downstream artefact is reached too (spec
        section 21's own motor-swap example: mount CAD -> simulation ->
        BOM). "The dependency graph shall determine impact" -- this is
        that walk, not a blanket re-run or a blind no-op.
        """
        all_nodes, current_revisions = await self._load_graph_state(project_id)
        markings = self._walk(all_nodes, current_revisions, changed_kind, changed_id)
        for marking in markings:
            await self.set_status(marking.entity_kind, marking.entity_id, StalenessStatus.STALE)
        return markings

    async def preview_impact(
        self,
        project_id: UUID,
        changed_kind: DependencyEntityKind,
        changed_id: UUID,
        *,
        projected_revision: int,
    ) -> list[StaleMarking]:
        """Pure, NO-WRITE preview of what `propagate` would mark stale if
        `changed_id` moved to `projected_revision` -- for a Patch that
        hasn't committed yet (FORGE-67's `ImpactEngine.analyse`). Never
        calls `set_status`; the graph is left exactly as it was found.
        """
        all_nodes, current_revisions = await self._load_graph_state(project_id)
        current_revisions[(changed_kind, changed_id)] = projected_revision
        return self._walk(all_nodes, current_revisions, changed_kind, changed_id)
