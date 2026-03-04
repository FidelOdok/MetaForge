"""Dependency engine — DAG validation, topological sort, ready-step resolution.

Builds a ``DependencyGraph`` from a ``WorkflowDefinition`` and provides:

* Cycle detection (Kahn's algorithm)
* Topological ordering of steps
* Ready-step resolution given a ``WorkflowRun``
* ``$ref:step_id.field`` parameter resolution for output passing
"""

from __future__ import annotations

from collections import deque
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.workflow_dag import (
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.dependency")


class CyclicDependencyError(Exception):
    """Raised when a workflow definition contains a dependency cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' -> '.join(cycle)}")


class DependencyGraph:
    """Directed acyclic graph of workflow step dependencies."""

    def __init__(self, definition: WorkflowDefinition) -> None:
        self._definition = definition
        self._steps: dict[str, WorkflowStep] = {}
        self._adjacency: dict[str, list[str]] = {}   # step -> dependents
        self._reverse: dict[str, list[str]] = {}      # step -> dependencies
        self._build()

    def _build(self) -> None:
        for step in self._definition.steps:
            self._steps[step.step_id] = step
            self._adjacency.setdefault(step.step_id, [])
            self._reverse.setdefault(step.step_id, [])

        for step in self._definition.steps:
            for dep in step.depends_on:
                if dep not in self._steps:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on unknown step '{dep}'"
                    )
                self._adjacency[dep].append(step.step_id)
                self._reverse[step.step_id].append(dep)

    def validate(self) -> None:
        """Raise ``CyclicDependencyError`` if the graph contains a cycle."""
        with tracer.start_as_current_span("dependency.validate") as span:
            in_degree: dict[str, int] = {
                sid: len(deps) for sid, deps in self._reverse.items()
            }
            queue: deque[str] = deque(
                sid for sid, deg in in_degree.items() if deg == 0
            )
            visited: list[str] = []

            while queue:
                node = queue.popleft()
                visited.append(node)
                for dependent in self._adjacency.get(node, []):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

            if len(visited) != len(self._steps):
                remaining = set(self._steps) - set(visited)
                span.set_attribute("dependency.has_cycle", True)
                raise CyclicDependencyError(sorted(remaining))

            span.set_attribute("dependency.has_cycle", False)
            span.set_attribute("dependency.step_count", len(self._steps))

    def topological_sort(self) -> list[str]:
        """Return step IDs in a valid execution order."""
        self.validate()

        in_degree: dict[str, int] = {
            sid: len(deps) for sid, deps in self._reverse.items()
        }
        queue: deque[str] = deque(
            sid for sid, deg in in_degree.items() if deg == 0
        )
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for dependent in self._adjacency.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return result

    def get_ready_steps(self, run: WorkflowRun) -> list[str]:
        """Return step IDs whose dependencies are all COMPLETED."""
        ready: list[str] = []
        for step_id, step in self._steps.items():
            sr = run.step_results.get(step_id)
            if sr is None:
                continue
            if sr.status not in {StepStatus.PENDING, StepStatus.WAITING, StepStatus.READY}:
                continue
            deps_met = all(
                run.step_results.get(dep) is not None
                and run.step_results[dep].status == StepStatus.COMPLETED
                for dep in step.depends_on
            )
            if deps_met:
                ready.append(step_id)
        return ready

    def get_dependents(self, step_id: str) -> list[str]:
        """Return step IDs that depend on *step_id*."""
        return list(self._adjacency.get(step_id, []))

    def get_dependencies(self, step_id: str) -> list[str]:
        """Return step IDs that *step_id* depends on."""
        return list(self._reverse.get(step_id, []))

    def get_step(self, step_id: str) -> WorkflowStep | None:
        return self._steps.get(step_id)

    def resolve_step_inputs(
        self,
        step: WorkflowStep,
        completed_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Resolve ``$ref:step_id.field`` references in step parameters.

        Parameters whose values start with ``$ref:`` are replaced with the
        corresponding field from a completed step's ``task_result``.
        """
        resolved: dict[str, Any] = {}
        for key, value in step.parameters.items():
            if isinstance(value, str) and value.startswith("$ref:"):
                ref_path = value[5:]  # strip "$ref:"
                parts = ref_path.split(".", 1)
                if len(parts) == 2:
                    ref_step, ref_field = parts
                    step_result = completed_results.get(ref_step, {})
                    resolved[key] = step_result.get(ref_field, value)
                else:
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved
