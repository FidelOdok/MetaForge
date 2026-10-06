"""A design flow as a dependency graph (FORGE-539).

A flow used to be an ordered list, and the engines walked it in order. That
is still the default: a phase with no ``depends_on`` depends on the phase
before it, so every existing template and every flow approved before this
module is the same straight line it always was.

A phase that declares ``depends_on`` names the phases whose results it
needs, and nothing else. Two phases that do not depend on each other can run
at the same time (electronics and mechanical design, say), and a change to
one does not invalidate the other. That last property is what selective
replanning rests on: sending a run back to a phase re-runs that phase and
the phases downstream of it, not every phase that happens to come later in
the list.

A phase may also carry a ``condition`` over the flow's facts (the
manufacturing route, the target maturity, whether the loads are known). A
phase whose condition is false is **skipped**, which is recorded as skipped
and never counts as having produced anything. A phase depending on a
skipped phase waits only for the phases that actually run.

Pure functions over plain data. Both engines import this, including the
Temporal workflow, so there is no I/O, no clock and no randomness here, and
every result is ordered deterministically (by the flow's own phase order).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "ConditionError",
    "FlowGraph",
    "GraphError",
    "PhaseLike",
    "build_graph",
    "evaluate_condition",
    "is_linear",
    "parse_condition",
    "rework_candidates",
]


class PhaseLike(Protocol):
    """What the graph needs from a phase: its id and its declared edges.

    Satisfied by :class:`~orchestrator.design_flow.spec.Phase` and
    :class:`~orchestrator.design_flow.frozen.FrozenPhase` alike.
    """

    id: str


class GraphError(ValueError):
    """The declared dependencies do not form a runnable graph."""


class ConditionError(ValueError):
    """A phase condition is not in the small language this module accepts."""


def _depends_on(phase: Any) -> tuple[str, ...] | None:
    raw = getattr(phase, "depends_on", None)
    if raw is None:
        return None
    return tuple(str(d) for d in raw)


@dataclass(frozen=True)
class FlowGraph:
    """The phases of one flow and the edges between them.

    ``order`` is the flow's own phase order, kept so every list this graph
    returns is deterministic and reads the way the flow was written.
    ``parents[p]`` are the phases ``p`` needs; ``children[p]`` the phases that
    need ``p``.
    """

    order: tuple[str, ...]
    parents: Mapping[str, tuple[str, ...]]
    children: Mapping[str, tuple[str, ...]]
    conditions: Mapping[str, str]

    # -- shape ---------------------------------------------------------------

    def roots(self) -> list[str]:
        return [p for p in self.order if not self.parents[p]]

    def topological(self) -> list[str]:
        """Phase ids in dependency order, ties broken by flow order."""
        index = {p: i for i, p in enumerate(self.order)}
        remaining = {p: len(self.parents[p]) for p in self.order}
        ready = sorted((p for p, n in remaining.items() if n == 0), key=index.__getitem__)
        out: list[str] = []
        while ready:
            current = ready.pop(0)
            out.append(current)
            for child in self.children[current]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    ready.append(child)
                    ready.sort(key=index.__getitem__)
        return out

    def waves(self) -> list[list[str]]:
        """Groups of phases that can run together, each after the last.

        Wave ``n`` holds every phase whose longest dependency chain has
        length ``n``. A straight-line flow is one phase per wave.
        """
        depth: dict[str, int] = {}
        for phase in self.topological():
            depth[phase] = 1 + max((depth[p] for p in self.parents[phase]), default=-1)
        out: list[list[str]] = []
        for phase in self.order:
            level = depth[phase]
            while len(out) <= level:
                out.append([])
            out[level].append(phase)
        return out

    # -- scheduling ----------------------------------------------------------

    def ready(
        self,
        *,
        done: Iterable[str],
        skipped: Iterable[str] = (),
        running: Iterable[str] = (),
    ) -> list[str]:
        """Phases whose every parent has settled and that have not started.

        A parent settles by finishing (``done``) or by being skipped. A
        skipped parent releases its children without counting as evidence
        for them: whether a child may run without that input is the child's
        own condition, not something this method infers.
        """
        settled = set(done) | set(skipped)
        excluded = settled | set(running)
        return [
            p
            for p in self.order
            if p not in excluded and all(parent in settled for parent in self.parents[p])
        ]

    def downstream(self, phase_id: str) -> list[str]:
        """``phase_id`` and every phase that depends on it, transitively.

        This is the set a rework to ``phase_id`` re-runs. Everything else
        keeps its result and its approval.
        """
        self._require(phase_id)
        seen = {phase_id}
        stack = [phase_id]
        while stack:
            for child in self.children[stack.pop()]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return [p for p in self.order if p in seen]

    def upstream(self, phase_id: str) -> list[str]:
        """Every phase ``phase_id`` depends on, transitively (not itself)."""
        self._require(phase_id)
        seen: set[str] = set()
        stack = list(self.parents[phase_id])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.parents[current])
        return [p for p in self.order if p in seen]

    def _require(self, phase_id: str) -> None:
        if phase_id not in self.parents:
            raise GraphError(f"unknown phase '{phase_id}'; phases: {', '.join(self.order)}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "phases": list(self.order),
            "edges": [{"from": p, "to": c} for p in self.order for c in self.children[p]],
            "waves": self.waves(),
            "conditions": dict(self.conditions),
            "linear": is_linear(self),
        }


def build_graph(phases: Sequence[Any]) -> FlowGraph:
    """The dependency graph of ``phases``. Raises :class:`GraphError`.

    A phase with ``depends_on`` set (even to an empty tuple, which makes it
    a root) uses exactly those edges. A phase without it depends on the
    phase before it, which is how every flow behaved before graphs existed.
    """
    order = tuple(str(p.id) for p in phases)
    if len(set(order)) != len(order):
        dupes = sorted({p for p in order if order.count(p) > 1})
        raise GraphError(f"duplicate phase ids: {', '.join(dupes)}")
    known = set(order)
    parents: dict[str, tuple[str, ...]] = {}
    conditions: dict[str, str] = {}
    for index, phase in enumerate(phases):
        declared = _depends_on(phase)
        if declared is None:
            parents[phase.id] = (order[index - 1],) if index else ()
        else:
            unknown = [d for d in declared if d not in known]
            if unknown:
                raise GraphError(
                    f"phase '{phase.id}' depends on unknown phase(s): {', '.join(unknown)}"
                )
            if phase.id in declared:
                raise GraphError(f"phase '{phase.id}' depends on itself")
            parents[phase.id] = tuple(dict.fromkeys(declared))
        condition = getattr(phase, "condition", None)
        if condition:
            parse_condition(str(condition))  # raises ConditionError early
            conditions[phase.id] = str(condition)
    children: dict[str, list[str]] = {p: [] for p in order}
    for phase in order:
        for parent in parents[phase]:
            children[parent].append(phase)
    graph = FlowGraph(
        order=order,
        parents=parents,
        children={p: tuple(c) for p, c in children.items()},
        conditions=conditions,
    )
    if len(graph.topological()) != len(order):
        cyclic = sorted(set(order) - set(graph.topological()))
        raise GraphError(f"dependency cycle among phases: {', '.join(cyclic)}")
    return graph


def rework_candidates(graph: FlowGraph, phase_id: str) -> list[str]:
    """The phase ids to validate a rework from ``phase_id`` against, in order.

    Pass the result to :func:`~orchestrator.design_flow.rework.rework_target_error`.
    A straight-line flow keeps its whole phase list (every earlier phase is a
    target, as before). A graph flow offers only the phases ``phase_id``
    depends on: sending a run back to a phase this one never consumed would
    re-run work that cannot have caused the problem.
    """
    if is_linear(graph):
        return list(graph.order)
    return [*graph.upstream(phase_id), phase_id]


def is_linear(graph: FlowGraph) -> bool:
    """Whether ``graph`` is the plain ordered list the engines always ran.

    True when every phase depends on exactly the one before it and nothing
    is conditional. The engines keep their original sequential loop for
    these, so flows approved before graphs existed replay unchanged.
    """
    if graph.conditions:
        return False
    for index, phase in enumerate(graph.order):
        expected = (graph.order[index - 1],) if index else ()
        if tuple(graph.parents[phase]) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------
#
# Deliberately tiny: comparisons of a fact against literals, joined by `and`.
# No expressions, no function calls, no eval. A condition decides whether
# engineering work happens at all, so it has to be readable by the person
# approving the flow and impossible to make do anything else.

_CLAUSE = re.compile(
    r"^\s*(?P<key>[a-z][a-z0-9_]*)\s*"
    r"(?P<op>==|!=|\bnot\s+in\b|\bin\b)\s*"
    r"(?P<value>\[[^\]]*\]|[A-Za-z0-9_.\-]+)\s*$"
)


@dataclass(frozen=True)
class _Clause:
    key: str
    op: str
    values: tuple[str, ...]


def parse_condition(text: str) -> list[_Clause]:
    """Parse ``key == value and key in [a, b]``. Raises :class:`ConditionError`."""
    clauses: list[_Clause] = []
    for raw in re.split(r"\s+and\s+", text.strip()):
        match = _CLAUSE.match(raw)
        if match is None:
            raise ConditionError(
                f"cannot read condition clause '{raw}'; use 'fact == value', "
                "'fact != value', 'fact in [a, b]' or 'fact not in [a, b]', joined by 'and'"
            )
        value = match.group("value")
        if value.startswith("["):
            values = tuple(v.strip() for v in value[1:-1].split(",") if v.strip())
            if not values:
                raise ConditionError(f"empty list in condition clause '{raw}'")
        else:
            values = (value,)
        op = " ".join(match.group("op").split())
        if op in {"==", "!="} and len(values) != 1:
            raise ConditionError(f"'{op}' takes one value in clause '{raw}'")
        clauses.append(_Clause(key=match.group("key"), op=op, values=values))
    return clauses


def evaluate_condition(text: str | None, facts: Mapping[str, str]) -> bool:
    """Whether ``text`` holds for ``facts``. An empty condition always holds.

    A fact the condition names but ``facts`` lacks makes the clause false,
    not an error: an unstated route is not "in_house", and a phase that only
    applies in-house must not run on a guess.
    """
    if not text:
        return True
    for clause in parse_condition(text):
        value = facts.get(clause.key)
        if clause.op == "==":
            ok = value is not None and value == clause.values[0]
        elif clause.op == "!=":
            ok = value is not None and value != clause.values[0]
        elif clause.op == "in":
            ok = value is not None and value in clause.values
        else:  # "not in"
            ok = value is not None and value not in clause.values
        if not ok:
            return False
    return True
