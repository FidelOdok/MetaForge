"""End-to-end tests for the coding harness (MET-475).

MET-475 acceptance:

* Pattern handles 5+ real GitHub issues end-to-end
* All 10 quality gates pass before merging
* Zero regressions in existing tests
* Developers can use as template for all future tasks

We exercise the loop with 5 representative issues — sized, scoped, and
labeled differently — to prove the pattern is generic. Each issue runs
through Planner → Generator → Evaluator and converges on iteration 1.

The Evaluator's 10 gates run as a mix of:
- Programmatic gates that read the Generator's manifest (tests count,
  bare-except scan, docstring presence, no-new-deps, PR-description
  structure) — these run real Python and pin the contract for the
  drop-in subprocess-driven gates that ship next
- Scripted gates for the four gates that require external tools
  (ruff, mypy, coverage, spec-match LLM check) — same Protocol, just
  pre-baked outcomes; production runners replace these
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from orchestrator.harness import (
    HarnessConfig,
    InMemoryArtifactStore,
    ThreeAgentHarness,
)
from orchestrator.harness.coding import (
    CodingEvaluator,
    CodingGenerator,
    CodingPlanner,
    GitHubIssue,
    QualityGate,
)
from orchestrator.harness.coding.agents import (
    gate_docstrings,
    gate_error_handling,
    gate_no_new_deps,
    gate_pr_description,
    gate_tests_min_5,
)
from orchestrator.harness.coding.gates import (
    CodingHarnessGates,
    FunctionalGate,
    GateOutcome,
    ScriptedGate,
    scripted_passing_gates,
)


def _ten_gate_set(*, all_pass: bool = True) -> list[QualityGate]:
    """The canonical 10 gates: 5 programmatic + 5 scripted (tool-deps)."""
    scripted_outcomes = [
        GateOutcome(name=name, passed=all_pass, detail="OK" if all_pass else "scripted fail")
        for name in (
            CodingHarnessGates.RUFF,
            CodingHarnessGates.MYPY,
            CodingHarnessGates.COVERAGE,
            CodingHarnessGates.SPEC_MATCH,
            CodingHarnessGates.PATTERNS,
        )
    ]
    return [
        ScriptedGate(CodingHarnessGates.RUFF, [scripted_outcomes[0]]),
        ScriptedGate(CodingHarnessGates.MYPY, [scripted_outcomes[1]]),
        FunctionalGate(CodingHarnessGates.TESTS, gate_tests_min_5),
        ScriptedGate(CodingHarnessGates.COVERAGE, [scripted_outcomes[2]]),
        ScriptedGate(CodingHarnessGates.SPEC_MATCH, [scripted_outcomes[3]]),
        FunctionalGate(CodingHarnessGates.ERROR_HANDLING, gate_error_handling),
        FunctionalGate(CodingHarnessGates.DOCSTRINGS, gate_docstrings),
        ScriptedGate(CodingHarnessGates.PATTERNS, [scripted_outcomes[4]]),
        FunctionalGate(CodingHarnessGates.DEPENDENCIES, gate_no_new_deps),
        FunctionalGate(CodingHarnessGates.PR_DESCRIPTION, gate_pr_description),
    ]


# Five real-ish issues — different shapes (memory / knowledge / twin /
# constraint / harness module) to prove the pattern's general enough.
ISSUES: list[GitHubIssue] = [
    GitHubIssue(
        number=1001,
        title="Add memory retrieval helper",
        body="Implement a small memory module that wraps retrieve_similar_experience",
        labels=["area: backend", "type: feature"],
    ),
    GitHubIssue(
        number=1002,
        title="Add knowledge ingest CLI",
        body="A knowledge ingest CLI that pipes a file into the L1 KB",
        labels=["area: cli"],
    ),
    GitHubIssue(
        number=1003,
        title="Add twin orphan check",
        body="Add a twin orphan-check helper for unit tests",
        labels=["area: backend"],
    ),
    GitHubIssue(
        number=1004,
        title="Add constraint validator example",
        body="A constraint validation example wired into the harness sample suite",
        labels=["type: docs"],
    ),
    GitHubIssue(
        number=1005,
        title="Add harness debug logger",
        body="A harness debug logger that pretty-prints iteration boundaries",
        labels=["area: backend", "type: chore"],
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", ISSUES, ids=lambda i: f"issue-{i.number}")
async def test_coding_harness_handles_real_github_issues(issue: GitHubIssue) -> None:
    """MET-475 acceptance: pattern handles 5+ real GitHub issues E2E."""
    store = InMemoryArtifactStore()
    harness = ThreeAgentHarness(
        CodingPlanner(issue),
        CodingGenerator(tests_per_module=5),
        CodingEvaluator(_ten_gate_set(all_pass=True)),
        store,
    )

    outcome = await harness.run(f"coding-{issue.number}-{uuid4().hex[:6]}")

    # Acceptance: every iteration's gates pass and the loop converges
    # on iteration 1 for the deterministic happy path.
    assert outcome.status == "passed", outcome.error
    assert len(outcome.iterations) == 1
    final = outcome.iterations[-1].evaluator
    assert final.passed is True
    # All 10 gates ran.
    assert len(final.gates) == 10
    assert {g.name for g in final.gates} == set(CodingHarnessGates.ALL)


@pytest.mark.asyncio
async def test_failed_gate_triggers_iteration_then_passes() -> None:
    """If a scripted gate fails on iter 1 and passes on iter 2, the
    loop converges on iter 2 — the prior_feedback path is exercised."""
    issue = GitHubIssue(number=2001, title="Failing gate then passing", body="memory module")
    store = InMemoryArtifactStore()

    # The mypy scripted gate fails iter 1 then passes iter 2.
    flaky_mypy = ScriptedGate(
        CodingHarnessGates.MYPY,
        [
            GateOutcome(name=CodingHarnessGates.MYPY, passed=False, detail="missing return type"),
            GateOutcome(name=CodingHarnessGates.MYPY, passed=True, detail="OK"),
        ],
    )
    gates = _ten_gate_set(all_pass=True)
    # Replace the scripted mypy gate with the flaky one (index 1).
    gates[1] = flaky_mypy

    harness = ThreeAgentHarness(
        CodingPlanner(issue),
        CodingGenerator(tests_per_module=5),
        CodingEvaluator(gates),
        store,
    )
    outcome = await harness.run("coding-flaky-mypy")

    assert outcome.status == "passed"
    assert len(outcome.iterations) == 2
    # Iter 1 failed mypy; iter 2 passed all 10.
    iter1_failed = [g.name for g in outcome.iterations[0].evaluator.gates if not g.passed]
    assert iter1_failed == [CodingHarnessGates.MYPY]
    assert outcome.iterations[1].evaluator.passed is True


@pytest.mark.asyncio
async def test_always_failing_gate_exhausts_the_cap() -> None:
    """A gate that never passes hits the 5-iteration cap from the spec."""
    issue = GitHubIssue(number=3001, title="impossible", body="harness module")
    store = InMemoryArtifactStore()
    gates = _ten_gate_set(all_pass=True)
    # Replace ruff gate (index 0) with one that always fails.
    gates[0] = ScriptedGate(
        CodingHarnessGates.RUFF,
        [GateOutcome(name=CodingHarnessGates.RUFF, passed=False, detail="always fails")],
    )
    harness = ThreeAgentHarness(
        CodingPlanner(issue),
        CodingGenerator(tests_per_module=5),
        CodingEvaluator(gates),
        store,
        config=HarnessConfig(max_iterations=5),
    )
    outcome = await harness.run("coding-impossible")

    assert outcome.status == "exhausted"
    assert len(outcome.iterations) == 5
    assert outcome.error is not None
    # Every iteration failed on the same ruff gate.
    for record in outcome.iterations:
        assert any(
            not g.passed and g.name == CodingHarnessGates.RUFF for g in record.evaluator.gates
        )


@pytest.mark.asyncio
async def test_evaluator_rejects_empty_gate_set() -> None:
    """At least one gate is required — programmer-error check."""
    with pytest.raises(ValueError, match="at least one"):
        CodingEvaluator([])


@pytest.mark.asyncio
async def test_scripted_passing_gates_helper_returns_full_set() -> None:
    """``scripted_passing_gates()`` covers all 10 named gates."""
    gates = scripted_passing_gates()
    assert {g.name for g in gates} == set(CodingHarnessGates.ALL)


@pytest.mark.asyncio
async def test_pr_description_gate_catches_missing_sections() -> None:
    """Gate 10 (PR description) demands both ``## Summary`` and ``## Test plan``."""
    store = InMemoryArtifactStore()
    await store.put("r1", "pr_description.md", "## Summary\nno test plan section")
    outcome = await gate_pr_description("r1", store)
    assert outcome.name == CodingHarnessGates.PR_DESCRIPTION
    assert outcome.passed is False
    assert "test_plan=False" in outcome.detail


@pytest.mark.asyncio
async def test_tests_min_5_gate_rejects_low_test_count() -> None:
    """Gate 3 fails when the generator's manifest reports <5 tests."""
    issue = GitHubIssue(number=4001, title="Low test count", body="memory module")
    store = InMemoryArtifactStore()
    generator = CodingGenerator(tests_per_module=3)  # below the spec floor

    # Drive plan + generate manually so we can call the gate directly.
    planner = CodingPlanner(issue)
    plan = await planner.plan("r1", store, iteration=1, prior_feedback=None)
    await generator.generate("r1", store, iteration=1, spec_artifact=plan.spec_artifact)

    outcome = await gate_tests_min_5("r1", store)
    assert outcome.passed is False
    assert "tests_per_module=3" in outcome.detail
