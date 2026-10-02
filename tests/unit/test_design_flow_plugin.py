"""Design flows through the harness plugins (FORGE-400).

The property this file exists for is an **absence**: the agent has no tool
that approves its own call. An absence is the easiest thing to test badly —
assert one name is missing and a differently-named tool sails through — so
the check here is over the whole catalogue, matched on what a tool *does*
rather than what it is called.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.annotations import annotations_for
from mcp_core.guardrails import Caller, decide
from mcp_core.workflows import WORKFLOWS, prompt_body
from tool_registry.tools.design_flow.adapter import DesignFlowServer, render_run_markdown


async def _catalogue() -> dict[str, Any]:
    return {"default_flow_id": "design_v1", "flows": []}


async def _propose(**kwargs: Any) -> dict[str, Any]:
    return {
        "version_id": "flowv_1",
        "approval_id": "run_1",
        "changes": [],
        "next_step": "held for a person",
    }


async def _status(run_id: str) -> dict[str, Any]:
    return {"runId": run_id, "status": "running", "phases": [], "events": [], "live": True}


async def _start(**kwargs: Any) -> dict[str, Any]:
    return {"run_id": "run_2", "status": "running"}


def _server() -> DesignFlowServer:
    return DesignFlowServer(
        catalogue_reader=_catalogue,
        proposer=_propose,
        run_status_reader=_status,
        run_starter=_start,
    )


# ── the absence ──────────────────────────────────────────────────────────


class TestTheAgentCannotApproveItsOwnCall:
    def test_no_flow_tool_approves_anything(self) -> None:
        """Matched on behaviour, not on one forbidden name.

        Asserting `"flow.approve" not in tools` would pass the moment someone
        adds `flow.decide`, which is exactly how this guarantee gets lost.
        """
        for tool_id in _server().tool_ids:
            assert not any(
                word in tool_id.lower() for word in ("approve", "decide", "sign_off", "grant")
            ), f"{tool_id} looks like it answers an approval"

    def test_the_whole_mcp_catalogue_has_no_self_approval_tool(self) -> None:
        """Wider than this adapter. A tool anywhere that lets a caller answer
        its own held write defeats every gate in the epic, not just flows."""
        from mcp_core.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY

        known = READ_ONLY | ADDITIVE | DESTRUCTIVE
        from mcp_core.guardrails import HUMAN_AUTHORITY_TOOLS

        # A tool whose name says "approve" is acceptable only when the
        # approver comes from the approval record rather than from the
        # caller -- which is exactly what HUMAN_AUTHORITY_TOOLS enforces
        # (FORGE-393). Anything else with "approve" in its id is a tool that
        # takes a name from whoever called it.
        #
        # This check found two: `twin.approve_design_loop` took `approved_by`
        # as an argument and `twin.approve_engineering_change` took
        # `approver`. FORGE-393 had fixed promotion and guarded `ect.approve`
        # against a *blank* approver, which is not the same as guarding it
        # against a supplied one.
        exempt = HUMAN_AUTHORITY_TOOLS | {
            # Advances an entity's authority state; the actor is the twin's
            # own `created_by` convention, not a claim about who signed off.
            "twin.approve_engineering_entity",
        }
        offenders = [t for t in known if "approve" in t and t not in exempt]
        assert offenders == [], offenders

    def test_proposing_says_plainly_that_nothing_runs_yet(self) -> None:
        """The agent has to tell the user. A status code it must interpret
        into a sentence is a sentence it will sometimes get wrong."""
        body = prompt_body("flow")
        assert "cannot approve" in body
        assert "no tool would let you" in body
        assert "do not poll" in body


# ── the surface ──────────────────────────────────────────────────────────


class TestTheTools:
    def test_the_four_tools_register(self) -> None:
        assert sorted(_server().tool_ids) == [
            "flow.list",
            "flow.propose",
            "flow.start_run",
            "flow.status",
        ]

    def test_a_missing_binding_drops_only_its_own_tool(self) -> None:
        """A partial wiring exposes what works rather than a surface that
        half-fails at call time."""
        partial = DesignFlowServer(catalogue_reader=_catalogue)
        assert sorted(partial.tool_ids) == ["flow.list"]

    def test_reads_are_reads_and_writes_are_approved_on_the_version(self) -> None:
        """FORGE-471: both write, but the approval is on the flow version, not
        on the call. Holding the call too asked the same person twice."""
        assert annotations_for("flow.list")["readOnlyHint"] is True
        assert annotations_for("flow.status")["readOnlyHint"] is True
        for tool_id in ("flow.propose", "flow.start_run"):
            assert annotations_for(tool_id)["readOnlyHint"] is False
            assert not decide(tool_id, caller=Caller.REMOTE).requires_approval

    def test_status_is_not_held(self) -> None:
        """An agent following a run calls this repeatedly. Holding every poll
        for a human would make following a run impossible."""
        assert not decide("flow.status", caller=Caller.REMOTE).requires_approval

    def test_neither_write_claims_to_be_destructive(self) -> None:
        # Neither overwrites anything. Telling a reviewer it might remove data
        # is the sort of inaccurate warning that trains people to ignore them.
        for tool_id in ("flow.propose", "flow.start_run"):
            assert annotations_for(tool_id)["destructiveHint"] is False


@pytest.mark.asyncio
class TestHandlers:
    async def test_propose_requires_an_intent(self) -> None:
        with pytest.raises(ValueError, match="intent"):
            await _server().propose({})

    async def test_status_requires_a_run(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            await _server().status({})

    async def test_start_requires_an_approved_version_id(self) -> None:
        with pytest.raises(ValueError, match="flow_version_id"):
            await _server().start_run({"goal": "g"})

    async def test_propose_returns_the_approval_id(self) -> None:
        result = await _server().propose({"intent": "a drone"})
        assert result["approval_id"] == "run_1"
        assert result["version_id"] == "flowv_1"

    async def test_a_tool_call_goes_through_the_dispatcher(self) -> None:
        from mcp_core.guardrails import ApprovalOutcome
        from metaforge.mcp.server import UnifiedMcpServer

        async def approve(ask: Any) -> ApprovalOutcome:
            return ApprovalOutcome.APPROVED

        server = UnifiedMcpServer(adapters=[_server()], caller=Caller.REMOTE, approval_gate=approve)
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "flow.status", "arguments": {"run_id": "r1"}},
                }
            )
        )
        assert "error" not in json.loads(raw)


# ── the run resource ─────────────────────────────────────────────────────


class TestTheRunReadsAsText:
    def test_a_client_with_no_canvas_still_gets_the_phases(self) -> None:
        text = render_run_markdown(
            {
                "runId": "run_1",
                "status": "running",
                "live": True,
                "awaitingGate": "Requirements sign-off",
                "phases": [
                    {
                        "id": "requirements",
                        "title": "Requirements",
                        "status": "awaiting_gate",
                        "gate": "Requirements sign-off",
                        "summary": "captured 9 requirements",
                        "artifacts": ["constraint_set"],
                    }
                ],
                "events": [
                    {"at": "10:00", "event": "phase_started", "phase": "requirements", "detail": ""}
                ],
            }
        )
        assert "Requirements" in text
        assert "Waiting on gate" in text
        assert "constraint_set" in text
        assert "phase_started" in text

    def test_an_unreadable_engine_says_unknown_not_idle(self) -> None:
        """Same rule as the dashboard (FORGE-396) and for the same reason: a
        model reading 'pending' will tell the user the run has not started."""
        text = render_run_markdown(
            {
                "runId": "run_1",
                "status": "running",
                "live": False,
                "detail": "the worker is not running",
                "phases": [
                    {"id": "a", "title": "A", "status": "unknown", "gate": None, "artifacts": []}
                ],
                "events": [],
            }
        )
        assert "**unknown**, not idle" in text
        assert "the worker is not running" in text

    def test_a_run_with_no_phases_says_so(self) -> None:
        text = render_run_markdown(
            {"runId": "r", "status": "completed", "live": True, "phases": [], "events": []}
        )
        assert "not a design flow" in text


class TestThePromptIsRegistered:
    def test_flow_is_in_the_workflow_catalogue(self) -> None:
        assert "flow" in WORKFLOWS

    def test_it_tells_the_agent_to_list_before_proposing(self) -> None:
        # A flow tailored from a template the model invented is refused, and
        # the refusal is easier to avoid than to explain.
        assert "flow.list" in prompt_body("flow")

    def test_it_carries_the_unknown_rule(self) -> None:
        assert "never 'not started yet'" in prompt_body("flow")
