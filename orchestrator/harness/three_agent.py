"""Three-agent harness (MET-474, MET-475).

Both the hardware-design harness (MET-474) and the coding harness
(MET-475) run the same loop:

  Planner → Generator → Evaluator → (fail) → Planner → ...

The orchestrator stops in three cases:

1. Evaluator returns ``passed=True`` → ``HarnessOutcome(status="passed")``
2. Iteration counter hits the cap (MET-474/MET-475 spec: 5) →
   ``HarnessOutcome(status="exhausted")``
3. Any agent raises → outcome captures the exception + the iteration
   it died on; the orchestrator re-raises so callers can decide
   whether to retry the whole run

The agent Protocols here are intentionally narrow — each method takes
a single dict of inputs (everything the agent needs is read off the
``ArtifactStore`` by name, not threaded through the signature) and
returns a small typed result. Concrete agents in follow-up PRs
implement the Protocol; the orchestrator never imports their
implementations directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog

from observability.tracing import get_tracer
from orchestrator.harness.artifacts import ArtifactStore

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.harness.three_agent")


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """One quality-gate check outcome returned by the Evaluator.

    The Evaluator surfaces a list of these; the orchestrator considers
    the iteration a pass when every gate's ``passed`` is True. Fields
    map directly onto the readiness reporter / Linear comment shape
    so the harness output is consumable without post-processing.
    """

    name: str
    passed: bool
    detail: str = ""
    # Free-form: ``severity``, ``rule_id``, ``measured_value``, etc.
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PlannerResult:
    """What the Planner hands back to the orchestrator."""

    spec_artifact: str  # name of the artifact the planner just wrote
    notes: str = ""


@dataclass
class GeneratorResult:
    """What the Generator hands back to the orchestrator."""

    # Artifacts the generator wrote during this iteration — e.g.
    # ``["bom.csv", "schematic_outline.md"]`` for hardware,
    # ``["src/foo.py", "tests/test_foo.py"]`` for coding.
    output_artifacts: list[str]
    notes: str = ""


@dataclass
class EvaluatorResult:
    """What the Evaluator hands back to the orchestrator."""

    gates: list[GateResult]
    # Convenience: ``all(g.passed for g in gates)``. Stored once at
    # construction so the orchestrator doesn't have to re-derive it.
    passed: bool
    notes: str = ""


@dataclass
class IterationRecord:
    """One iteration's slice through the loop — what each agent did."""

    iteration: int
    planner: PlannerResult
    generator: GeneratorResult
    evaluator: EvaluatorResult
    duration_seconds: float


@dataclass
class HarnessOutcome:
    """Final outcome of a ``ThreeAgentHarness.run`` call.

    ``status`` is one of:
    - ``"passed"`` — Evaluator returned ``passed=True``
    - ``"exhausted"`` — hit ``max_iterations`` without a pass
    - ``"errored"`` — an agent raised; ``error`` holds the message
    """

    run_id: str
    status: str
    iterations: list[IterationRecord]
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def final_iteration(self) -> IterationRecord | None:
        return self.iterations[-1] if self.iterations else None


# ---------------------------------------------------------------------------
# Agent Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class Planner(Protocol):
    """The Planner reads user intent + prior evaluator feedback,
    writes a spec artifact, and returns the artifact name."""

    async def plan(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        prior_feedback: EvaluatorResult | None,
    ) -> PlannerResult: ...


@runtime_checkable
class Generator(Protocol):
    """The Generator reads the spec, writes implementation artifacts,
    and returns the list of artifact names it produced."""

    async def generate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
    ) -> GeneratorResult: ...


@runtime_checkable
class Evaluator(Protocol):
    """The Evaluator reads everything produced this iteration and
    returns the gate verdicts."""

    async def evaluate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
        output_artifacts: list[str],
    ) -> EvaluatorResult: ...


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    """Knobs the orchestrator reads at ``run`` time.

    ``max_iterations`` defaults to 5 to match the MET-474 / MET-475
    spec ("Handles up to 5 iterations before escalation").
    """

    max_iterations: int = 5
    stop_on_error: bool = True


class ThreeAgentHarness:
    """Run the Planner → Generator → Evaluator loop with a cap.

    Construction is pure config — the harness owns no state. ``run``
    threads everything through the artifact store so concrete agents
    can mock cleanly and the contract for "what artifacts exist after
    each iteration" is enforced by the store, not the orchestrator.
    """

    def __init__(
        self,
        planner: Planner,
        generator: Generator,
        evaluator: Evaluator,
        store: ArtifactStore,
        *,
        config: HarnessConfig | None = None,
    ) -> None:
        self._planner = planner
        self._generator = generator
        self._evaluator = evaluator
        self._store = store
        self._config = config or HarnessConfig()

    async def run(self, run_id: str) -> HarnessOutcome:
        """Execute the loop. Returns when a gate set passes, the cap
        is hit, or an agent raises (and ``stop_on_error`` is True)."""
        if not run_id:
            raise ValueError("run_id is required")

        start = time.perf_counter()
        iterations: list[IterationRecord] = []
        prior_feedback: EvaluatorResult | None = None
        last_error: str | None = None

        with tracer.start_as_current_span("harness.three_agent.run") as span:
            span.set_attribute("harness.run_id", run_id)
            span.set_attribute("harness.max_iterations", self._config.max_iterations)

            for iteration in range(1, self._config.max_iterations + 1):
                iter_start = time.perf_counter()
                logger.info(
                    "harness_iteration_start",
                    run_id=run_id,
                    iteration=iteration,
                    has_prior_feedback=prior_feedback is not None,
                )
                try:
                    planner_result = await self._planner.plan(
                        run_id,
                        self._store,
                        iteration=iteration,
                        prior_feedback=prior_feedback,
                    )
                    generator_result = await self._generator.generate(
                        run_id,
                        self._store,
                        iteration=iteration,
                        spec_artifact=planner_result.spec_artifact,
                    )
                    evaluator_result = await self._evaluator.evaluate(
                        run_id,
                        self._store,
                        iteration=iteration,
                        spec_artifact=planner_result.spec_artifact,
                        output_artifacts=generator_result.output_artifacts,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    logger.error(
                        "harness_iteration_error",
                        run_id=run_id,
                        iteration=iteration,
                        error=last_error,
                    )
                    span.record_exception(exc)
                    if self._config.stop_on_error:
                        elapsed = time.perf_counter() - start
                        return HarnessOutcome(
                            run_id=run_id,
                            status="errored",
                            iterations=iterations,
                            error=last_error,
                            duration_seconds=elapsed,
                        )
                    continue

                iterations.append(
                    IterationRecord(
                        iteration=iteration,
                        planner=planner_result,
                        generator=generator_result,
                        evaluator=evaluator_result,
                        duration_seconds=time.perf_counter() - iter_start,
                    )
                )
                logger.info(
                    "harness_iteration_complete",
                    run_id=run_id,
                    iteration=iteration,
                    passed=evaluator_result.passed,
                    gate_count=len(evaluator_result.gates),
                    failed_gates=[g.name for g in evaluator_result.gates if not g.passed],
                )

                if evaluator_result.passed:
                    elapsed = time.perf_counter() - start
                    span.set_attribute("harness.status", "passed")
                    span.set_attribute("harness.iterations_used", iteration)
                    return HarnessOutcome(
                        run_id=run_id,
                        status="passed",
                        iterations=iterations,
                        duration_seconds=elapsed,
                    )

                # Feed this evaluator's verdict to the planner next loop.
                prior_feedback = evaluator_result

            # Cap exhausted — return the partial history.
            elapsed = time.perf_counter() - start
            span.set_attribute("harness.status", "exhausted")
            span.set_attribute("harness.iterations_used", len(iterations))
            return HarnessOutcome(
                run_id=run_id,
                status="exhausted",
                iterations=iterations,
                error=f"Max iterations ({self._config.max_iterations}) exhausted",
                duration_seconds=elapsed,
            )
