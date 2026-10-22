"""Composing domain fragments into a flow (composable flows).

A product flow is the spine (a template's gated stages) plus the fragments
its intent names. Composition turns that into one dependency graph:

1. Each fragment task becomes a gateless phase. A block's members become one
   block phase (:mod:`orchestrator.design_flow.coupling`).
2. A task's dependencies are **derived, not declared**: the tasks that write
   its inputs, plus whatever its stage itself waits for (so design work
   starts only once the stage before it is approved).
3. Each stage phase additionally depends on the tasks attached to it, so its
   gate reviews their outputs and evaluates their checks.

Because the edges come from the data, a new revision of one item makes stale
exactly the tasks that read it and what reads their outputs
(:mod:`orchestrator.design_flow.lifecycle`), and a rework re-runs only those
(``FlowGraph.downstream``) -- Planner §21, "the harness SHALL invalidate only
downstream dependencies".

Refused, with the rule named: a task whose stage the flow does not have, a
fragment whose required fragment is missing, two tasks writing one item, and
data that flows backwards across stages (a design task reading a V&V
result), which would be a cycle through the stage gates.

Pure functions, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import structlog

from orchestrator.design_flow.contract import outputs_of, ref_key
from orchestrator.design_flow.coupling import block_phase
from orchestrator.design_flow.fragments import Fragment, task_phase_id
from orchestrator.design_flow.graph import GraphError, build_graph
from orchestrator.design_flow.spec import FlowDefinition, Phase

logger = structlog.get_logger(__name__)

__all__ = [
    "ComposedFlow",
    "CompositionError",
    "ExternalInput",
    "compose",
    "fragment_ids",
]


class CompositionError(ValueError):
    """The fragments cannot be composed into this flow. The message names why."""


@dataclass(frozen=True)
class ExternalInput:
    """An input no task in the flow writes: a person or a requirement must supply it.

    Typically a limit (the allowed junction temperature, the maximum ambient)
    that has to come from the requirements or a datasheet with its source.
    Until it is recorded, every check that reads it is ``no_data``.
    """

    ref: str
    item_key: str
    consumers: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"ref": self.ref, "item_key": self.item_key, "consumers": list(self.consumers)}


@dataclass(frozen=True)
class ComposedFlow:
    definition: FlowDefinition
    fragments: tuple[str, ...]
    external_inputs: tuple[ExternalInput, ...] = ()
    #: task phase id -> the phase ids it was derived to depend on.
    task_dependencies: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "fragments": list(self.fragments),
            "external_inputs": [e.as_dict() for e in self.external_inputs],
            "task_dependencies": {k: list(v) for k, v in self.task_dependencies.items()},
        }


def fragment_ids(definition: FlowDefinition) -> tuple[str, ...]:
    """The fragments a flow was composed from, in the order they appear."""
    return tuple(dict.fromkeys(p.fragment for p in definition.phases if p.fragment))


def _nodes(fragment: Fragment) -> list[Phase]:
    """The fragment's phases as composed: blocks collapsed, other tasks as-is."""
    in_block = {task_phase_id(fragment.domain, m): b for b in fragment.blocks for m in b.members}
    nodes: list[Phase] = []
    emitted: set[str] = set()
    for task in fragment.tasks:
        block = in_block.get(task.id)
        if block is None:
            nodes.append(task)
            continue
        if block.id in emitted:
            continue
        emitted.add(block.id)
        nodes.append(
            block_phase(
                phase_id=task_phase_id(fragment.domain, block.id),
                title=block.title,
                members=[fragment.task(m) for m in block.members],
                solves_for=block.solves_for,
                tolerance=block.tolerance,
                max_iterations=block.max_iterations,
            )
        )
    return nodes


def compose(base: FlowDefinition, fragments: Sequence[Fragment]) -> ComposedFlow:
    """``base`` with ``fragments``' tasks wired in. Raises :class:`CompositionError`.

    Composing an already-composed flow with more fragments is allowed (the
    existing tasks are kept); composing the same fragment twice is not.
    """
    already = set(fragment_ids(base))
    chosen = list(fragments)
    ids = [f.id for f in chosen]
    dupes = sorted({i for i in ids if ids.count(i) > 1} | (already & set(ids)))
    if dupes:
        raise CompositionError(f"fragment(s) composed twice: {', '.join(dupes)}")
    present = already | set(ids)
    for fragment in chosen:
        missing = [r for r in fragment.requires if r not in present]
        if missing:
            raise CompositionError(
                f"fragment {fragment.id} requires {', '.join(missing)}; add it as well"
            )
    if not chosen:
        return ComposedFlow(definition=base, fragments=tuple(sorted(already)))

    try:
        base_graph = build_graph(base.phases)
    except GraphError as exc:
        raise CompositionError(f"the base flow is not a valid graph: {exc}") from exc
    stages = {p.id: p for p in base.phases if not p.is_task}
    nodes: list[Phase] = [p for p in base.phases if p.is_task]
    for fragment in chosen:
        for node in _nodes(fragment):
            if node.stage not in stages:
                raise CompositionError(
                    f"task {node.id} ({fragment.id}) belongs to stage '{node.stage}', which "
                    f"flow {base.id} does not have (stages: {', '.join(stages)})"
                )
            nodes.append(node)

    producers: dict[str, str] = {}
    for node in nodes:
        for ref in outputs_of(node):
            key = ref_key(ref)
            if key in producers:
                raise CompositionError(
                    f"'{ref}' is written by both {producers[key]} and {node.id}; "
                    "an item has one writer"
                )
            producers[key] = node.id

    derived: dict[str, tuple[str, ...]] = {}
    external: dict[str, list[str]] = {}
    refs: dict[str, str] = {}
    composed_nodes: list[Phase] = []
    for node in nodes:
        stage_parents = tuple(
            p for p in base_graph.parents[node.stage] if not by_id_is_task(base, p)
        )
        data_parents: list[str] = []
        # A check's limit is read like an input: a changed limit re-checks,
        # and an unrecorded one is supplied from outside the flow.
        limits = [f"design_value:{c.limit}" for c in node.checks if c.limit]
        for ref in dict.fromkeys([*node.inputs, *limits]):
            key = ref_key(ref)
            source = producers.get(key)
            if source is None:
                external.setdefault(key, []).append(node.id)
                refs[key] = ref
            elif source != node.id:
                data_parents.append(source)
        deps = tuple(dict.fromkeys([*stage_parents, *data_parents]))
        derived[node.id] = deps
        stage = stages[node.stage]
        composed_nodes.append(
            replace(node, depends_on=deps, condition=node.condition or stage.condition)
        )

    tasks_by_stage: dict[str, list[str]] = {}
    for node in composed_nodes:
        tasks_by_stage.setdefault(node.stage, []).append(node.id)
    phases: list[Phase] = []
    task_lookup = {n.id: n for n in composed_nodes}
    for phase in base.phases:
        if phase.is_task:
            continue  # re-placed with its stage below
        attached = tasks_by_stage.get(phase.id, [])
        for tid in _topological(attached, task_lookup):
            phases.append(task_lookup[tid])
        parents = tuple(p for p in base_graph.parents[phase.id] if p not in task_lookup)
        phases.append(replace(phase, depends_on=tuple(dict.fromkeys([*parents, *attached]))))
    definition = FlowDefinition(id=base.id, name=base.name, phases=tuple(phases))
    try:
        build_graph(definition.phases)
    except GraphError as exc:
        raise CompositionError(
            f"{exc}. A task reads an item written in a later stage, so its stage would "
            "wait for itself; move the task, or the item it reads, to the right stage"
        ) from exc
    fragment_order = [*fragment_ids(base), *ids]
    composed = ComposedFlow(
        definition=definition,
        fragments=tuple(dict.fromkeys(fragment_order)),
        external_inputs=tuple(
            ExternalInput(ref=refs[k], item_key=k, consumers=tuple(v)) for k, v in external.items()
        ),
        task_dependencies=derived,
    )
    logger.info(
        "flow_composed",
        flow=base.id,
        fragments=list(composed.fragments),
        tasks=len(composed_nodes),
        external_inputs=len(composed.external_inputs),
    )
    return composed


def by_id_is_task(definition: FlowDefinition, phase_id: str) -> bool:
    return any(p.id == phase_id and p.is_task for p in definition.phases)


def _topological(ids: Sequence[str], lookup: dict[str, Phase]) -> list[str]:
    """``ids`` in dependency order among themselves, ties in declared order."""
    pending = list(ids)
    members = set(ids)
    out: list[str] = []
    while pending:
        for tid in pending:
            needs = [d for d in (lookup[tid].depends_on or ()) if d in members and d not in out]
            if not needs:
                out.append(tid)
                pending.remove(tid)
                break
        else:
            # A cycle among tasks of one stage; build_graph names it.
            out.extend(pending)
            break
    return out
