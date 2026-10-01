"""A client ahead of us must be answered with our newest revision (FORGE-416).

Claude Code 2.1.286 sends `protocolVersion: 2025-11-25`. The server answered
`2024-11-05` -- its **oldest** supported revision -- because anything
unsupported fell through to `_DEFAULT_PROTOCOL_VERSION`.

`elicitation/create` exists only from 2025-06-18, so `can_elicit` was false and
every held write went to the dashboard queue instead of being answered inline.
FORGE-360 built inline approvals; this made them unreachable for the client
most likely to use them. The feature worked. Nobody could get to it.

The MCP rule: echo the requested revision when supported, otherwise answer with
another revision the server supports, which SHOULD be the latest.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer

_NEWEST = max(UnifiedMcpServer._SUPPORTED_PROTOCOL_VERSIONS)
_OLDEST = min(UnifiedMcpServer._SUPPORTED_PROTOCOL_VERSIONS)


def _negotiate(requested: str | None) -> str:
    return UnifiedMcpServer._negotiate_protocol(requested)


class TestTheCaseThatWasBroken:
    def test_claude_codes_actual_request_gets_our_newest(self) -> None:
        """The literal observed input, not a stand-in for it."""
        assert _negotiate("2025-11-25") == _NEWEST

    def test_any_revision_newer_than_ours_gets_our_newest(self) -> None:
        for requested in ("2025-07-01", "2026-01-01", "2099-01-01"):
            assert _negotiate(requested) == _NEWEST, requested

    def test_it_is_not_our_oldest(self) -> None:
        """Stated separately because that is the bug, and because of how it
        hid: there used to be two constants holding the same string, so a test
        asserting the server answers "our oldest" compared against one called
        `_MCP_PROTOCOL_VERSION` ("the version we negotiate with") and passed.
        FORGE-416 removed the duplicate; the newest is now derived from
        `_SUPPORTED_PROTOCOL_VERSIONS` rather than copied."""
        assert _negotiate("2025-11-25") != _OLDEST


class TestASupportedRevisionIsEchoed:
    @pytest.mark.parametrize("requested", sorted(UnifiedMcpServer._SUPPORTED_PROTOCOL_VERSIONS))
    def test_each_supported_revision_comes_back_unchanged(self, requested: str) -> None:
        assert _negotiate(requested) == requested


class TestAnOlderClientIsNotBrokenToSatisfyAShould:
    """The deliberate deviation, and why.

    The spec's "SHOULD be the latest" would have us answer 2025-06-18 to a
    client that pinned 2025-03-26. The spec then says such a client SHOULD
    disconnect. Today that client is answered 2024-11-05 and works, so
    following the SHOULD literally would turn a working connection into a
    dropped one -- a regression introduced while fixing a different client.
    """

    def test_a_revision_between_ours_gets_the_newest_one_not_newer_than_it(self) -> None:
        assert _negotiate("2025-03-26") == _OLDEST

    def test_a_revision_older_than_anything_we_speak_gets_our_floor(self) -> None:
        """Nothing better is available. This client may still disconnect, which
        is its right -- but we answered with something real rather than
        claiming a revision it never asked about."""
        assert _negotiate("2024-01-01") == _OLDEST

    def test_the_deviation_only_ever_answers_downward(self) -> None:
        """The invariant that makes it safe: for an unsupported request below
        our newest we never answer with a revision *newer* than asked for --
        unless nothing we support is that old, in which case our floor is the
        only honest answer and the client may decline it."""
        for requested in ("2025-01-01", "2025-03-26", "2025-05-01"):
            answered = _negotiate(requested)
            assert answered <= requested, (requested, answered)
        # Below our floor there is nothing to step down to.
        assert _negotiate("2024-01-01") == _OLDEST


class TestNoVersionAtAll:
    def test_a_client_that_names_no_revision_gets_our_newest(self) -> None:
        """Non-conformant, so there is no expectation to honour; offer the most
        capable thing we have. `can_elicit` stays gated on the client having
        declared the capability, so this cannot make us talk to a client that
        is not listening."""
        assert _negotiate(None) == _NEWEST
        assert _negotiate("") == _NEWEST


def _handshake(server: Any, requested: str, caps: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": requested,
                        "capabilities": caps if caps is not None else {},
                        "clientInfo": {"name": "claude-code", "version": "2.1.286"},
                    },
                }
            )
        )
    )
    return dict(json.loads(raw)["result"])


class TestOverTheRealHandshake:
    def test_the_response_carries_the_negotiated_revision(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        assert _handshake(server, "2025-11-25")["protocolVersion"] == _NEWEST

    def test_skew_is_still_reported_because_we_did_not_give_them_what_they_asked(
        self,
    ) -> None:
        """Answering better than before is not the same as answering what was
        requested. The doctor should keep saying so."""
        server = UnifiedMcpServer(adapters=[])
        _handshake(server, "2025-11-25")
        client = asyncio.run(server._health_check())["client"]
        assert client["protocol_skew"] is True
        assert client["protocol_requested"] == "2025-11-25"
        assert client["protocol_negotiated"] == _NEWEST

    def test_an_echoed_revision_is_not_skew(self) -> None:
        server = UnifiedMcpServer(adapters=[])
        _handshake(server, _NEWEST)
        client = asyncio.run(server._health_check())["client"]
        # The key is only present when there IS skew, so absence is the
        # no-skew answer -- asserted via .get so this reads the same either way.
        assert not client.get("protocol_skew")


class TestWhatThisUnblocks:
    def test_a_client_ahead_of_us_declaring_elicitation_can_now_be_asked(self) -> None:
        """The point of the ticket. `can_elicit` needs all three: a transport
        back to the client, the client's declared capability, and a negotiated
        revision where `elicitation/create` exists. The revision was the one
        failing, silently."""
        server = UnifiedMcpServer(adapters=[])
        server.attach_elicitor(lambda *a, **k: None)
        _handshake(server, "2025-11-25", caps={"elicitation": {}})
        assert server.can_elicit is True

    def test_before_the_fix_that_same_client_could_not_be(self) -> None:
        """Shown by negotiating the old way explicitly: on our floor,
        elicitation is correctly refused, which is why the downgrade was fatal
        rather than merely untidy."""
        server = UnifiedMcpServer(adapters=[])
        server.attach_elicitor(lambda *a, **k: None)
        _handshake(server, _OLDEST, caps={"elicitation": {}})
        assert server.can_elicit is False
