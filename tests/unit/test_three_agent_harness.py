"""Unit tests for the three-agent harness foundation (MET-474, MET-475).

Concrete Planner / Generator / Evaluator implementations land in
follow-up PRs (hardware-design for MET-474, coding for MET-475). This
file pins the **orchestrator contract** that both will use:

- pass on first iteration → ``status="passed"``, one iteration recorded
- N failures then a pass → ``status="passed"``, N+1 iterations recorded
- always-failing evaluator → ``status="exhausted"``, ``max_iterations``
  iterations recorded, ``error`` set
- agent raises → ``status="errored"``, partial history preserved
- evaluator sees prior feedback on retries (so the planner can react)
- artifact store enforces the (run_id, name) → versioned content
  contract every concrete harness depends on
"""

from __future__ import annotations

import pytest

from orchestrator.harness import (
    ArtifactStore,
    Evaluator,
    GateResult,
    Generator,
    HarnessConfig,
    InMemoryArtifactStore,
    Planner,
    ThreeAgentHarness,
)
from orchestrator.harness.three_agent import (
    EvaluatorResult,
    GeneratorResult,
    PlannerResult,
)

# ---------------------------------------------------------------------------
# Stub agents — exercise the loop without real LLM calls
# ---------------------------------------------------------------------------


class _StubPlanner:
    """Writes ``design_spec.md`` with the iteration number, records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def plan(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        prior_feedback: EvaluatorResult | None,
    ) -> PlannerResult:
        self.calls.append(
            {
                "iteration": iteration,
                "prior_feedback_passed": (prior_feedback.passed if prior_feedback else None),
                "prior_failed_gates": (
                    [g.name for g in prior_feedback.gates if not g.passed] if prior_feedback else []
                ),
            }
        )
        await store.put(
            run_id,
            "design_spec.md",
            f"spec written by stub planner at iteration {iteration}\n",
            metadata={"iteration": str(iteration)},
        )
        return PlannerResult(spec_artifact="design_spec.md")


class _StubGenerator:
    """Writes ``bom.csv``, records the spec it saw."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
    ) -> GeneratorResult:
        spec = await store.get(run_id, spec_artifact)
        self.calls.append(
            {
                "iteration": iteration,
                "spec_seen": spec.content if spec else None,
            }
        )
        await store.put(
            run_id,
            "bom.csv",
            f"mpn,qty\nSTM32H743,1\n# iter={iteration}\n",
        )
        return GeneratorResult(output_artifacts=["bom.csv"])


class _ScriptedEvaluator:
    """Returns a scripted sequence of gate verdicts."""

    def __init__(self, verdicts: list[bool]) -> None:
        # verdicts[i] is the result for the i-th call (1-indexed iteration → 0).
        self._verdicts = list(verdicts)
        self.calls: list[dict[str, object]] = []

    async def evaluate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
        output_artifacts: list[str],
    ) -> EvaluatorResult:
        idx = iteration - 1
        verdict = self._verdicts[idx] if idx < len(self._verdicts) else False
        self.calls.append(
            {
                "iteration": iteration,
                "verdict": verdict,
                "outputs": list(output_artifacts),
            }
        )
        gates = [
            GateResult(name="bom_present", passed=True),
            GateResult(
                name="constraint_validate",
                passed=verdict,
                detail="" if verdict else "voltage out of bounds",
            ),
        ]
        return EvaluatorResult(gates=gates, passed=all(g.passed for g in gates))


class _ExplodingPlanner:
    """Raises on the configured iteration to exercise the error path."""

    def __init__(self, explode_on: int) -> None:
        self._explode_on = explode_on

    async def plan(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        prior_feedback: EvaluatorResult | None,
    ) -> PlannerResult:
        if iteration == self._explode_on:
            raise RuntimeError("kaboom")
        await store.put(run_id, "design_spec.md", f"iter {iteration}")
        return PlannerResult(spec_artifact="design_spec.md")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_iteration_pass_runs_once() -> None:
    """Evaluator passes on iteration 1 → exactly one iteration recorded."""
    store = InMemoryArtifactStore()
    planner, generator, evaluator = (
        _StubPlanner(),
        _StubGenerator(),
        _ScriptedEvaluator([True]),
    )
    harness = ThreeAgentHarness(planner, generator, evaluator, store)

    outcome = await harness.run("run-pass")

    assert outcome.status == "passed"
    assert len(outcome.iterations) == 1
    assert outcome.iterations[0].evaluator.passed is True
    assert outcome.error is None
    # Both agents ran exactly once.
    assert len(planner.calls) == 1
    assert len(generator.calls) == 1
    # First call has no prior feedback.
    assert planner.calls[0]["prior_feedback_passed"] is None


@pytest.mark.asyncio
async def test_failure_then_pass_iterates_with_feedback() -> None:
    """Two fails then a pass → 3 iterations; planner sees prior feedback."""
    store = InMemoryArtifactStore()
    planner, generator, evaluator = (
        _StubPlanner(),
        _StubGenerator(),
        _ScriptedEvaluator([False, False, True]),
    )
    harness = ThreeAgentHarness(planner, generator, evaluator, store)

    outcome = await harness.run("run-converge")

    assert outcome.status == "passed"
    assert len(outcome.iterations) == 3
    assert outcome.iterations[-1].evaluator.passed is True

    # First call: no prior feedback. Subsequent calls: feedback from the
    # failed evaluator carries the bad gate name through.
    assert planner.calls[0]["prior_feedback_passed"] is None
    assert planner.calls[1]["prior_feedback_passed"] is False
    assert planner.calls[1]["prior_failed_gates"] == ["constraint_validate"]
    assert planner.calls[2]["prior_feedback_passed"] is False


@pytest.mark.asyncio
async def test_max_iterations_exhausted_returns_partial_history() -> None:
    """Evaluator never passes → cap kicks in at max_iterations=5."""
    store = InMemoryArtifactStore()
    planner, generator, evaluator = (
        _StubPlanner(),
        _StubGenerator(),
        _ScriptedEvaluator([False] * 10),
    )
    harness = ThreeAgentHarness(
        planner,
        generator,
        evaluator,
        store,
        config=HarnessConfig(max_iterations=5),
    )

    outcome = await harness.run("run-exhaust")

    assert outcome.status == "exhausted"
    assert len(outcome.iterations) == 5
    assert outcome.error is not None
    assert "Max iterations" in outcome.error
    # The evaluator was called exactly 5 times — the cap is honoured.
    assert len(evaluator.calls) == 5


@pytest.mark.asyncio
async def test_agent_raises_stops_loop_with_partial_history() -> None:
    """Planner raises mid-loop → status=errored, error captured."""
    store = InMemoryArtifactStore()
    generator, evaluator = _StubGenerator(), _ScriptedEvaluator([False, True, True])
    planner = _ExplodingPlanner(explode_on=2)
    harness = ThreeAgentHarness(planner, generator, evaluator, store)

    outcome = await harness.run("run-explode")

    assert outcome.status == "errored"
    assert outcome.error == "kaboom"
    # Iteration 1 completed and was recorded before iter 2 blew up.
    assert len(outcome.iterations) == 1
    assert outcome.iterations[0].iteration == 1


@pytest.mark.asyncio
async def test_run_id_required() -> None:
    """Empty run_id is a programmer error — surface it cleanly."""
    harness = ThreeAgentHarness(
        _StubPlanner(),
        _StubGenerator(),
        _ScriptedEvaluator([True]),
        InMemoryArtifactStore(),
    )
    with pytest.raises(ValueError, match="run_id"):
        await harness.run("")


# ---------------------------------------------------------------------------
# InMemoryArtifactStore contract
# ---------------------------------------------------------------------------


class TestInMemoryArtifactStore:
    """Pin the contract the harness depends on."""

    @pytest.mark.asyncio
    async def test_put_versions_increment_per_name(self) -> None:
        store = InMemoryArtifactStore()
        a1 = await store.put("r1", "spec.md", "v1")
        a2 = await store.put("r1", "spec.md", "v2")
        a3 = await store.put("r1", "bom.csv", "x")
        assert (a1.version, a2.version, a3.version) == (1, 2, 1)

    @pytest.mark.asyncio
    async def test_get_returns_latest(self) -> None:
        store = InMemoryArtifactStore()
        await store.put("r1", "spec.md", "v1")
        await store.put("r1", "spec.md", "v2")
        latest = await store.get("r1", "spec.md")
        assert latest is not None
        assert latest.content == "v2"
        assert latest.version == 2

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self) -> None:
        store = InMemoryArtifactStore()
        assert await store.get("r1", "missing.md") is None

    @pytest.mark.asyncio
    async def test_list_for_run_preserves_insertion_order(self) -> None:
        store = InMemoryArtifactStore()
        await store.put("r1", "a", "1")
        await store.put("r1", "b", "2")
        await store.put("r1", "a", "3")
        history = await store.list_for_run("r1")
        assert [a.name for a in history] == ["a", "b", "a"]
        assert [a.content for a in history] == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_runs_are_isolated(self) -> None:
        """Two runs writing the same artifact name don't collide."""
        store = InMemoryArtifactStore()
        await store.put("r1", "spec.md", "r1-content")
        await store.put("r2", "spec.md", "r2-content")
        r1 = await store.get("r1", "spec.md")
        r2 = await store.get("r2", "spec.md")
        assert r1 is not None and r2 is not None
        assert r1.content == "r1-content"
        assert r2.content == "r2-content"


# ---------------------------------------------------------------------------
# Protocol compliance — the stubs satisfy the published Protocols
# ---------------------------------------------------------------------------


def test_stubs_satisfy_protocols() -> None:
    assert isinstance(_StubPlanner(), Planner)
    assert isinstance(_StubGenerator(), Generator)
    assert isinstance(_ScriptedEvaluator([True]), Evaluator)
