"""Bracket, end to end: the two suites actually connect (FORGE-566).

intent -> generated workflow -> accepted version -> execution on the real
in-process engine -> injected structural failure at V&V -> local repair
(rework to design) -> current evidence -> completion verdict.

The engine, the gates, the rework path and the lifecycle verdict are all the
real code. Only the phase work is scripted (no LLM, no CAD kernel): the brain
"designs" a 4 mm arm first, which the analysis finds at a safety factor of
1.6, and a 6 mm arm after the rework, which reaches 2.4. The constraint
checker plays the twin's constraint engine and reads those numbers.
"""

from __future__ import annotations

import asyncio

import pytest

from api_gateway.runs.routes import _in_process_state
from mcp_core.profiles import DELIVERABLE_TOOLS
from orchestrator.design_flow.capabilities import assess_capabilities
from orchestrator.design_flow.executor import (
    ConstraintReport,
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.lifecycle import CompletionClass, lifecycle_view
from orchestrator.design_flow.spec import Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from tests.workflow.scenarios import bracket


class BracketBrain:
    """Scripted phase work: the arm thickness, and the analysis it gets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.arm_mm = 0.0
        #: The analysed safety factor, or None when the geometry changed since.
        self.safety_factor: float | None = None

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.retry_feedback))
        if phase.id == "design":
            self.arm_mm = 6.0 if context.retry_feedback.startswith("REWORK") else 4.0
            self.safety_factor = None
            return PhaseOutcome(summary=f"bracket arm {self.arm_mm} mm", status="completed")
        if phase.id == "simulation":
            # Scripted results, not computed: the thin arm fails REQ-FOS, the thick one passes.
            self.safety_factor = 1.6 if self.arm_mm == 4.0 else 2.4
            return PhaseOutcome(
                summary=f"FEA at {bracket.LOAD_N} N: safety factor {self.safety_factor}",
                status="completed",
            )
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


class SafetyFactorChecker:
    """The constraint engine's view: REQ-FOS against the latest analysis."""

    def __init__(self, brain: BracketBrain) -> None:
        self.brain = brain

    async def check(self, project_id: str | None, since_ts: float = 0.0) -> ConstraintReport:
        sf = self.brain.safety_factor
        if sf is not None and sf < bracket.MIN_SAFETY_FACTOR:
            return ConstraintReport(
                checked=True,
                passed=False,
                evaluated_count=1,
                violations=[f"REQ-FOS: safety factor {sf} < {bracket.MIN_SAFETY_FACTOR}"],
            )
        return ConstraintReport(checked=True, passed=True, evaluated_count=1)


async def _next_gate(store: InMemoryRunStore, run_id: str, seen: int) -> None:
    for _ in range(800):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > seen:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never reached gate {seen + 1}: {store.get(run_id).status}")


@pytest.mark.asyncio
async def test_bracket_from_intent_to_a_verified_completion() -> None:
    # 1. Generation: a valid, ready workflow from the intent.
    proposal = bracket.generate()
    assert proposal.valid
    readiness = assess_capabilities(
        proposal.definition.phases, producers=DELIVERABLE_TOOLS, registered=bracket.all_tools()
    )
    assert readiness.status == "READY"

    # 2. Acceptance: the contract the run executes.
    accepted = bracket.accept(proposal)
    flow = accepted.definition

    # 3. Execution on the real engine.
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = BracketBrain()
    run = store.create({"goal": bracket.INTENT, "flow": flow.id, "project_id": "bracket"})
    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        constraint_checker=SafetyFactorChecker(brain),
    )
    task = asyncio.create_task(executor.run(run.id, flow, facts=accepted.frozen.facts))

    gates = 0
    decisions: list[tuple[str, str]] = []
    reworked = False
    while not task.done():
        try:
            await _next_gate(store, run.id, gates)
        except AssertionError:
            if task.done():
                break
            raise
        gates += 1
        state = coord.gate_state(run.id) or {}
        phase = str(state.get("phase"))
        reason = store.get(run.id).approval_reason or ""
        if "NOT READY" in reason:
            # 4. The injected structural failure: send the run back to the design.
            assert phase == "simulation" and not reworked, reason
            assert "REQ-FOS" in reason
            decisions.append((phase, "rework"))
            coord.note_rework(run.id, "design", "thicken the arm: REQ-FOS fails at 1.6")
            store.submit_approval(run.id, ApprovalDecision.REWORK)
            reworked = True
        else:
            decisions.append((phase, "approve"))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=5.0)

    # 5. The repair was local: earlier phases ran once, design and V&V twice.
    ran = [c[0] for c in brain.calls]
    assert ran == [
        "intent", "needs", "requirements", "feasibility", "design", "simulation",
        "design", "simulation",
    ]  # fmt: skip
    assert ("simulation", "rework") in decisions
    rework_feedback = next(f for p, f in brain.calls[6:] if p == "design")
    assert "REQ-FOS" in rework_feedback and "thicken the arm" in rework_feedback
    assert brain.safety_factor == 2.4

    # 6. Current evidence, then the verdict, from the real run record.
    final = store.get(run.id)
    assert final.status is RunStatus.COMPLETED
    requirements = [
        {"id": "REQ-LOAD", "status": "pass"},
        {"id": "REQ-FOS", "status": "pass" if brain.safety_factor >= 2 else "fail"},
        {"id": "REQ-ENVELOPE", "status": "pass"},
    ]
    view = lifecycle_view(flow.phases, _in_process_state(final), requirements=requirements)
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )

    # And had the stale 1.6 result been the evidence, it would not verify.
    stale = lifecycle_view(
        flow.phases,
        _in_process_state(final),
        requirements=[*requirements[:1], {"id": "REQ-FOS", "status": "fail"}, requirements[2]],
    )
    assert not stale.completion.verified
