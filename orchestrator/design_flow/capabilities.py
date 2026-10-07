"""Can this flow actually be done with the tools that exist? (FORGE-539)

A phase that must deliver a ``simulation_result`` needs a mesher, a solver
and a recorder. If the solver's container is down, the tool was never
registered, or the client's connection does not serve it, the phase cannot
pass its gate, and the run finds that out hours in, at the gate.

This module answers it before anything runs. For every deliverable a phase
requires or expects, it looks at the tools that produce that deliverable
and classifies the phase's coverage:

* FULL: every producing tool is registered, reachable and served
* PARTIAL: some are; the step may run with reduced fidelity or confidence
* UNAVAILABLE: none are
* UNKNOWN: no model-callable tool produces it at all (a platform handler
  does, or nothing does); the report says so instead of guessing

and turns every shortfall into a gap with a severity: whether it blocks the
step, degrades confidence, needs the user to do something (start a
container, connect with another profile) or needs a capability MetaForge
does not have. Each gap lists the workarounds that keep the requirement's
verification method intact, never one that quietly swaps physical evidence
for an opinion.

Pure: the caller supplies which tools produce what and which tools exist.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "CapabilityReport",
    "Coverage",
    "Gap",
    "GapSeverity",
    "NodeCoverage",
    "assess_capabilities",
]


class Coverage(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class GapSeverity(StrEnum):
    NON_BLOCKING = "NON_BLOCKING"
    DEGRADES_CONFIDENCE = "DEGRADES_CONFIDENCE"
    BLOCKS_STEP = "BLOCKS_STEP"
    REQUIRES_USER_ACTION = "REQUIRES_USER_ACTION"
    REQUIRES_NEW_CAPABILITY = "REQUIRES_NEW_CAPABILITY"


#: Ways to keep going when a deliverable cannot be produced, that do not
#: weaken what the requirement asks for. Shown to the person, not applied.
_WORKAROUNDS: dict[str, tuple[str, ...]] = {
    "simulation_result": (
        "a closed-form hand calculation recorded as a decision, where the geometry is a "
        "textbook case (lower confidence, still analysis)",
        "a test_plan deliverable as the alternative verification, if the requirement's "
        "verification method allows test",
        "defer the analysis phase until the solver is available",
    ),
    "cad_model": (
        "stateless CAD tools (cadquery.create_parametric / cadquery.execute_script) instead "
        "of a FreeCAD session",
        "import a STEP the user provides and commit it",
    ),
    "schematic": ("record the schematic the user provides as a document",),
    "bom": ("record component choices one at a time with the component tools",),
    "load_case": ("record the boundary conditions as a document by hand",),
}


@dataclass(frozen=True)
class Gap:
    phase_id: str
    capability: str
    severity: GapSeverity
    blocking: bool
    consequence: str
    missing_tools: tuple[str, ...] = ()
    unreachable_tools: tuple[str, ...] = ()
    unserved_tools: tuple[str, ...] = ()
    workarounds: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase_id,
            "capability": self.capability,
            "severity": self.severity.value,
            "blocking": self.blocking,
            "consequence": self.consequence,
            "missing_tools": list(self.missing_tools),
            "unreachable_tools": list(self.unreachable_tools),
            "unserved_tools": list(self.unserved_tools),
            "workarounds": list(self.workarounds),
        }


@dataclass(frozen=True)
class NodeCoverage:
    phase_id: str
    coverage: Coverage
    #: Per deliverable: its coverage, required or only expected.
    deliverables: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase_id,
            "coverage": self.coverage.value,
            "deliverables": [dict(d) for d in self.deliverables],
        }


@dataclass(frozen=True)
class CapabilityReport:
    nodes: tuple[NodeCoverage, ...]
    gaps: tuple[Gap, ...] = ()
    #: What the assessment could not see, said rather than assumed.
    limits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocking_gaps(self) -> tuple[Gap, ...]:
        return tuple(g for g in self.gaps if g.blocking)

    @property
    def status(self) -> str:
        """READY, READY_WITH_WARNINGS or BLOCKED: the readiness gate's answer."""
        if self.blocking_gaps:
            return "BLOCKED"
        if self.gaps or any(n.coverage is not Coverage.FULL for n in self.nodes):
            return "READY_WITH_WARNINGS"
        return "READY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "nodes": [n.as_dict() for n in self.nodes],
            "gaps": [g.as_dict() for g in self.gaps],
            "limits": list(self.limits),
        }


_RANK = {Coverage.FULL: 0, Coverage.UNKNOWN: 1, Coverage.PARTIAL: 2, Coverage.UNAVAILABLE: 3}


def assess_capabilities(
    phases: Sequence[Any],
    *,
    producers: Mapping[str, Iterable[str]],
    registered: Iterable[str],
    reachable: Iterable[str] | None = None,
    served: Iterable[str] | None = None,
) -> CapabilityReport:
    """Coverage and gaps for every phase of a flow.

    ``producers`` maps a deliverable type to the tools that produce it.
    ``registered`` is every tool the server has. ``reachable`` (optional)
    narrows that to tools whose adapter answered a health check; ``served``
    (optional) to tools the caller's connection is served, such as a
    client's tool profile. ``None`` means "not checked", which is recorded
    in the report's limits rather than read as "all fine".
    """
    registered_set = set(registered)
    reachable_set = None if reachable is None else set(reachable)
    served_set = None if served is None else set(served)
    limits: list[str] = []
    if reachable_set is None:
        limits.append("adapter health was not checked: a registered tool may still be down")
    if served_set is None:
        limits.append("the caller's tool profile was not given: coverage is for the full set")

    nodes: list[NodeCoverage] = []
    gaps: list[Gap] = []
    for phase in phases:
        required = list(getattr(phase, "required_deliverables", ()) or ())
        expected = [a for a in getattr(phase, "expected_artifacts", ()) or () if a not in required]
        rows: list[dict[str, str]] = []
        worst = Coverage.FULL
        conditional = bool(getattr(phase, "condition", None))
        for deliverable, is_required in [
            *((d, True) for d in required),
            *((d, False) for d in expected),
        ]:
            tools = tuple(sorted(set(producers.get(deliverable, ()))))
            if not tools:
                coverage = Coverage.UNKNOWN
                rows.append(_row(deliverable, coverage, is_required))
                worst = max(worst, coverage, key=_RANK.__getitem__)
                continue
            missing = tuple(t for t in tools if t not in registered_set)
            present = [t for t in tools if t in registered_set]
            unreachable = tuple(
                t for t in present if reachable_set is not None and t not in reachable_set
            )
            unserved = tuple(
                t
                for t in present
                if t not in unreachable and served_set is not None and t not in served_set
            )
            usable = [t for t in present if t not in unreachable and t not in unserved]
            if len(usable) == len(tools):
                coverage = Coverage.FULL
            elif usable:
                coverage = Coverage.PARTIAL
            else:
                coverage = Coverage.UNAVAILABLE
            rows.append(_row(deliverable, coverage, is_required))
            worst = max(worst, coverage, key=_RANK.__getitem__)
            if coverage is Coverage.FULL:
                continue
            gaps.append(
                _gap(
                    phase.id,
                    deliverable,
                    coverage=coverage,
                    required=is_required,
                    conditional=conditional,
                    missing=missing,
                    unreachable=unreachable,
                    unserved=unserved,
                )
            )
        nodes.append(NodeCoverage(phase_id=phase.id, coverage=worst, deliverables=tuple(rows)))
    return CapabilityReport(nodes=tuple(nodes), gaps=tuple(gaps), limits=tuple(limits))


def _row(deliverable: str, coverage: Coverage, required: bool) -> dict[str, str]:
    return {
        "deliverable": deliverable,
        "coverage": coverage.value,
        "required": "true" if required else "false",
    }


def _gap(
    phase_id: str,
    deliverable: str,
    *,
    coverage: Coverage,
    required: bool,
    conditional: bool,
    missing: tuple[str, ...],
    unreachable: tuple[str, ...],
    unserved: tuple[str, ...],
) -> Gap:
    """One shortfall, with the severity its cause and importance call for."""
    if unreachable or unserved:
        # Something the user can fix: start the adapter, or connect with a
        # profile that serves the tool.
        severity = GapSeverity.REQUIRES_USER_ACTION
        if unreachable:
            consequence = (
                f"adapter(s) for {', '.join(unreachable)} did not answer a health check; "
                "start the container"
            )
        else:
            consequence = (
                f"{', '.join(unserved)} not served on this connection's profile; "
                "connect with one that serves it"
            )
    elif coverage is Coverage.UNAVAILABLE:
        severity = GapSeverity.REQUIRES_NEW_CAPABILITY
        consequence = f"no registered tool produces {deliverable}"
    else:
        severity = GapSeverity.DEGRADES_CONFIDENCE
        consequence = (
            f"part of the {deliverable} toolchain is missing ({', '.join(missing)}); the "
            "step can run with reduced fidelity"
        )
    if not required:
        # Only expected, not required at the gate: the gate can still pass.
        blocking = False
        if severity is not GapSeverity.REQUIRES_USER_ACTION:
            severity = GapSeverity.DEGRADES_CONFIDENCE
    else:
        blocking = coverage is Coverage.UNAVAILABLE or bool(unreachable and not missing)
        if blocking and severity is GapSeverity.DEGRADES_CONFIDENCE:
            severity = GapSeverity.BLOCKS_STEP
    if conditional and blocking:
        consequence += " (the phase is conditional, so it may not run at all)"
    return Gap(
        phase_id=phase_id,
        capability=deliverable,
        severity=severity,
        blocking=blocking,
        consequence=consequence,
        missing_tools=missing,
        unreachable_tools=unreachable,
        unserved_tools=unserved,
        workarounds=_WORKAROUNDS.get(deliverable, ()),
    )
