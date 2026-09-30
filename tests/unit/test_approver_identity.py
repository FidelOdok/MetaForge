"""The approver is a human who acted, not a string the model typed (FORGE-393).

``twin.attempt_promotion`` writes down who authorised a maturity promotion.
Until this ticket that name arrived as an ordinary tool argument, so the model
filled it in, and the only thing discouraging it from typing a real engineer's
name was a sentence in the skill text. Over local stdio -- where writes are
exempt from approval because there is nowhere to answer one -- the model's
string was the *only* authority recorded.

The assertion that carries this file is not "an error came back". It is that
the name reaching the stored gate is never one the caller chose. A guardrail
that accepts the model's name and merely logs a warning is worse than none,
because the gate still reads as signed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    ApprovalAsk,
    ApprovalOutcome,
    ApprovalResolution,
    Approver,
    ApproverArgumentRejectedError,
    Caller,
    HumanAuthorityRequiredError,
    decide,
    reject_caller_supplied_approver,
    requires_human_authority,
    resolve_approval,
    strip_reserved_arguments,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

PROMOTION = "twin.attempt_promotion"


# ── The classification ────────────────────────────────────────────────────


class TestTheRuleItself:
    def test_promotion_is_a_human_authority_tool(self) -> None:
        assert requires_human_authority(PROMOTION)

    def test_an_ordinary_write_is_not(self) -> None:
        # The distinction has to stay narrow. If every write demanded a named
        # approver, stdio sessions would stop working and somebody would turn
        # the whole thing off.
        assert not requires_human_authority("twin.commit_geometry")

    def test_the_local_exemption_does_not_cover_it(self) -> None:
        """The actual hole. Local writes are exempt so stdio has a way to
        work at all -- and that exemption silently covered the one tool whose
        output *is* a human's name."""
        assert decide(PROMOTION, caller=Caller.LOCAL, exempt_local_writes=True).requires_approval

    def test_an_ordinary_local_write_is_still_exempt(self) -> None:
        # Negative control for the line above: the fix must not turn the
        # exemption off wholesale.
        assert not decide(
            "twin.commit_geometry", caller=Caller.LOCAL, exempt_local_writes=True
        ).requires_approval

    def test_the_reason_says_where_the_name_comes_from(self) -> None:
        reason = decide(PROMOTION, caller=Caller.LOCAL).reason
        assert "comes from the approval" in reason


class TestTheCallerCannotNameTheApprover:
    @pytest.mark.parametrize("field", ["decided_by", "approver"])
    def test_supplying_one_is_refused(self, field: str) -> None:
        with pytest.raises(ApproverArgumentRejectedError):
            reject_caller_supplied_approver(PROMOTION, {field: "Dr Jane Smith"})

    def test_refused_rather_than_dropped(self) -> None:
        """Quietly ignoring it would leave the model believing it had set the
        authority, and the belief is the thing that produces a confident,
        wrong audit line later."""
        with pytest.raises(ApproverArgumentRejectedError) as exc:
            reject_caller_supplied_approver(PROMOTION, {"decided_by": "Jane"})
        assert "cannot be supplied by the caller" in str(exc.value)

    def test_other_tools_may_still_use_the_word(self) -> None:
        reject_caller_supplied_approver("twin.commit_geometry", {"decided_by": "Jane"})

    def test_the_reserved_key_is_taken_away_from_the_client(self) -> None:
        """Reserving a name only works if it is stripped first. Otherwise it
        is a convention, and a convention is what the model can imitate."""
        cleaned = strip_reserved_arguments({"project_id": "p", APPROVED_BY_ARG: "Jane"})
        assert APPROVED_BY_ARG not in cleaned
        assert cleaned == {"project_id": "p"}


class TestApproverConstruction:
    def test_an_approver_needs_an_identity(self) -> None:
        with pytest.raises(ValueError):
            Approver(actor_id="   ")

    def test_unverified_is_the_honest_default(self) -> None:
        # A local gateway has no identities to verify. Recording the click as
        # unverified is a true statement; recording a name is not.
        assert Approver(actor_id="local:dashboard").verified is False

    def test_a_bare_outcome_carries_no_approver(self) -> None:
        resolved = resolve_approval(ApprovalOutcome.APPROVED)
        assert resolved.outcome is ApprovalOutcome.APPROVED
        assert resolved.approver is None


# ── The dispatcher ────────────────────────────────────────────────────────


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id="twin",
        name=tool_id,
        description=f"stub {tool_id}",
        capability="test",
    )


class _Spy(McpToolServer):
    """Records the arguments each handler actually received."""

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        for tool_id in (PROMOTION, "twin.commit_geometry"):
            self.register_tool(_manifest(tool_id), self._make(tool_id))

    def _make(self, tool_id: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((tool_id, dict(args)))
            return {"ok": True}

        return handler


async def _call(server: UnifiedMcpServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        )
    )
    return json.loads(raw)


def _gate_naming(actor: str, *, verified: bool = True):
    async def gate(ask: ApprovalAsk) -> ApprovalResolution:
        return ApprovalResolution(
            outcome=ApprovalOutcome.APPROVED,
            approver=Approver(actor_id=actor, verified=verified),
        )

    return gate


async def _anonymous_gate(ask: ApprovalAsk) -> ApprovalOutcome:
    """A gate from before this ticket: says yes, says nothing about who."""
    return ApprovalOutcome.APPROVED


@pytest.mark.asyncio
class TestWhatReachesTheTool:
    async def test_the_approver_reaches_the_tool_not_the_models_name(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=_gate_naming("user:abc-123"),
        )
        response = await _call(server, PROMOTION, {"project_id": "p", "level": "released"})
        assert "error" not in response, response

        _, args = spy.calls[0]
        assert args[APPROVED_BY_ARG] == "user:abc-123"

    async def test_a_model_supplied_name_never_runs(self) -> None:
        """The headline. Not 'an error is returned' -- the handler must not
        have run, because a promotion recorded then corrected is a promotion
        that existed."""
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=_gate_naming("user:abc-123"),
        )
        response = await _call(
            server,
            PROMOTION,
            {"project_id": "p", "level": "released", "decided_by": "Dr Jane Smith"},
        )
        assert "error" in response, response
        assert spy.calls == [], "the promotion ran with a model-supplied approver"

    async def test_a_forged_reserved_key_is_stripped_before_it_is_read(self) -> None:
        """A model that has read the source could try the reserved name
        itself. It is removed from client input before anything looks at it,
        so what lands is the real approver."""
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=_gate_naming("user:real-approver"),
        )
        response = await _call(
            server,
            PROMOTION,
            {"project_id": "p", "level": "released", APPROVED_BY_ARG: "Dr Jane Smith"},
        )
        assert "error" not in response, response
        _, args = spy.calls[0]
        assert args[APPROVED_BY_ARG] == "user:real-approver"

    async def test_local_stdio_cannot_promote_without_a_gate(self) -> None:
        """The repro from the ticket. A local caller with no approval surface
        used to promote freely, recording whatever the model typed."""
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.LOCAL)
        response = await _call(server, PROMOTION, {"project_id": "p", "level": "released"})
        assert "error" in response, response
        assert spy.calls == []

    async def test_local_stdio_can_still_do_ordinary_writes(self) -> None:
        # The fix must not take stdio offline for everything else.
        spy = _Spy()
        server = UnifiedMcpServer(adapters=[spy], caller=Caller.LOCAL)
        response = await _call(server, "twin.commit_geometry", {"node_id": "n"})
        assert "error" not in response, response
        assert spy.calls[0][0] == "twin.commit_geometry"

    async def test_an_approval_that_names_nobody_is_not_enough(self) -> None:
        """A gate can approve without identifying anyone -- every gate written
        before this ticket does. For an ordinary write that is fine. Here the
        approver's name is the result, so proceeding would record an authority
        that was never established."""
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy], caller=Caller.REMOTE, approval_gate=_anonymous_gate
        )
        response = await _call(server, PROMOTION, {"project_id": "p", "level": "released"})
        assert "error" in response, response
        assert spy.calls == []

    async def test_an_anonymous_gate_still_serves_ordinary_writes(self) -> None:
        # Negative control: older gates must keep working for everything that
        # is not about recording a human.
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy], caller=Caller.REMOTE, approval_gate=_anonymous_gate
        )
        response = await _call(server, "twin.commit_geometry", {"node_id": "n"})
        assert "error" not in response, response
        assert spy.calls[0][0] == "twin.commit_geometry"

    async def test_an_unverified_local_approver_is_accepted(self) -> None:
        """A click on a local gateway is a real human with an unverifiable
        name. That is recordable; a model-typed name is not. Refusing here
        would break local-first for no safety gain."""
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=_gate_naming("local:dashboard", verified=False),
        )
        response = await _call(server, PROMOTION, {"project_id": "p", "level": "released"})
        assert "error" not in response, response
        assert spy.calls[0][1][APPROVED_BY_ARG] == "local:dashboard"


@pytest.mark.asyncio
class TestTheRefusalIsLegible:
    async def test_it_names_the_offending_argument(self) -> None:
        spy = _Spy()
        server = UnifiedMcpServer(
            adapters=[spy],
            caller=Caller.REMOTE,
            approval_gate=_gate_naming("user:abc"),
        )
        response = await _call(server, PROMOTION, {"project_id": "p", "decided_by": "Jane"})
        assert "decided_by" in json.dumps(response["error"])

    async def test_the_missing_approver_error_says_where_it_comes_from(self) -> None:
        exc = HumanAuthorityRequiredError(PROMOTION, "nobody was identified")
        assert "never" in str(exc) and "tool argument" in str(exc)
