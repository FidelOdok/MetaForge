"""What a tailored flow can never remove (FORGE-397).

A generated flow (FORGE-398) or an edited one (FORGE-399) is written by a
model or by a person in a hurry, and both will happily produce something that
runs beautifully and proves nothing. The obvious failure is a flow with no
release gate. The more common one is subtler: a gate that requires a
deliverable no earlier phase produces, so it can never pass — or a phase that
declares ``enforce_deliverables: false``, which turns a real gate into a
decorative one while still rendering as a gate on the canvas.

These are **server-enforced**. The validator runs before a flow is approved
and before a run starts, not as a lint somebody can choose to skip. A rule
that only fires in a linter is a rule with nothing checking it.

Each violation names the rule that produced it. "Invalid flow" sends whoever
reads it hunting; "no-pass-without-data: phase 'simulation' has a gate but
declares no required deliverables" tells them what to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "RELEASE_GATE_MARKERS",
    "FlowInvariantError",
    "Violation",
    "ValidationResult",
    "validate_flow",
]

#: A release gate is recognised by name rather than by a flag, because the
#: flows that exist name it in prose and adding a flag would mean editing
#: every template to satisfy a rule about the templates.
RELEASE_GATE_MARKERS: tuple[str, ...] = ("release", "sign-off", "signoff", "acceptance")


@dataclass(frozen=True)
class Violation:
    """One broken rule, in the words of whoever has to fix it."""

    rule: str
    message: str
    phase_id: str | None = None

    def __str__(self) -> str:
        where = f" (phase '{self.phase_id}')" if self.phase_id else ""
        return f"{self.rule}: {self.message}{where}"


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def raise_if_invalid(self, flow_id: str) -> None:
        if self.ok:
            return
        raise FlowInvariantError(flow_id, self.violations)


class FlowInvariantError(ValueError):
    """A flow broke a rule that exists to stop it lying."""

    def __init__(self, flow_id: str, violations: list[Violation]) -> None:
        self.flow_id = flow_id
        self.violations = violations
        detail = "\n  - ".join(str(v) for v in violations)
        super().__init__(
            f"flow '{flow_id}' is not valid ({len(violations)} violation(s)):\n  - {detail}"
        )


# ── the rules ────────────────────────────────────────────────────────────


def _rule_has_phases(phases: list[Any]) -> list[Violation]:
    if not phases:
        return [Violation("has-phases", "a flow with no phases cannot produce anything")]
    return []


def _rule_unique_phase_ids(phases: list[Any]) -> list[Violation]:
    seen: set[str] = set()
    out: list[Violation] = []
    for phase in phases:
        if phase.id in seen:
            # Two phases sharing an id make every per-phase result ambiguous:
            # readiness, activity history, and the live view all key on it.
            out.append(
                Violation("unique-phase-ids", f"phase id '{phase.id}' appears more than once")
            )
        seen.add(phase.id)
    return out


def _rule_release_gate_exists(phases: list[Any]) -> list[Violation]:
    for phase in phases:
        gate = getattr(phase, "gate", None)
        if gate is None or getattr(gate, "auto_approve", False):
            continue
        name = (gate.name or "").lower()
        if any(marker in name for marker in RELEASE_GATE_MARKERS):
            return []
    return [
        Violation(
            "release-gate-exists",
            "no phase carries a human-answered release or sign-off gate, so the flow "
            "can run to completion without anyone approving the result",
        )
    ]


def _rule_gates_require_evidence(phases: list[Any]) -> list[Violation]:
    """A gate with nothing required of it cannot fail on missing data.

    This is the no-pass-without-data rule. A gate whose phase declares no
    required deliverables asks a human to approve whatever happened, with the
    system contributing nothing — and the human has no way to tell an empty
    phase from a complete one.
    """
    out: list[Violation] = []
    for phase in phases:
        gate = getattr(phase, "gate", None)
        if gate is None or getattr(gate, "auto_approve", False):
            continue
        if not getattr(phase, "required_deliverables", ()):
            out.append(
                Violation(
                    "no-pass-without-data",
                    f"gate '{gate.name}' has no required deliverables, so it cannot "
                    "distinguish a phase that produced nothing from one that worked",
                    phase.id,
                )
            )
    return out


def _rule_enforcement_not_disabled_at_a_gate(phases: list[Any]) -> list[Violation]:
    """``enforce_deliverables: false`` under a gate is a decorative gate.

    It renders as a gate, a human answers it, and the deliverable check that
    would have blocked it never runs. That is worse than having no gate,
    because the approval is now evidence that somebody looked.
    """
    out: list[Violation] = []
    for phase in phases:
        gate = getattr(phase, "gate", None)
        if gate is None or getattr(gate, "auto_approve", False):
            continue
        if getattr(phase, "required_deliverables", ()) and not getattr(
            phase, "enforce_deliverables", True
        ):
            out.append(
                Violation(
                    "gates-enforce-what-they-require",
                    f"gate '{gate.name}' lists required deliverables but the phase sets "
                    "enforce_deliverables=false, so they are never checked",
                    phase.id,
                )
            )
    return out


def _rule_deliverables_are_producible(phases: list[Any]) -> list[Violation]:
    """A gate cannot require what no earlier phase produces.

    The dependency error the ticket names. A flow with
    ``required_deliverables: [simulation_result]`` on a phase that runs before
    any simulation is not a strict flow — it is a flow that always fails, and
    it fails at the gate rather than at the point somebody could have noticed
    while writing it.
    """
    out: list[Violation] = []
    producible: set[str] = set()
    for phase in phases:
        # A phase's own expected artifacts count toward its own gate: the
        # phase runs before its gate is evaluated.
        producible.update(getattr(phase, "expected_artifacts", ()) or ())
        for required in getattr(phase, "required_deliverables", ()) or ():
            if required not in producible:
                out.append(
                    Violation(
                        "deliverable-is-producible",
                        f"requires '{required}', which no phase up to and including this "
                        "one lists in expected_artifacts — the gate can never pass",
                        phase.id,
                    )
                )
    return out


def _rule_requirements_are_verifiable(phases: list[Any]) -> list[Violation]:
    """Requirements must end up verified somewhere in the flow.

    A flow that records requirements and never reaches a verification phase
    produces a twin full of claims nothing checks — which reads, on every
    dashboard, exactly like a product that passed.
    """
    produces_requirements = any(
        "constraint_set" in (getattr(p, "expected_artifacts", ()) or ())
        or "prd" in (getattr(p, "expected_artifacts", ()) or ())
        for p in phases
    )
    if not produces_requirements:
        return []
    verifies = any(
        artifact in (getattr(p, "expected_artifacts", ()) or ())
        for p in phases
        for artifact in ("simulation_result", "test_plan", "verification_report")
    )
    if verifies:
        return []
    return [
        Violation(
            "requirements-are-verified",
            "the flow records requirements but no phase produces a simulation_result, "
            "test_plan or verification_report, so nothing ever checks them",
        )
    ]


_RULES = (
    _rule_has_phases,
    _rule_unique_phase_ids,
    _rule_release_gate_exists,
    _rule_gates_require_evidence,
    _rule_enforcement_not_disabled_at_a_gate,
    _rule_deliverables_are_producible,
    _rule_requirements_are_verifiable,
)


def validate_flow(definition: Any) -> ValidationResult:
    """Check a flow against every invariant. Reports all of them, not the first.

    Returning the full list matters: a model fixing one violation at a time
    needs three round trips to learn what a single response could have told
    it, and a person editing on the canvas wants the whole list under the
    save button.
    """
    phases = list(getattr(definition, "phases", ()) or ())
    violations: list[Violation] = []
    for rule in _RULES:
        violations.extend(rule(phases))
    result = ValidationResult(violations=violations)
    if not result.ok:
        logger.info(
            "flow_invariants_violated",
            flow=getattr(definition, "id", "?"),
            count=len(violations),
            rules=sorted({v.rule for v in violations}),
        )
    return result
