"""Approvals answered inside the harness (FORGE-360).

F1 holds writes but parks them in the dashboard queue, which is why it
exempts local stdio writes by default -- ``guardrails._EXEMPTIBLE`` says in
so many words that the exemption exists only because stdio has nowhere to
answer, and should go once F2 lands.

These tests pin the three things that make it land: the client is only
asked when it can actually be asked, the three-action response maps onto
F1's outcomes without conflating "nobody looked" with "a person said no",
and the stdio loop can carry a question and its answer at the same time.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.elicitation import (
    ElicitAction,
    ElicitResult,
    approval_request,
    elicitation_gate,
)
from mcp_core.guardrails import ApprovalAsk, ApprovalOutcome, Caller
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

ELICIT_VERSION = "2025-06-18"


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id="twin",
        name=tool_id,
        description="stub",
        capability="test",
    )


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(_manifest("twin.get_node"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}


def _ask(**over: Any) -> ApprovalAsk:
    base: dict[str, Any] = {
        "tool_id": "twin.commit_geometry",
        "arguments": {"obj_id": "bracket"},
        "caller": Caller.LOCAL,
        "reason": "writes; held for approval (local caller)",
    }
    base.update(over)
    return ApprovalAsk(**base)


# ---------------------------------------------------------------------------
# The request we put to the user
# ---------------------------------------------------------------------------


def test_schema_stays_inside_the_allowed_subset() -> None:
    """Flat object, primitive properties.

    The spec restricts elicitation schemas to exactly that, and a client is
    not required to render anything richer -- so an over-ambitious schema
    does not fail loudly, it fails as a prompt the user never sees.
    """
    _, schema = approval_request(_ask())
    assert schema["type"] == "object"
    assert schema["required"] == ["approve"]
    for prop in schema["properties"].values():
        assert prop["type"] in {"string", "number", "integer", "boolean"}
        assert "properties" not in prop and "items" not in prop


def test_credentials_are_not_put_on_screen() -> None:
    """The spec forbids *requesting* sensitive information; shipping it into
    the same dialog is the same mistake pointed the other way. A reviewer
    does not need the key to say yes."""
    message, _ = approval_request(
        _ask(arguments={"api_token": "sk-live-abcdef", "obj_id": "bracket"})
    )
    assert "sk-live-abcdef" not in message
    assert "<redacted>" in message
    assert "bracket" in message  # the harmless argument still shows


def test_long_arguments_are_clipped() -> None:
    """Some clients render this in a box the size of a tooltip, and a 60 KB
    CAD script tells a reviewer nothing a summary does not."""
    message, _ = approval_request(_ask(arguments={"script": "x = 1\n" * 5000}))
    assert len(message) < 1000
    assert "…" in message


def test_message_names_the_tool_and_the_caller() -> None:
    message, _ = approval_request(_ask(caller=Caller.REMOTE))
    assert "twin.commit_geometry" in message
    assert "remote" in message


# ---------------------------------------------------------------------------
# Three actions onto F1's outcomes
# ---------------------------------------------------------------------------


def _outcome(result: ElicitResult) -> ApprovalOutcome:
    async def elicitor(message: str, schema: dict[str, Any]) -> ElicitResult:
        return result

    return asyncio.run(elicitation_gate(elicitor)(_ask()))


def test_accept_with_approve_true_runs_it() -> None:
    assert _outcome(ElicitResult(ElicitAction.ACCEPT, {"approve": True})) is (
        ApprovalOutcome.APPROVED
    )


def test_accept_with_approve_false_is_a_rejection() -> None:
    """The user was asked and answered no. That is a decision, not a timeout."""
    assert _outcome(ElicitResult(ElicitAction.ACCEPT, {"approve": False})) is (
        ApprovalOutcome.REJECTED
    )


def test_decline_is_a_rejection() -> None:
    assert _outcome(ElicitResult(ElicitAction.DECLINE)) is ApprovalOutcome.REJECTED


def test_cancel_is_a_timeout_not_a_rejection() -> None:
    """Dismissing a dialog is not saying no.

    F1 keeps TIMED_OUT separate precisely so an agent is not told "a
    reviewer rejected it" when no reviewer looked -- conflating them
    teaches it to retry a refusal.
    """
    assert _outcome(ElicitResult(ElicitAction.CANCEL)) is ApprovalOutcome.TIMED_OUT


@pytest.mark.parametrize("content", [{}, {"approve": "yes"}, {"approve": None}])
def test_accept_without_a_boolean_is_refused(content: dict[str, Any]) -> None:
    """A client that did not honour the schema. Guessing wrong the other way
    is an unreviewed write."""
    assert _outcome(ElicitResult(ElicitAction.ACCEPT, content)) is ApprovalOutcome.REJECTED


# ---------------------------------------------------------------------------
# When the server may ask at all
# ---------------------------------------------------------------------------


async def _elicitor(message: str, schema: dict[str, Any]) -> ElicitResult:
    return ElicitResult(ElicitAction.ACCEPT, {"approve": True})


def _initialise(server: UnifiedMcpServer, *, version: str, capabilities: dict[str, Any]) -> None:
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": version,
                        "capabilities": capabilities,
                        "clientInfo": {"name": "claude-code", "version": "2.1.4"},
                    },
                }
            )
        )
    )


def test_can_elicit_needs_all_three_conditions() -> None:
    both = UnifiedMcpServer([_Adapter()], elicitor=_elicitor)
    _initialise(both, version=ELICIT_VERSION, capabilities={"elicitation": {}})
    assert both.can_elicit is True

    # No transport channel back.
    no_channel = UnifiedMcpServer([_Adapter()])
    _initialise(no_channel, version=ELICIT_VERSION, capabilities={"elicitation": {}})
    assert no_channel.can_elicit is False

    # Client never said it could.
    no_capability = UnifiedMcpServer([_Adapter()], elicitor=_elicitor)
    _initialise(no_capability, version=ELICIT_VERSION, capabilities={})
    assert no_capability.can_elicit is False

    # Capability declared, but on a revision where the method does not
    # exist -- a correct client is not listening for it.
    old_protocol = UnifiedMcpServer([_Adapter()], elicitor=_elicitor)
    _initialise(old_protocol, version="2024-11-05", capabilities={"elicitation": {}})
    assert old_protocol.can_elicit is False


def test_protocol_is_negotiated_not_pinned() -> None:
    """Pinning unconditionally told a client asking for 2025-06-18 that it
    had 2024-11-05, at which point it quite correctly stopped expecting
    anything that revision added -- including the elicitation it had just
    offered to handle."""
    server = UnifiedMcpServer([_Adapter()])
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {"protocolVersion": ELICIT_VERSION, "capabilities": {}},
                }
            )
        )
    )
    assert json.loads(raw)["result"]["protocolVersion"] == ELICIT_VERSION


def test_unsupported_revision_still_falls_back_and_says_so() -> None:
    server = UnifiedMcpServer([_Adapter()])
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {"protocolVersion": "2099-01-01", "capabilities": {}},
                }
            )
        )
    )
    assert json.loads(raw)["result"]["protocolVersion"] == "2024-11-05"
    client = asyncio.run(server._health_check())["client"]
    assert client["protocol_skew"] is True


# ---------------------------------------------------------------------------
# The exemption flip
# ---------------------------------------------------------------------------


def test_a_client_that_can_be_asked_loses_the_local_exemption() -> None:
    """F1 exempts local writes only because stdio had nowhere to answer.

    Once it does, the exemption is the thing standing between a local agent
    and an unreviewed write.
    """
    asked: list[str] = []

    async def recording(message: str, schema: dict[str, Any]) -> ElicitResult:
        asked.append(message)
        return ElicitResult(ElicitAction.ACCEPT, {"approve": True})

    server = UnifiedMcpServer(
        [_Adapter()], caller=Caller.LOCAL, exempt_local_writes=True, elicitor=recording
    )
    _initialise(server, version=ELICIT_VERSION, capabilities={"elicitation": {}})
    asyncio.run(server._authorise("twin.commit_geometry", {"obj_id": "bracket"}))
    assert len(asked) == 1


def test_a_client_that_cannot_be_asked_keeps_it() -> None:
    """Holding with nowhere to answer is an outage, not a guardrail."""
    server = UnifiedMcpServer([_Adapter()], caller=Caller.LOCAL, exempt_local_writes=True)
    _initialise(server, version="2024-11-05", capabilities={})
    # No gate configured, and no exception: the call was never held.
    asyncio.run(server._authorise("twin.commit_geometry", {"obj_id": "bracket"}))


def test_reads_are_never_elicited() -> None:
    asked: list[str] = []

    async def recording(message: str, schema: dict[str, Any]) -> ElicitResult:
        asked.append(message)
        return ElicitResult(ElicitAction.ACCEPT, {"approve": True})

    server = UnifiedMcpServer([_Adapter()], caller=Caller.REMOTE, elicitor=recording)
    _initialise(server, version=ELICIT_VERSION, capabilities={"elicitation": {}})
    asyncio.run(server._authorise("twin.get_node", {"node_id": "n1"}))
    assert asked == []


def test_elicitation_is_preferred_over_the_dashboard_queue() -> None:
    """Not a fallback chain. Asking the dashboard after the client already
    answered would put the same question to a second person and discard the
    first answer."""
    dashboard_calls: list[ApprovalAsk] = []

    async def dashboard(ask: ApprovalAsk) -> ApprovalOutcome:
        dashboard_calls.append(ask)
        return ApprovalOutcome.APPROVED

    server = UnifiedMcpServer(
        [_Adapter()], caller=Caller.REMOTE, elicitor=_elicitor, approval_gate=dashboard
    )
    _initialise(server, version=ELICIT_VERSION, capabilities={"elicitation": {}})
    asyncio.run(server._authorise("twin.commit_geometry", {"obj_id": "bracket"}))
    assert dashboard_calls == []


def test_dashboard_queue_still_serves_clients_that_cannot_elicit() -> None:
    dashboard_calls: list[ApprovalAsk] = []

    async def dashboard(ask: ApprovalAsk) -> ApprovalOutcome:
        dashboard_calls.append(ask)
        return ApprovalOutcome.APPROVED

    server = UnifiedMcpServer([_Adapter()], caller=Caller.REMOTE, approval_gate=dashboard)
    _initialise(server, version="2024-11-05", capabilities={})
    asyncio.run(server._authorise("twin.commit_geometry", {"obj_id": "bracket"}))
    assert len(dashboard_calls) == 1
