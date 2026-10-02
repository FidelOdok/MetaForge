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
    "FrozenFlow",
    "FrozenGate",
    "FrozenPhase",
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

    def compute_hash(self) -> str:
        rows = []
        for p in self.phases:
            row = asdict(p)
            # Absent when unset, so flows frozen before FORGE-477 keep the
            # hash they were approved with.
            if row.get("model") is None:
                row.pop("model", None)
            rows.append(row)
        # Absent when empty, so flows frozen before FORGE-491 keep their hash.
        body: object = {"phases": rows, "context": self.context} if self.context else rows
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


def freeze_flow(definition: object, *, version: str = "builtin", context: str = "") -> FrozenFlow:
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
    )
    flow.content_hash = flow.compute_hash()
    return flow
