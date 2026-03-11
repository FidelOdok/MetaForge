"""Constraint engine — ABC and in-memory implementation for cross-domain validation."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from observability.metrics import MetricsCollector

from twin_core.constraint_engine.context import ConstraintContext, build_context
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.constraint_engine.resolver import (
    find_constrained_artifacts,
    resolve_constraints,
)
from twin_core.graph_engine import GraphEngine
from twin_core.models.constraint import Constraint
from twin_core.models.enums import (
    ConstraintSeverity,
    ConstraintStatus,
    NodeType,
)
from twin_core.models.relationship import ConstrainedByEdge

# Safe builtins whitelist — no __import__, open, exec, eval, compile, etc.
_SAFE_BUILTINS: dict = {
    "all": all,
    "any": any,
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "isinstance": isinstance,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "True": True,
    "False": False,
    "None": None,
}


class ConstraintEngine(ABC):
    """Abstract interface for constraint evaluation against the Digital Twin graph."""

    @abstractmethod
    async def evaluate(self, artifact_ids: list[UUID]) -> ConstraintEvaluationResult:
        """Evaluate constraints relevant to the given artifacts.

        Returns a result indicating whether all ERROR-severity constraints pass.
        """
        ...

    @abstractmethod
    async def evaluate_all(self) -> ConstraintEvaluationResult:
        """Evaluate every constraint in the graph."""
        ...

    @abstractmethod
    async def add_constraint(self, constraint: Constraint, artifact_ids: list[UUID]) -> Constraint:
        """Register a constraint and create CONSTRAINED_BY edges to the given artifacts."""
        ...

    @abstractmethod
    async def get_constraint(self, constraint_id: UUID) -> Constraint | None:
        """Retrieve a constraint by ID, or None if not found."""
        ...

    @abstractmethod
    async def remove_constraint(self, constraint_id: UUID) -> bool:
        """Delete a constraint node and all its edges. Returns False if not found."""
        ...


class InMemoryConstraintEngine(ConstraintEngine):
    """In-memory constraint engine backed by a GraphEngine."""

    def __init__(
        self,
        graph: GraphEngine,
        collector: MetricsCollector | None = None,
    ) -> None:
        self._graph = graph
        self._collector: MetricsCollector | None = collector

    async def evaluate(self, artifact_ids: list[UUID]) -> ConstraintEvaluationResult:
        start = time.monotonic()

        constraints = await resolve_constraints(self._graph, artifact_ids)
        if not constraints:
            elapsed = (time.monotonic() - start) * 1000
            return ConstraintEvaluationResult(passed=True, evaluated_count=0, duration_ms=elapsed)

        ctx = await build_context(self._graph)

        violations: list[ConstraintViolation] = []
        warnings: list[ConstraintViolation] = []
        skipped_count = 0
        now = datetime.now(UTC)

        for constraint in constraints:
            status, message = self._eval_expression(constraint.expression, ctx)

            if status == ConstraintStatus.SKIPPED:
                skipped_count += 1
                await self._update_constraint_status(constraint.id, ConstraintStatus.SKIPPED, now)
                continue

            # Determine new status based on expression result
            if status == ConstraintStatus.PASS:
                await self._update_constraint_status(constraint.id, ConstraintStatus.PASS, now)
            else:
                # Expression returned False — it's a failure
                artifact_ids_for_constraint = await find_constrained_artifacts(
                    self._graph, constraint.id
                )
                violation = ConstraintViolation(
                    constraint_id=constraint.id,
                    constraint_name=constraint.name,
                    severity=constraint.severity,
                    message=message or constraint.message,
                    artifact_ids=artifact_ids_for_constraint,
                    expression=constraint.expression,
                    evaluated_at=now,
                )

                if constraint.severity == ConstraintSeverity.ERROR:
                    violations.append(violation)
                    await self._update_constraint_status(constraint.id, ConstraintStatus.FAIL, now)
                else:
                    # WARNING or INFO
                    warnings.append(violation)
                    await self._update_constraint_status(constraint.id, ConstraintStatus.WARN, now)

        elapsed = (time.monotonic() - start) * 1000
        if self._collector:
            result_label = "pass" if len(violations) == 0 else "fail"
            self._collector.record_constraint_evaluation(
                "cross-domain", result_label, elapsed / 1000
            )
        return ConstraintEvaluationResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            evaluated_count=len(constraints) - skipped_count,
            skipped_count=skipped_count,
            duration_ms=elapsed,
        )

    async def evaluate_all(self) -> ConstraintEvaluationResult:
        all_artifacts = await self._graph.list_nodes(node_type=NodeType.ARTIFACT)
        artifact_ids = [a.id for a in all_artifacts]
        return await self.evaluate(artifact_ids)

    async def add_constraint(self, constraint: Constraint, artifact_ids: list[UUID]) -> Constraint:
        # Verify constraint doesn't already exist
        existing = await self._graph.get_node(constraint.id)
        if existing is not None:
            raise ValueError(f"Constraint with ID {constraint.id} already exists")

        # Verify all target artifacts exist
        for aid in artifact_ids:
            node = await self._graph.get_node(aid)
            if node is None:
                raise ValueError(f"Artifact {aid} does not exist")

        # Add the constraint node
        await self._graph.add_node(constraint)

        # Create CONSTRAINED_BY edges from each artifact to the constraint
        scope = "global" if constraint.cross_domain else "local"
        for aid in artifact_ids:
            edge = ConstrainedByEdge(
                source_id=aid,
                target_id=constraint.id,
                scope=scope,
            )
            await self._graph.add_edge(edge)

        return constraint

    async def get_constraint(self, constraint_id: UUID) -> Constraint | None:
        node = await self._graph.get_node(constraint_id)
        if node is not None and isinstance(node, Constraint):
            return node
        return None

    async def remove_constraint(self, constraint_id: UUID) -> bool:
        node = await self._graph.get_node(constraint_id)
        if node is None:
            return False
        return await self._graph.delete_node(constraint_id)

    async def _update_constraint_status(
        self, constraint_id: UUID, status: ConstraintStatus, evaluated_at: datetime
    ) -> None:
        try:
            await self._graph.update_node(
                constraint_id,
                {"status": status, "last_evaluated": evaluated_at},
            )
        except KeyError:
            pass  # Constraint was deleted between resolve and update

    @staticmethod
    def _eval_expression(expression: str, ctx: ConstraintContext) -> tuple[ConstraintStatus, str]:
        """Compile and evaluate a constraint expression with restricted builtins.

        Returns (ConstraintStatus, message) where message describes failures.
        """
        try:
            code = compile(expression, "<constraint>", "eval")
        except SyntaxError as exc:
            return ConstraintStatus.SKIPPED, f"Syntax error: {exc}"

        restricted_globals = {"__builtins__": _SAFE_BUILTINS, "ctx": ctx}
        try:
            result = eval(code, restricted_globals)  # noqa: S307
        except Exception as exc:
            return ConstraintStatus.SKIPPED, f"Runtime error: {exc}"

        if result:
            return ConstraintStatus.PASS, ""
        return ConstraintStatus.FAIL, f"Expression evaluated to {result!r}"
