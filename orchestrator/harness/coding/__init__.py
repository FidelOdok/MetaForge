"""Coding three-agent harness (MET-475).

Concrete CodingPlanner / CodingGenerator / CodingEvaluator triple
that runs on top of the shared harness from
``orchestrator.harness.three_agent`` (MET-474 foundation). The
Evaluator hosts the **10 objective quality gates** the spec lists;
each gate is a ``QualityGate`` Protocol implementation, so concrete
checks (ruff, mypy, pytest, coverage…) can ship as drop-in
replacements without touching the orchestrator.

The agents here are deterministic — they don't shell out to ruff /
mypy / pytest at test time (that would couple the harness tests to
the host toolchain) but the Protocols are typed so a production
runner can wire those subprocesses behind the same interface.

Public API:

* ``GitHubIssue`` — minimal issue body the planner consumes
* ``CodingPlanner`` / ``CodingGenerator`` / ``CodingEvaluator``
* ``QualityGate`` Protocol + ``GateOutcome`` result shape
* ``CodingHarnessGates`` — the 10 named gate slots from MET-475
"""

from orchestrator.harness.coding.agents import (
    CodingEvaluator,
    CodingGenerator,
    CodingPlanner,
    GitHubIssue,
)
from orchestrator.harness.coding.gates import (
    CodingHarnessGates,
    GateOutcome,
    QualityGate,
)

__all__ = [
    "CodingEvaluator",
    "CodingGenerator",
    "CodingHarnessGates",
    "CodingPlanner",
    "GateOutcome",
    "GitHubIssue",
    "QualityGate",
]
