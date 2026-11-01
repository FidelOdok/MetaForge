"""The approved flow, as plain data a workflow can be handed (FORGE-401).

A Temporal workflow is replayed from its history, so its *input* has to be
data — not a lookup into a module that might have changed between the run
starting and the replay. Today's executor calls ``get_flow(id)`` at run time,
which means editing ``spec.py`` silently changes what an in-flight run is
doing and makes a completed run unreplayable.

So the flow is frozen at approval: converted to these structures, hashed, and
carried in the workflow input. ``template_id`` and ``version`` say which
template it came from; ``content_hash`` says whether the content actually
matches that template, which is the part that catches a flow edited after
approval.

Everything here is deliberately dumb: dataclasses of strings and bools, no
behaviour, no imports beyond the standard library. It is passed through
Temporal's sandbox unmodified and serialised into workflow history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

__all__ = [
    "FrozenCheck",
    "FrozenCoupling",
    "FrozenFlow",
    "FrozenGate",
    "FrozenPhase",
    "FrozenSlot",
    "freeze_flow",
]


@dataclass
class FrozenGate:
    """A gate, as the workflow sees it."""

    name: str
    auto_approve: bool = False
    criteria: list[str] = field(default_factory=list)
    enforce_constraints: bool = False
    gate_id: str | None = None


@dataclass
class FrozenSlot:
    """A deliverable slot and the item key it is frozen to (FORGE-524)."""

    item_type: str
    name: str
    item_key: str = ""


@dataclass
class FrozenCheck:
    """A task's acceptance check (composable flows)."""

    id: str
    value: str
    op: str
    threshold: float = 0.0
    unit: str = ""
    limit: str = ""


@dataclass
class FrozenCoupling:
    """A block phase's convergence contract (composable flows)."""

    members: list[str] = field(default_factory=list)
    solves_for: list[str] = field(default_factory=list)
    tolerance: float = 0.01
    max_iterations: int = 20


@dataclass
class FrozenPhase:
    """A phase, as the workflow sees it."""

    id: str
    title: str
    objective: str
    expected_artifacts: list[str] = field(default_factory=list)
    required_deliverables: list[str] = field(default_factory=list)
    enforce_deliverables: bool = True
    gate: FrozenGate | None = None
    disciplines: list[str] = field(default_factory=list)
    #: FORGE-477: ``"provider:model"`` override for this phase, or ``None``.
    model: str | None = None
    #: FORGE-524: the items this phase writes. Empty for flows frozen before
    #: slots existed and for template runs, whose slots are derived at run time.
    slots: list[FrozenSlot] = field(default_factory=list)
    #: FORGE-539: explicit dependencies (``None`` = the phase before), a run
    #: condition over the flow's facts, and the outcome the phase establishes.
    #: All absent from the hash when unset, so earlier approvals still verify.
    depends_on: list[str] | None = None
    condition: str | None = None
    outcome: str = ""
    #: Composable flows: the task contract. All absent from the hash when
    #: unset, so every flow approved before fragments existed still verifies.
    fragment: str = ""
    stage: str = ""
    inputs: list[str] = field(default_factory=list)
    checks: list[FrozenCheck] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    coupling: FrozenCoupling | None = None


#: FrozenPhase fields left out of the hash when falsy, so versions approved
#: before a field existed keep the hash they were approved with.
_OPTIONAL_HASH_FIELDS = (
    "model",
    "slots",
    "depends_on",
    "condition",
    "outcome",
    "fragment",
    "stage",
    "inputs",
    "checks",
    "capabilities",
    "coupling",
)


@dataclass
class FrozenFlow:
    """An approved flow, pinned for the life of one run."""

    template_id: str
    name: str
    phases: list[FrozenPhase] = field(default_factory=list)

    #: Template version this was tailored from. ``"builtin"`` until FORGE-397
    #: moves the built-in flows into versioned template files.
    version: str = "unversioned"

    #: SHA-256 over the phase content. Set by :func:`freeze_flow`; recomputed
    #: and compared by :meth:`verify`, so a flow edited between approval and
    #: start is caught rather than quietly run.
    content_hash: str = ""

    #: FORGE-491: the proposal's context (manufacturing route and capabilities,
    #: target maturity, loads and use, budget, requirements) rendered once as a
    #: stable text block. Part of the hashed content, so it cannot change after
    #: approval, and carried to every phase brain. Optional with a default so
    #: flows frozen before this field, and in-flight workflow inputs, still load.
    context: str = ""
    #: FORGE-539: the facts phase conditions are evaluated against
    #: (``route``, ``target_maturity``, ``loads_known``, ...). Hashed when
    #: present, so a condition cannot be steered by editing facts later.
    facts: dict[str, str] = field(default_factory=dict)

    def compute_hash(self) -> str:
        rows = []
        for p in self.phases:
            row = asdict(p)
            # Absent when unset (FORGE-477 model, FORGE-524 slots, FORGE-539
            # graph fields, the composable-flow task contract), so flows frozen
            # before each field keep the hash they were approved with. An
            # explicit empty ``depends_on`` is a root and stays in the hash.
            for name in _OPTIONAL_HASH_FIELDS:
                if name == "depends_on":
                    if row.get(name) is None:
                        row.pop(name, None)
                elif not row.get(name):
                    row.pop(name, None)
            rows.append(row)
        # Absent when empty, so flows frozen before FORGE-491 keep their hash.
        body: object = {"phases": rows, "context": self.context} if self.context else rows
        if self.facts:
            body = {"phases": rows, "context": self.context, "facts": dict(self.facts)}
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify(self) -> None:
        """Raise if the content no longer matches the hash it was approved with."""
        if not self.content_hash:
            raise ValueError(
                f"flow '{self.template_id}' carries no content_hash; it was not frozen "
                "at approval and cannot be trusted to be the flow a human agreed to"
            )
        actual = self.compute_hash()
        if actual != self.content_hash:
            raise ValueError(
                f"flow '{self.template_id}' content does not match the hash it was "
                f"approved with (approved {self.content_hash[:12]}, got {actual[:12]}). "
                "Something edited the flow after approval."
            )


def _freeze_coupling(coupling: object) -> FrozenCoupling | None:
    if coupling is None:
        return None
    return FrozenCoupling(
        members=[str(m) for m in getattr(coupling, "members", ())],
        solves_for=[str(v) for v in getattr(coupling, "solves_for", ())],
        tolerance=float(getattr(coupling, "tolerance", 0.01)),
        max_iterations=int(getattr(coupling, "max_iterations", 20)),
    )


def freeze_flow(
    definition: object,
    *,
    version: str = "builtin",
    context: str = "",
    facts: dict[str, str] | None = None,
) -> FrozenFlow:
    """Convert a :class:`~orchestrator.design_flow.spec.FlowDefinition`.

    Takes ``object`` rather than the real type on purpose: this module is
    passed through the Temporal sandbox, and importing ``spec`` here would
    drag the whole flow catalogue in with it.
    """
    phases: list[FrozenPhase] = []
    for phase in getattr(definition, "phases", ()):
        gate = getattr(phase, "gate", None)
        phases.append(
            FrozenPhase(
                id=phase.id,
                title=phase.title,
                objective=phase.objective,
                expected_artifacts=list(getattr(phase, "expected_artifacts", ()) or ()),
                required_deliverables=list(getattr(phase, "required_deliverables", ()) or ()),
                enforce_deliverables=bool(getattr(phase, "enforce_deliverables", True)),
                disciplines=list(getattr(phase, "disciplines", ()) or ()),
                model=getattr(phase, "model", None) or None,
                depends_on=(
                    None
                    if getattr(phase, "depends_on", None) is None
                    else [str(d) for d in phase.depends_on]
                ),
                condition=getattr(phase, "condition", None) or None,
                outcome=str(getattr(phase, "outcome", "") or ""),
                fragment=str(getattr(phase, "fragment", "") or ""),
                stage=str(getattr(phase, "stage", "") or ""),
                inputs=[str(i) for i in (getattr(phase, "inputs", ()) or ())],
                checks=[
                    FrozenCheck(
                        id=str(c.id),
                        value=str(c.value),
                        op=str(c.op),
                        threshold=float(c.threshold),
                        unit=str(getattr(c, "unit", "") or ""),
                        limit=str(getattr(c, "limit", "") or ""),
                    )
                    for c in (getattr(phase, "checks", ()) or ())
                ],
                capabilities=[str(c) for c in (getattr(phase, "capabilities", ()) or ())],
                coupling=_freeze_coupling(getattr(phase, "coupling", None)),
                slots=[
                    FrozenSlot(
                        item_type=str(slot.item_type),
                        name=str(slot.name),
                        item_key=str(getattr(slot, "item_key", "") or ""),
                    )
                    for slot in (getattr(phase, "slots", ()) or ())
                ],
                gate=(
                    None
                    if gate is None
                    else FrozenGate(
                        name=gate.name,
                        auto_approve=bool(getattr(gate, "auto_approve", False)),
                        criteria=list(getattr(gate, "criteria", ()) or ()),
                        enforce_constraints=bool(getattr(gate, "enforce_constraints", False)),
                        gate_id=getattr(gate, "gate_id", None),
                    )
                ),
            )
        )
    flow = FrozenFlow(
        template_id=getattr(definition, "id", "unknown"),
        name=getattr(definition, "name", ""),
        phases=phases,
        version=version,
        context=context,
        facts={str(k): str(v) for k, v in (facts or {}).items()},
    )
    flow.content_hash = flow.compute_hash()
    return flow
