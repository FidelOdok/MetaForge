"""ImpactEngine (FORGE-67, spec sections 21 Dependency-Directed Invalidation,
36-37 Worked Examples, 72 Core Harness Interfaces -- step 8 of the Harness
Execution Contract, Phase 7 of epic FORGE-35: Advanced Change Management).

This is the real, pre-commit "impact graph" FORGE-66 deliberately deferred:
``EngineeringChangeTransaction.analyze()`` (FORGE-66) reports only the
Patch's own DIRECT targets as ``affected_objects``, because
``StalenessEngine.propagate()`` -- the only existing dependency-graph walker
-- is strictly post-commit (it writes STALE as it walks; that IS its whole
point). ``ImpactEngine.analyse()`` closes that gap by reusing
``StalenessEngine.preview_impact`` (a pure, no-write sibling extracted from
``propagate`` specifically for this): for every REVISE/SUPERSEDE/DEPRECATE/
INVALIDATE operation in a not-yet-committed ``Patch``, it substitutes the
entity's PROJECTED post-commit revision (current + 1) into the exact same
BFS the live engine runs, and unions the results -- a real transitive walk
("servo -> gearbox -> frame -> battery -> ..."), computed before a single
write happens.

**Conflicts** reuse FORGE-64's existing evidence mechanism rather than
inventing numeric interpretation of arbitrary ``Constraint.expression``
strings against arbitrary ``Evidence.result`` dicts (which would need
either an LLM or a rigid schema neither of which exists): a "conflict" is
simply an already-recorded ``"evidence"`` entity with a real
``EdgeType.CONFLICTS_WITH`` edge into one of the patch's directly-changed
requirements (``twin.record_evidence``'s ``contradicts`` param, FORGE-64).
Severity reuses the same ``ConstraintSeverity`` "critical" proxy G4/G7
already established (ERROR -> high, WARNING -> medium, INFO -> low) rather
than a second, competing scoring system.

**The revalidation plan** is a structured list of steps, never auto-
executed. For an affected ``"evidence"`` entity, the concrete action IS
FORGE-65's real revalidation flow (rerun the producing tool, call
``twin.record_evidence`` with ``supersedes``). For anything else the walk
reaches (another Constraint/EngineeringEntity that itself depends on the
changed one), there is honestly no automatic re-run mechanism for a
requirement-depends-on-requirement link -- the step says so explicitly
rather than pretending one exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.api import TwinAPI
from twin_core.consistency.staleness import StaleMarking, StalenessEngine
from twin_core.models.enums import EdgeType
from twin_core.models.patch import ControlledEntityKind, Patch, PatchOp

ConflictSeverity = Literal["low", "medium", "high"]

_SEVERITY_BY_CONSTRAINT_SEVERITY: dict[str, ConflictSeverity] = {
    "error": "high",
    "warning": "medium",
    "info": "low",
}

_PROJECTED_REVISION_OPS = (PatchOp.REVISE, PatchOp.SUPERSEDE, PatchOp.DEPRECATE, PatchOp.INVALIDATE)


class ImpactConflict(BaseModel):
    """An already-recorded evidence entity contradicts a requirement this
    patch touches (spec's "simulation discovery" worked example)."""

    requirement_id: UUID
    evidence_id: UUID
    severity: ConflictSeverity
    description: str


class RevalidationStep(BaseModel):
    entity_kind: ControlledEntityKind
    entity_id: UUID
    reason: str
    action: str


class ImpactReport(BaseModel):
    """Outcome of ``ImpactEngine.analyse(patch, state)``."""

    directly_changed: list[UUID] = Field(default_factory=list)
    affected_objects: list[UUID] = Field(default_factory=list)
    conflicts: list[ImpactConflict] = Field(default_factory=list)
    revalidation_plan: list[RevalidationStep] = Field(default_factory=list)


class ImpactEngine:
    """Spec section 72's core interface: `async def analyse(patch, state)
    -> ImpactReport`."""

    def __init__(self, twin: TwinAPI) -> None:
        self._twin = twin
        self._staleness = StalenessEngine(twin)

    async def analyse(self, patch: Patch, state: dict[str, object] | None = None) -> ImpactReport:
        """Pure preview -- reads only, never writes, never requires `patch`
        to have been committed. `state` is accepted for interface parity
        with `HITLEngine.required_approval`/spec section 72 but not yet
        consulted by this engine.
        """
        if patch.project_id is None:
            raise ValueError("ImpactEngine.analyse: patch.project_id is required")
        project_id = patch.project_id

        directly_changed: set[UUID] = set()
        markings_by_id: dict[UUID, StaleMarking] = {}

        for op in patch.operations:
            if op.op not in _PROJECTED_REVISION_OPS:
                continue
            assert op.entity_kind is not None
            assert op.entity_id is not None
            directly_changed.add(op.entity_id)

            current = await self._get(op.entity_kind, op.entity_id)
            if current is None:
                continue  # unresolvable target -- TransactionEngine.commit will reject it
            markings = await self._staleness.preview_impact(
                project_id,
                op.entity_kind,
                op.entity_id,
                projected_revision=current.revision + 1,
            )
            for marking in markings:
                markings_by_id.setdefault(marking.entity_id, marking)

        conflicts = await self._find_conflicts(directly_changed)
        revalidation_plan = await self._build_revalidation_plan(markings_by_id.values())

        return ImpactReport(
            directly_changed=sorted(directly_changed, key=str),
            affected_objects=sorted(markings_by_id.keys(), key=str),
            conflicts=conflicts,
            revalidation_plan=revalidation_plan,
        )

    async def _get(self, kind: ControlledEntityKind, entity_id: UUID):
        if kind == "constraint":
            return await self._twin.get_constraint(entity_id)
        return await self._twin.get_engineering_entity(entity_id)

    async def _find_conflicts(self, requirement_ids: set[UUID]) -> list[ImpactConflict]:
        conflicts: list[ImpactConflict] = []
        for requirement_id in requirement_ids:
            requirement = await self._twin.get_constraint(requirement_id)
            if requirement is None:
                continue  # not a Constraint -- no "requirement" conflict concept applies
            edges = await self._twin.get_edges(
                requirement_id, direction="incoming", edge_type=EdgeType.CONFLICTS_WITH
            )
            severity = _SEVERITY_BY_CONSTRAINT_SEVERITY.get(requirement.severity.value, "medium")
            for edge in edges:
                conflicts.append(
                    ImpactConflict(
                        requirement_id=requirement_id,
                        evidence_id=edge.source_id,
                        severity=severity,
                        description=(
                            f"evidence {edge.source_id} contradicts requirement "
                            f"{requirement.name!r}"
                        ),
                    )
                )
        return conflicts

    async def _build_revalidation_plan(
        self, markings: Iterable[StaleMarking]
    ) -> list[RevalidationStep]:
        steps: list[RevalidationStep] = []
        for marking in markings:
            entity = await self._get(marking.entity_kind, marking.entity_id)
            if entity is None:
                continue
            is_evidence = (
                marking.entity_kind == "engineering_entity"
                and getattr(entity, "entity_type", None) == "evidence"
            )
            action = (
                "rerun the producing tool and call twin.record_evidence with "
                "supersedes=<this id> (FORGE-65 revalidation flow)"
                if is_evidence
                else "review for consistency with the change -- no automatic re-run "
                "mechanism exists for this entity kind"
            )
            steps.append(
                RevalidationStep(
                    entity_kind=marking.entity_kind,
                    entity_id=marking.entity_id,
                    reason=marking.reason,
                    action=action,
                )
            )
        return steps
