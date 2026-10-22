"""Domain fragments: the specialised work of one domain (composable flows).

The spine (``templates/spine/``) is the design process every product shares.
A fragment is the technical work of one domain -- thermal, electronics,
aircraft -- as a small graph of **tasks**, each attached to a spine stage
and each declaring the task contract (:mod:`orchestrator.design_flow.contract`):
what it reads, what it writes, how success is checked and which tools it
needs. A product flow is the spine plus the fragments its intent names
(:mod:`orchestrator.design_flow.compose`).

Fragments live in ``templates/fragments/<id>.yaml`` with an explicit version,
exactly like templates, and are validated when loaded: a fragment that is
refused here can never reach a proposal.

Feedback loops (battery capacity -> mass -> power -> battery capacity) are
declared as **blocks** (:mod:`orchestrator.design_flow.coupling`): their
members may depend on each other, and the block as a whole is one node of
the flow graph, so the graph stays acyclic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from orchestrator.design_flow.contract import (
    ContractError,
    checks_from,
    outputs_of,
    parse_ref,
    ref_key,
    slots_from_outputs,
)
from orchestrator.design_flow.spec import Phase
from orchestrator.design_flow.templates import TEMPLATE_DIR, load_spine

logger = structlog.get_logger(__name__)

__all__ = [
    "FRAGMENT_DIR",
    "Block",
    "Fragment",
    "FragmentError",
    "fragment_from_mapping",
    "load_fragment",
    "load_fragments",
    "task_phase_id",
]

FRAGMENT_DIR = TEMPLATE_DIR / "fragments"

#: The spine fragments attach to. One spine today; a fragment names its own
#: when there is more than one.
DEFAULT_SPINE = "spine_v1"


class FragmentError(ValueError):
    """A fragment is not well formed. The message names the fragment and the rule."""


@dataclass(frozen=True)
class Block:
    """Mutually dependent tasks solved to convergence (a feedback loop).

    ``members`` are task ids of the same fragment and the same stage;
    ``solves_for`` are the design values the loop closes on; it stops when
    each changes by less than ``tolerance`` (relative) between iterations, or
    raises an engineering decision after ``max_iterations``.
    """

    id: str
    stage: str
    members: tuple[str, ...]
    solves_for: tuple[str, ...]
    tolerance: float = 0.01
    max_iterations: int = 20
    title: str = ""


@dataclass(frozen=True)
class Fragment:
    """One domain's tasks, ready to compose into a spine."""

    id: str
    version: str
    domain: str
    label: str
    description: str
    spine: str
    tasks: tuple[Phase, ...]
    blocks: tuple[Block, ...] = ()
    #: Fragments this one needs composed alongside it (their outputs feed it).
    requires: tuple[str, ...] = ()
    #: Domain words in an intent that suggest this fragment ("thermal", "cooling").
    keywords: tuple[str, ...] = ()
    source: Path | None = field(default=None, compare=False)

    def task(self, task_id: str) -> Phase:
        for task in self.tasks:
            if task.id == task_phase_id(self.domain, task_id) or task.id == task_id:
                return task
        raise KeyError(task_id)

    def outputs(self) -> dict[str, str]:
        """Every item key this fragment writes -> the task phase id writing it."""
        return {ref_key(ref): task.id for task in self.tasks for ref in outputs_of(task)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "domain": self.domain,
            "label": self.label,
            "description": self.description,
            "spine": self.spine,
            "requires": list(self.requires),
            "keywords": list(self.keywords),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "stage": t.stage,
                    "inputs": list(t.inputs),
                    "outputs": list(outputs_of(t)),
                    "checks": [c.describe() for c in t.checks],
                    "capabilities": list(t.capabilities),
                }
                for t in self.tasks
            ],
            "blocks": [
                {
                    "id": b.id,
                    "stage": b.stage,
                    "members": list(b.members),
                    "solves_for": list(b.solves_for),
                    "tolerance": b.tolerance,
                    "max_iterations": b.max_iterations,
                }
                for b in self.blocks
            ],
        }


def task_phase_id(domain: str, task_id: str) -> str:
    """The phase id a task gets in a composed flow: ``<domain>.<task>``."""
    return f"{domain}.{task_id}"


def _strings(raw: Any) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in raw or () if str(v).strip())


def _task_from(
    raw: Mapping[str, Any], *, fragment_id: str, domain: str, disciplines: Sequence[str]
) -> Phase:
    tid = str(raw.get("id") or "").strip()
    if not tid or "." in tid:
        raise FragmentError(f"fragment {fragment_id}: every task needs an 'id' without dots")
    where = f"fragment {fragment_id} task {tid}"
    try:
        inputs = _strings(raw.get("inputs"))
        for ref in inputs:
            parse_ref(ref)
        outputs = _strings(raw.get("outputs"))
        if not outputs:
            raise FragmentError(f"{where}: a task must declare at least one output")
        slots = slots_from_outputs(outputs)
        checks = checks_from(raw.get("checks"))
    except ContractError as exc:
        raise FragmentError(f"{where}: {exc}") from exc
    # Records (a design_decision, a simulation_result) are not revisioned
    # items, so they are not outputs; a task that must leave one says so here
    # and the stage gate requires the type, the way phase deliverables work.
    from twin_core.items.registry import classify

    records = _strings(raw.get("records"))
    for record in records:
        if classify(record) is None:
            raise FragmentError(f"{where}: record type '{record}' is not a twin type")
    types = tuple(dict.fromkeys([*(s.item_type for s in slots), *records]))
    objective = str(raw.get("objective") or "").strip()
    if not objective:
        raise FragmentError(f"{where}: a task needs an 'objective'")
    return Phase(
        id=task_phase_id(domain, tid),
        title=str(raw.get("title") or tid).strip(),
        objective=objective,
        expected_artifacts=types,
        required_deliverables=types,
        disciplines=tuple(raw.get("disciplines") or disciplines),
        slots=slots,
        outcome=str(raw.get("outcome") or "").strip(),
        fragment=fragment_id,
        stage=str(raw.get("stage") or "").strip(),
        inputs=inputs,
        checks=checks,
        capabilities=_strings(raw.get("capabilities")),
    )


def _block_from(raw: Mapping[str, Any], *, fragment_id: str) -> Block:
    bid = str(raw.get("id") or "").strip()
    if not bid:
        raise FragmentError(f"fragment {fragment_id}: every block needs an 'id'")
    try:
        tolerance = float(raw.get("tolerance", 0.01))
        max_iterations = int(raw.get("max_iterations", 20))
    except (TypeError, ValueError) as exc:
        raise FragmentError(f"fragment {fragment_id} block {bid}: {exc}") from exc
    if not 0 < tolerance < 1:
        raise FragmentError(f"fragment {fragment_id} block {bid}: tolerance must be in (0, 1)")
    if not 1 < max_iterations <= 200:
        raise FragmentError(f"fragment {fragment_id} block {bid}: max_iterations must be 2..200")
    return Block(
        id=bid,
        stage=str(raw.get("stage") or "").strip(),
        members=_strings(raw.get("members")),
        solves_for=_strings(raw.get("solves_for")),
        tolerance=tolerance,
        max_iterations=max_iterations,
        title=str(raw.get("title") or bid).strip(),
    )


def _validate(fragment: Fragment) -> None:
    fid = fragment.id
    stages = set(load_spine(fragment.spine).phases)
    ids = [t.id for t in fragment.tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise FragmentError(f"fragment {fid}: duplicate task id(s) {', '.join(dupes)}")
    produced: dict[str, str] = {}
    for task in fragment.tasks:
        if task.stage not in stages:
            raise FragmentError(
                f"fragment {fid} task {task.id}: stage '{task.stage}' is not a "
                f"{fragment.spine} stage ({', '.join(sorted(stages))})"
            )
        for ref in outputs_of(task):
            key = ref_key(ref)
            if key in produced:
                raise FragmentError(
                    f"fragment {fid}: '{ref}' is written by both {produced[key]} and {task.id}"
                )
            produced[key] = task.id
    # A check reads a design value; within the fragment it must be one a task
    # writes (an input from another fragment is checked where it is written).
    for task in fragment.tasks:
        written = {s.name for s in task.slots if s.item_type == "design_value"}
        for check in task.checks:
            if check.value not in written:
                raise FragmentError(
                    f"fragment {fid} task {task.id}: check '{check.id}' reads "
                    f"'{check.value}', which the task does not write"
                )
    members_of: dict[str, str] = {}
    for block in fragment.blocks:
        if len(block.members) < 2:
            raise FragmentError(f"fragment {fid} block {block.id}: a block needs 2+ members")
        member_phases = []
        for member in block.members:
            pid = task_phase_id(fragment.domain, member)
            if pid not in ids:
                raise FragmentError(f"fragment {fid} block {block.id}: unknown member '{member}'")
            if pid in members_of:
                raise FragmentError(
                    f"fragment {fid}: task {pid} is in blocks {members_of[pid]} and {block.id}"
                )
            members_of[pid] = block.id
            member_phases.append(fragment.task(member))
        if any(p.stage != block.stage for p in member_phases):
            raise FragmentError(f"fragment {fid} block {block.id}: members must share its stage")
        written = {s.name for p in member_phases for s in p.slots if s.item_type == "design_value"}
        missing = [v for v in block.solves_for if v not in written]
        if not block.solves_for or missing:
            raise FragmentError(
                f"fragment {fid} block {block.id}: solves_for must name design values its "
                f"members write (not written: {', '.join(missing) or 'none declared'})"
            )
    _require_acyclic(fragment, members_of, produced)


def _require_acyclic(
    fragment: Fragment, members_of: Mapping[str, str], produced: Mapping[str, str]
) -> None:
    """Tasks may form cycles only inside a block; collapsed, the graph is a DAG."""

    def node(pid: str) -> str:
        return f"block:{members_of[pid]}" if pid in members_of else pid

    edges: dict[str, set[str]] = {node(t.id): set() for t in fragment.tasks}
    for task in fragment.tasks:
        for ref in task.inputs:
            source = produced.get(ref_key(ref))
            if source is not None and node(source) != node(task.id):
                edges[node(source)].add(node(task.id))
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        state[n] = 1
        for child in sorted(edges[n]):
            if state.get(child) == 1:
                cycle = " -> ".join([*path[path.index(child) :], child])
                raise FragmentError(
                    f"fragment {fragment.id}: dependency cycle {cycle}; put mutually "
                    "dependent tasks in a block"
                )
            if child not in state:
                visit(child, [*path, child])
        state[n] = 2

    for n in sorted(edges):
        if n not in state:
            visit(n, [n])


def fragment_from_mapping(raw: Mapping[str, Any], *, source: Path | None = None) -> Fragment:
    """Build and validate a fragment. Raises :class:`FragmentError`."""
    fid = str(raw.get("id") or "").strip()
    if not fid:
        raise FragmentError("a fragment needs an 'id'")
    version = str(raw.get("version") or "").strip()
    if not version:
        raise FragmentError(f"fragment {fid} has no 'version'")
    domain = str(raw.get("domain") or "").strip()
    if not domain or "." in domain:
        raise FragmentError(f"fragment {fid} needs a 'domain' without dots")
    disciplines = _strings(raw.get("disciplines"))
    tasks = tuple(
        _task_from(t, fragment_id=fid, domain=domain, disciplines=disciplines)
        for t in raw.get("tasks") or ()
    )
    if not tasks:
        raise FragmentError(f"fragment {fid} declares no tasks")
    fragment = Fragment(
        id=fid,
        version=version,
        domain=domain,
        label=str(raw.get("label") or fid).strip(),
        description=str(raw.get("description") or "").strip(),
        spine=str(raw.get("spine") or DEFAULT_SPINE).strip(),
        tasks=tasks,
        blocks=tuple(_block_from(b, fragment_id=fid) for b in raw.get("blocks") or ()),
        requires=_strings(raw.get("requires")),
        keywords=tuple(k.lower() for k in _strings(raw.get("keywords"))),
        source=source,
    )
    _validate(fragment)
    return fragment


def load_fragment(fragment_id: str, directory: Path | None = None) -> Fragment:
    path = (directory or FRAGMENT_DIR) / f"{fragment_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no fragment at {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise FragmentError(f"{path} must contain a mapping")
    if raw.get("id") != fragment_id:
        raise FragmentError(f"{path} declares id {raw.get('id')!r}, expected {fragment_id!r}")
    return fragment_from_mapping(raw, source=path)


@lru_cache(maxsize=1)
def load_fragments() -> dict[str, Fragment]:
    """Every fragment on disk, by id. Requirements between them must resolve."""
    loaded: dict[str, Fragment] = {}
    for path in sorted(FRAGMENT_DIR.glob("*.yaml")):
        fragment = load_fragment(path.stem)
        loaded[fragment.id] = fragment
    _require_known(loaded.values(), loaded)
    logger.info("flow_fragments_loaded", count=len(loaded), ids=sorted(loaded))
    return loaded


def _require_known(fragments: Iterable[Fragment], known: Mapping[str, Fragment]) -> None:
    for fragment in fragments:
        unknown = [r for r in fragment.requires if r not in known]
        if unknown:
            raise FragmentError(f"fragment {fragment.id} requires unknown {', '.join(unknown)}")
