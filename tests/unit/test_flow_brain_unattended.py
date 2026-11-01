"""A server-run phase is told it cannot ask the person (FORGE-714).

Live on the sandbox: a mech_v1 run for a chair reached route_selection with no
manufacturing context in the brief. The objective says "ask the person for
anything missing rather than assume it", so on every attempt the phase asked for
quantity and materials in its reply, stopped after two steps having recorded
nothing, and the run failed when three attempts drew the same gate findings.
Nothing can answer a question in a phase this brain runs.
"""

from __future__ import annotations

from api_gateway.runs.flow_brain import UNATTENDED_PHASE_RULE, ReActPhaseBrain
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.retry import build_retry_feedback
from orchestrator.design_flow.spec import Phase, get_flow


def _route_selection() -> Phase:
    return next(p for p in get_flow("mech_v1").phases if p.id == "route_selection")


def test_the_route_phase_that_asked_is_told_nobody_can_answer() -> None:
    phase = _route_selection()
    assert "ask the person" in phase.objective  # the instruction that stalled the run
    prompt = ReActPhaseBrain(mcp_bridge=None)._prompt(
        "Build a comfortable modern chair", phase, FlowContext(goal="chair", project_id="p1")
    )
    assert UNATTENDED_PHASE_RULE in prompt


def test_the_rule_names_the_way_out_instead_of_asking() -> None:
    # Deferral is how a missing answer reaches the gate, where a person sees it.
    assert "do NOT ask and stop" in UNATTENDED_PHASE_RULE
    assert "deferred: true" in UNATTENDED_PHASE_RULE
    assert "open questions" in UNATTENDED_PHASE_RULE
    assert "state each assumption" in UNATTENDED_PHASE_RULE
    assert "retry brings their answer back" in UNATTENDED_PHASE_RULE


def test_the_rule_follows_the_procedures_that_say_to_ask() -> None:
    # The injected procedure (rank_manufacturing_processes) also says to ask the
    # person; the rule must come after it so it is read as the override.
    prompt = ReActPhaseBrain(mcp_bridge=None)._prompt(
        "chair", _route_selection(), FlowContext(goal="chair", project_id="p1")
    )
    assert prompt.index(UNATTENDED_PHASE_RULE) > prompt.index("Your objective for THIS phase")


def test_every_phase_gets_the_rule_and_a_retry_still_leads() -> None:
    brain = ReActPhaseBrain(mcp_bridge=None)
    phase = Phase(id="p", title="P", objective="obj")
    feedback = build_retry_feedback(findings=["missing X"], reason="quantity 50", attempt=2)
    retried = brain._prompt("goal", phase, FlowContext(goal="goal", retry_feedback=feedback))
    assert retried.startswith(feedback)
    assert UNATTENDED_PHASE_RULE in retried
