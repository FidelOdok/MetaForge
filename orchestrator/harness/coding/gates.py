"""The 10 quality gates for the coding harness (MET-475).

Each gate is a ``QualityGate`` — a Protocol with one async ``check``
method that takes the run's artifact store and returns a typed
``GateOutcome``. The orchestrator runs every registered gate and the
iteration passes only when every outcome's ``passed`` is True
(matches the harness foundation's ``GateResult.passed`` semantics).

The 10 gate slots from the MET-475 spec live as named constants on
``CodingHarnessGates``. Concrete implementations (real ruff / mypy /
pytest runners) ship as follow-up PRs and slot in by satisfying the
Protocol. This module ships a ``ScriptedGate`` helper so tests can
exercise the iteration loop deterministically.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from orchestrator.harness.artifacts import ArtifactStore


@dataclass(frozen=True)
class GateOutcome:
    """Result of one gate check.

    Maps 1:1 onto ``orchestrator.harness.three_agent.GateResult`` so
    the evaluator just renames the field on the way through.
    """

    name: str
    passed: bool
    detail: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class QualityGate(Protocol):
    """One programmatic check the CodingEvaluator runs.

    Each gate gets a ``name`` so the evaluator can roll results up by
    name (and the Phase 7 readiness reporter can key off it later).
    """

    name: str

    async def check(self, run_id: str, store: ArtifactStore) -> GateOutcome: ...


class ScriptedGate:
    """Deterministic gate that returns a pre-baked outcome.

    Used by the harness E2E tests to drive the iteration loop without
    shelling out to ruff / mypy / pytest. Production runners replace
    this with concrete subprocess-driven gates that satisfy the same
    ``QualityGate`` Protocol.
    """

    def __init__(self, name: str, outcomes: list[GateOutcome | bool]) -> None:
        self.name = name
        self._outcomes: list[GateOutcome | bool] = list(outcomes)
        self._call_count = 0

    async def check(self, run_id: str, store: ArtifactStore) -> GateOutcome:
        idx = min(self._call_count, len(self._outcomes) - 1)
        result = self._outcomes[idx]
        self._call_count += 1
        if isinstance(result, GateOutcome):
            return result
        return GateOutcome(
            name=self.name,
            passed=bool(result),
            detail="" if result else f"scripted fail for {self.name}",
        )


class FunctionalGate:
    """Adapts a plain async callable to the ``QualityGate`` Protocol.

    Lets simple in-process checks (parse plan.md, count tests, scan
    for bare `except:`) avoid a full subclass.
    """

    def __init__(
        self,
        name: str,
        check_fn: Callable[[str, ArtifactStore], Awaitable[GateOutcome]],
    ) -> None:
        self.name = name
        self._check_fn = check_fn

    async def check(self, run_id: str, store: ArtifactStore) -> GateOutcome:
        return await self._check_fn(run_id, store)


# ---------------------------------------------------------------------------
# The 10 named gate slots from MET-475
# ---------------------------------------------------------------------------


class CodingHarnessGates:
    """Canonical names for the 10 gates listed in MET-475.

    Concrete `QualityGate` implementations register under these names
    so the evaluator can roll the outcomes up consistently and Phase
    7's readiness reporter keys off stable identifiers.
    """

    # 1. Static analysis: ruff check + ruff format = zero errors
    RUFF = "static_analysis_ruff"
    # 2. Type checking: mypy --strict = zero errors
    MYPY = "type_check_mypy_strict"
    # 3. Tests: minimum 5 tests per module, all pass, no skips
    TESTS = "tests_min_5_per_module_no_skips"
    # 4. Coverage: ≥80% or explained gaps
    COVERAGE = "coverage_min_80_or_explained"
    # 5. No hallucinations: Code matches specs exactly
    SPEC_MATCH = "no_hallucinations_spec_match"
    # 6. Error handling: All exceptions handled, no bare except
    ERROR_HANDLING = "error_handling_no_bare_except"
    # 7. Docstrings: Module/function docs present (Google style)
    DOCSTRINGS = "docstrings_google_style"
    # 8. Patterns: Follows existing codebase style
    PATTERNS = "follows_codebase_patterns"
    # 9. Dependencies: No new deps without justification
    DEPENDENCIES = "no_unjustified_new_deps"
    # 10. PR description: Clear commit message + testing proof
    PR_DESCRIPTION = "pr_description_with_test_proof"

    ALL: tuple[str, ...] = (
        RUFF,
        MYPY,
        TESTS,
        COVERAGE,
        SPEC_MATCH,
        ERROR_HANDLING,
        DOCSTRINGS,
        PATTERNS,
        DEPENDENCIES,
        PR_DESCRIPTION,
    )


def scripted_passing_gates() -> list[QualityGate]:
    """Convenience: 10 ScriptedGates that all pass.

    Used by E2E tests that want the loop to converge on iteration 1.
    """
    return [
        ScriptedGate(name, [GateOutcome(name=name, passed=True, detail="OK")])
        for name in CodingHarnessGates.ALL
    ]
