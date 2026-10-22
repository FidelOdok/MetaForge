"""Coupled blocks: feedback loops in an acyclic flow (composable flows).

Battery capacity sets mass, mass sets flight power, flight power sets battery
capacity. A cycle cannot be an edge of the flow graph -- a cyclic graph has
no execution order -- so the mutually dependent tasks are declared as a
*block* (:class:`~orchestrator.design_flow.fragments.Block`) and compiled
into **one** phase: the outer graph stays a DAG, and the block's own work is
an iteration to a fixed point.

Two things live here, both pure:

* :func:`block_phase` turns a block's member tasks into that one phase: the
  union of their outputs and checks, the inputs that come from outside the
  block, and a :class:`~orchestrator.design_flow.spec.Coupling` contract.
* :func:`solve` and :func:`judge` are the convergence rules. ``solve`` runs a
  deterministic fixed-point iteration over plain functions (what a test or a
  closed-form sizing step uses); ``judge`` decides from the recorded
  iteration history whether a block converged, which is what the stage gate
  reads for work an agent did. Both say the same thing about the same
  history.

Non-convergence is never resolved here. Planner §35: downstream evidence
that conflicts with a requirement SHALL NOT weaken the requirement. A block
that does not settle returns ``CONVERGED = False`` with the reason, and the
gate turns it into an engineering decision for a person.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from orchestrator.design_flow.contract import ref_key
from orchestrator.design_flow.spec import Coupling, Phase

__all__ = [
    "ConvergenceStatus",
    "ConvergenceVerdict",
    "block_phase",
    "judge",
    "solve",
]


class ConvergenceStatus(StrEnum):
    CONVERGED = "CONVERGED"
    NOT_CONVERGED = "NOT_CONVERGED"
    DIVERGED = "DIVERGED"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class ConvergenceVerdict:
    status: ConvergenceStatus
    iterations: int
    values: Mapping[str, float] = field(default_factory=dict)
    #: The largest relative change between the last two iterations, per value.
    changes: Mapping[str, float] = field(default_factory=dict)
    reason: str = ""

    @property
    def converged(self) -> bool:
        return self.status is ConvergenceStatus.CONVERGED

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "iterations": self.iterations,
            "values": dict(self.values),
            "changes": dict(self.changes),
            "reason": self.reason,
        }


def block_phase(
    *,
    phase_id: str,
    title: str,
    members: Sequence[Phase],
    solves_for: Sequence[str],
    tolerance: float,
    max_iterations: int,
) -> Phase:
    """One phase standing for ``members``, solved together to convergence.

    Inputs a member reads from another member are internal to the loop and
    dropped; the rest are the block's inputs. Outputs, checks, records and
    capabilities are the members' union, so staleness and the stage gate see
    exactly what the members would have produced.
    """
    if not members:
        raise ValueError("a block needs members")
    internal = {s.item_key or ref_key(f"{s.item_type}:{s.name}") for m in members for s in m.slots}
    inputs = [ref for m in members for ref in m.inputs if ref_key(ref) not in internal]
    steps = "\n".join(f"  {i + 1}. {m.title}: {m.objective}" for i, m in enumerate(members))
    objective = (
        "These tasks depend on each other, so solve them together as one loop. Iterate:\n"
        f"{steps}\n"
        f"After each pass record every one of {', '.join(solves_for)} as a design value "
        "(the next revision of the same item) and note the iteration number in its source. "
        f"Stop when each changes by less than {tolerance:.2%} from the previous pass, or after "
        f"{max_iterations} passes. If they have not settled by then, say so plainly: do not "
        "relax a requirement or a limit to make the loop close."
    )
    return Phase(
        id=phase_id,
        title=title,
        objective=objective,
        expected_artifacts=tuple(dict.fromkeys(a for m in members for a in m.expected_artifacts)),
        required_deliverables=tuple(
            dict.fromkeys(d for m in members for d in m.required_deliverables)
        ),
        disciplines=tuple(dict.fromkeys(d for m in members for d in m.disciplines)),
        slots=tuple(s for m in members for s in m.slots),
        outcome=f"{', '.join(solves_for)} closed to within {tolerance:.2%}",
        fragment=members[0].fragment,
        stage=members[0].stage,
        inputs=tuple(dict.fromkeys(inputs)),
        checks=tuple(c for m in members for c in m.checks),
        capabilities=tuple(dict.fromkeys(c for m in members for c in m.capabilities)),
        coupling=Coupling(
            members=tuple(m.id for m in members),
            solves_for=tuple(solves_for),
            tolerance=tolerance,
            max_iterations=max_iterations,
        ),
    )


def _relative_change(previous: float, current: float) -> float:
    scale = max(abs(previous), abs(current))
    if scale == 0:
        return 0.0
    return abs(current - previous) / scale


def judge(history: Sequence[Mapping[str, float]], coupling: Coupling) -> ConvergenceVerdict:
    """Whether a block's recorded iterations converged.

    ``history`` is one mapping per iteration, oldest first, of the
    ``solves_for`` values recorded on that pass. Converged when the last two
    passes differ by less than ``tolerance`` (relative) in every value. Fewer
    than two passes, or a value missing from a pass, is NO_DATA; a value that
    is not finite is DIVERGED; running past ``max_iterations`` without
    settling is NOT_CONVERGED.
    """
    names = tuple(coupling.solves_for)
    complete = [h for h in history if all(n in h for n in names)]
    if len(complete) < 2:
        missing = [n for n in names if not history or n not in history[-1]]
        return ConvergenceVerdict(
            ConvergenceStatus.NO_DATA,
            iterations=len(complete),
            reason=(
                f"fewer than two complete passes recorded (missing: {', '.join(missing)})"
                if missing
                else "fewer than two passes recorded, so convergence cannot be judged"
            ),
        )
    last, previous = complete[-1], complete[-2]
    if any(not math.isfinite(float(last[n])) for n in names):
        return ConvergenceVerdict(
            ConvergenceStatus.DIVERGED,
            iterations=len(complete),
            values={n: float(last[n]) for n in names},
            reason="a coupled value is no longer finite",
        )
    changes = {n: _relative_change(float(previous[n]), float(last[n])) for n in names}
    values = {n: float(last[n]) for n in names}
    if all(c < coupling.tolerance for c in changes.values()):
        return ConvergenceVerdict(
            ConvergenceStatus.CONVERGED, iterations=len(complete), values=values, changes=changes
        )
    worst = max(changes, key=changes.__getitem__)
    if len(complete) >= coupling.max_iterations:
        return ConvergenceVerdict(
            ConvergenceStatus.NOT_CONVERGED,
            iterations=len(complete),
            values=values,
            changes=changes,
            reason=(
                f"did not settle in {coupling.max_iterations} passes: {worst} still changed "
                f"{changes[worst]:.2%} (tolerance {coupling.tolerance:.2%}); this needs an "
                "engineering decision (redesign, a controlled requirement change, or an "
                "accepted risk), not a looser loop"
            ),
        )
    return ConvergenceVerdict(
        ConvergenceStatus.NOT_CONVERGED,
        iterations=len(complete),
        values=values,
        changes=changes,
        reason=f"still iterating: {worst} changed {changes[worst]:.2%} on the last pass",
    )


#: One member step: reads the current values, returns the values it sets.
Step = Callable[[Mapping[str, float]], Mapping[str, float]]


def solve(
    steps: Sequence[Step],
    initial: Mapping[str, float],
    coupling: Coupling,
) -> tuple[ConvergenceVerdict, list[dict[str, float]]]:
    """Run ``steps`` in order, repeatedly, until :func:`judge` says converged.

    Deterministic Gauss-Seidel iteration: each step sees the values the
    previous steps of the same pass just set. Returns the verdict and the
    per-pass history of the ``solves_for`` values (what an agent would have
    recorded). Stops at ``max_iterations``, a non-finite value or a numeric
    overflow (both DIVERGED). Any other exception from a step is re-raised:
    a broken sizing function is a bug, not a convergence result.
    """
    values = dict(initial)
    history: list[dict[str, float]] = []
    verdict = judge(history, coupling)
    for _ in range(coupling.max_iterations):
        try:
            for step in steps:
                values.update({k: float(v) for k, v in step(values).items()})
        except OverflowError:
            # A loop running away overflows before it reaches infinity; that
            # is divergence, a result, not a broken step.
            return (
                ConvergenceVerdict(
                    ConvergenceStatus.DIVERGED,
                    iterations=len(history),
                    values={n: values[n] for n in coupling.solves_for if n in values},
                    reason="the coupled values grew without bound (numeric overflow); this "
                    "needs an engineering decision, not a looser loop",
                ),
                history,
            )
        history.append({n: values[n] for n in coupling.solves_for if n in values})
        verdict = judge(history, coupling)
        if verdict.status in (ConvergenceStatus.CONVERGED, ConvergenceStatus.DIVERGED):
            break
    return verdict, history
