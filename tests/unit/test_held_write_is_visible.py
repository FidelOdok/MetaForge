"""A held write must not look like an ordinary one (FORGE-417).

`twin.record_decision` from the plugin was held (`run_b42aa3ea023f46c0`,
awaiting_approval, "writes; held for approval (untrusted caller)"), approved
from the queue 8 s later, then executed. The tool result was a plain success
envelope with no sign of any of that. The agent read it and told the user:

    "writes from this external harness are not being held for approval"

False, and contradicted by the gateway ledger entry sitting right there. The
guardrail worked; the client could not tell, so the model filled the gap with
a guess. That is a grounding failure (FORGE-362), not an approval failure.

So the result now carries the hold in `_meta` **and** as a second content
block. `_meta` alone is what "technically present and never read" looks like.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.guardrails import (
    ApprovalOutcome,
    ApprovalResolution,
    Approver,
    Caller,
)
from metaforge.mcp.server import ApprovalRecord, UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

_RUN_ID = "run_b42aa3ea023f46c0"


class _Twin(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="twin.record_decision",
                adapter_id="twin",
                name="record decision",
                description="stub",
                capability="test",
            ),
            self._handler,
        )

    async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"node_id": "abc123"}


def _approving_gate(approver: Approver | None = None, approval_id: str | None = _RUN_ID) -> Any:
    async def gate(_ask: Any) -> ApprovalResolution:
        await asyncio.sleep(0.01)
        return ApprovalResolution(
            outcome=ApprovalOutcome.APPROVED,
            approver=approver,
            approval_id=approval_id,
        )

    return gate


async def _call(server: UnifiedMcpServer) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "twin.record_decision",
                    "arguments": {"title": "Plugin test"},
                },
            }
        )
    )
    return dict(json.loads(raw))


@pytest.mark.asyncio
class TestTheResultSaysItWasHeld:
    async def _held_result(self, **gate_kwargs: Any) -> dict[str, Any]:
        server = UnifiedMcpServer(
            [_Twin()], caller=Caller.UNTRUSTED, approval_gate=_approving_gate(**gate_kwargs)
        )
        response = await _call(server)
        assert "error" not in response, response
        return dict(response["result"])

    async def test_meta_carries_the_outcome_and_the_ledger_id(self) -> None:
        """The id is the point: a claim about an approval should cite
        something somebody can look up."""
        approval = (await self._held_result())["_meta"]["approval"]
        assert approval["held"] is True
        assert approval["outcome"] == "approved"
        assert approval["approvalId"] == _RUN_ID

    async def test_meta_carries_how_long_it_was_held(self) -> None:
        approval = (await self._held_result())["_meta"]["approval"]
        assert approval["heldSeconds"] > 0

    async def test_meta_names_the_approver_when_the_gate_identified_one(self) -> None:
        approver = Approver(actor_id="user:fidel", verified=True, display_name="Fidel")
        approval = (await self._held_result(approver=approver))["_meta"]["approval"]
        assert approval["approvedBy"] == "Fidel"
        assert approval["approverVerified"] is True

    async def test_an_unverified_approver_is_marked_as_such(self) -> None:
        """A local gateway runs with auth off and has no identity to check,
        so "approved by Fidel" must not read as a verified signature."""
        approver = Approver(actor_id="local:dashboard", verified=False, display_name="Fidel")
        approval = (await self._held_result(approver=approver))["_meta"]["approval"]
        assert approval["approverVerified"] is False

    async def test_the_route_is_named(self) -> None:
        """Dashboard queue or inline elicitation. FORGE-416 exists because
        Claude Code was silently getting the former."""
        assert (await self._held_result())["_meta"]["approval"]["route"] == "dashboard"


@pytest.mark.asyncio
class TestTheModelCanActuallyReadIt:
    async def test_a_second_content_block_states_the_hold(self) -> None:
        """`_meta` is the right home for structured facts and the wrong place
        to put the only copy: a model reads content blocks."""
        server = UnifiedMcpServer(
            [_Twin()],
            caller=Caller.UNTRUSTED,
            approval_gate=_approving_gate(
                approver=Approver(actor_id="user:fidel", verified=True, display_name="Fidel")
            ),
        )
        result = (await _call(server))["result"]
        assert len(result["content"]) == 2
        sentence = result["content"][1]["text"]
        assert "held for human approval" in sentence
        assert "approved" in sentence
        assert "Fidel" in sentence
        assert _RUN_ID in sentence

    async def test_the_tools_own_output_is_left_alone(self) -> None:
        """The first block belongs to the tool's schema. Merging approval text
        into it would make every adapter's declared output wrong."""
        server = UnifiedMcpServer(
            [_Twin()], caller=Caller.UNTRUSTED, approval_gate=_approving_gate()
        )
        result = (await _call(server))["result"]
        payload = json.loads(result["content"][0]["text"])
        assert "approval" not in payload
        assert payload["data"]["node_id"] == "abc123"


@pytest.mark.asyncio
class TestAnUnheldCallStaysClean:
    async def test_no_approval_block_and_no_approval_meta(self) -> None:
        """The other half of the bug would be claiming a hold that never
        happened -- which would make the agent's report false in the opposite
        direction."""
        server = UnifiedMcpServer([_Twin()], caller=Caller.LOCAL, exempt_local_writes=True)
        result = (await _call(server))["result"]
        assert len(result["content"]) == 1
        assert "approval" not in result["_meta"]


class TestTheRecordRendersHonestly:
    def test_it_does_not_name_an_approver_it_was_not_given(self) -> None:
        record = ApprovalRecord(outcome="approved", route="elicitation", held_seconds=3.4)
        sentence = record.as_sentence()
        # No approver named, and no id cited -- the id is parenthesised, so
        # match that rather than the bare word, which appears in "held for
        # human approval" regardless.
        assert " by " not in sentence
        assert "(approval " not in sentence
        assert "elicitation" in sentence

    def test_an_unverified_identity_is_qualified_in_the_sentence(self) -> None:
        record = ApprovalRecord(
            outcome="approved",
            route="dashboard",
            held_seconds=8.0,
            approver="Fidel",
            approver_verified=False,
        )
        assert "identity unverified" in record.as_sentence()

    def test_a_verified_identity_is_not_qualified(self) -> None:
        record = ApprovalRecord(
            outcome="approved",
            route="dashboard",
            held_seconds=8.0,
            approver="Fidel",
            approver_verified=True,
        )
        assert "identity unverified" not in record.as_sentence()

    def test_meta_omits_what_it_does_not_know(self) -> None:
        meta = ApprovalRecord(outcome="approved", route="elicitation", held_seconds=1.0).as_meta()
        assert "approvalId" not in meta
        assert "approvedBy" not in meta
        assert meta["held"] is True


@pytest.mark.asyncio
class TestRejectedAndTimedOutStayErrors:
    """The ticket is explicit that these keep failing the call. A rejected
    write reported as a success with an "outcome: rejected" note would be a
    far worse version of the same bug."""

    async def _outcome(self, outcome: ApprovalOutcome) -> dict[str, Any]:
        async def gate(_ask: Any) -> ApprovalResolution:
            return ApprovalResolution(outcome=outcome, approval_id=_RUN_ID)

        server = UnifiedMcpServer([_Twin()], caller=Caller.UNTRUSTED, approval_gate=gate)
        return await _call(server)

    @pytest.mark.parametrize("outcome", [ApprovalOutcome.REJECTED, ApprovalOutcome.TIMED_OUT])
    async def test_it_is_an_error_naming_the_outcome(self, outcome: ApprovalOutcome) -> None:
        response = await self._outcome(outcome)
        assert "error" in response
        assert outcome.value in json.dumps(response["error"])


class TestTheRecordStoreDoesNotLeak:
    def test_reading_a_record_removes_it(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        record = ApprovalRecord(outcome="approved", route="dashboard", held_seconds=1.0)
        server._remember_hold("call-1", record)
        assert server.take_approval_record("call-1") is record
        assert server.take_approval_record("call-1") is None

    def test_it_is_capped_so_an_undrained_entry_cannot_accumulate(self) -> None:
        """A legacy-dialect caller never reads these, and a raised error can
        skip the read. An observability aid must not become a leak."""
        server = UnifiedMcpServer(adapters=[])
        for i in range(200):
            server._remember_hold(
                f"call-{i}", ApprovalRecord(outcome="approved", route="d", held_seconds=0.0)
            )
        assert len(server._approval_records) <= 65
