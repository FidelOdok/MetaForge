"""Captured work has to say who did it (FORGE-366).

Layer A has recorded every MCP tool call as an action since MET-496, but
filed under ``agent_code: "mcp"`` with nothing else in it. A reviewer
reading /sessions could see *what* was done and not who did it, from which
client, or with which model — which is most of the value of having the
timeline at all, and all of the value for an audit.

The distinction these tests mostly exist to hold is between what the server
established and what a client asserted. FORGE-330 fixed a case where a
client-supplied actor header outranked a verified OAuth identity; recording
both under one unlabelled field would give that back by another route.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from mcp_core.context import McpCallContext, with_context
from metaforge.mcp.capture import SessionCapture
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="twin.get_node",
                adapter_id="twin",
                name="twin.get_node",
                description="stub",
                capability="test",
            ),
            self._ok,
        )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


class _Session:
    def __init__(self, sid: str) -> None:
        self.id = sid


class _Store:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def create_session(self, **kw: Any) -> _Session:
        self.created.append(kw)
        return _Session(f"s-{len(self.created)}")

    async def append_event(self, session_id: str, **kw: Any) -> tuple[str, int]:
        self.events.append({"session_id": session_id, **kw})
        return ("e-1", len(self.events))

    async def complete_session(self, *a: Any, **kw: Any) -> None:
        return None


def _initialise(server: UnifiedMcpServer, name: str = "claude-code", version: str = "2.1.4"):
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": name, "version": version},
                    },
                }
            )
        )
    )


# ---------------------------------------------------------------------------
# What the server knows for itself
# ---------------------------------------------------------------------------


def test_the_client_is_stamped_from_the_handshake() -> None:
    server = UnifiedMcpServer([_Adapter()])
    _initialise(server)
    assert server.attribution()["client"] == {"name": "claude-code", "version": "2.1.4"}


def test_no_handshake_means_no_client_claim() -> None:
    """Absent, not empty. An empty client block reads as a client that
    identified itself as nothing."""
    assert "client" not in UnifiedMcpServer([_Adapter()]).attribution()


def test_an_actor_the_server_established_is_marked_verified() -> None:
    server = UnifiedMcpServer([_Adapter()])
    with with_context(
        McpCallContext(session_id=uuid.uuid4(), actor_id="user:fidel", actor_verified=True)
    ):
        att = server.attribution()
    assert att["actor"] == "user:fidel"
    assert att["actor_verified"] is True


def test_a_self_asserted_actor_is_not_verified() -> None:
    """FORGE-330. This flag used to be `actor != "system:unattributed"` --
    "the field is not the default" -- so a client that set
    X-MetaForge-Actor to anything at all was recorded as verified. An
    audit trail that cannot tell a proven identity from a typed-in one is
    not an audit trail."""
    server = UnifiedMcpServer([_Adapter()])
    with with_context(McpCallContext(session_id=uuid.uuid4(), actor_id="user:ceo")):
        att = server.attribution()
    assert att["actor"] == "user:ceo"  # still recorded -- it is what they said
    assert att["actor_verified"] is False


def test_a_header_supplied_actor_is_not_verified() -> None:
    """The path that matters: in open mode the actor comes straight off
    the wire."""
    from mcp_core.context import context_from_headers

    server = UnifiedMcpServer([_Adapter()])
    with with_context(context_from_headers({"X-MetaForge-Actor": "user:ceo"})):
        assert server.attribution()["actor_verified"] is False


def test_the_unattributed_sentinel_is_not_verified() -> None:
    """Open mode and a shared API key both land here. A shared key
    authorises the call and identifies nobody."""
    server = UnifiedMcpServer([_Adapter()])
    att = server.attribution()
    assert att["actor"] == "system:unattributed"
    assert att["actor_verified"] is False


# ---------------------------------------------------------------------------
# What only the client can say
# ---------------------------------------------------------------------------


def test_the_model_is_recorded_as_a_claim() -> None:
    """Nothing on the MCP wire carries the model, so the server cannot
    check it. Recording it unlabelled next to a verified actor would make
    an assertion look like a finding."""
    server = UnifiedMcpServer([_Adapter()])
    att = server.attribution({"_meta": {"model": "claude-opus-5"}})
    assert att["claimed"] == {"model": "claude-opus-5"}
    assert "model" not in att


def test_no_model_claim_adds_nothing() -> None:
    assert "claimed" not in UnifiedMcpServer([_Adapter()]).attribution({"_meta": {}})


def test_a_non_string_model_claim_is_ignored() -> None:
    server = UnifiedMcpServer([_Adapter()])
    assert "claimed" not in server.attribution({"_meta": {"model": {"name": "x"}}})


# ---------------------------------------------------------------------------
# It reaches the store
# ---------------------------------------------------------------------------


def _run_one_call(store: _Store, *, client: str = "claude-code") -> None:
    server = UnifiedMcpServer([_Adapter()], session_capture=SessionCapture(store))
    _initialise(server, name=client)
    with with_context(
        McpCallContext(session_id=uuid.uuid4(), actor_id="user:fidel", actor_verified=True)
    ):
        asyncio.run(
            server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "2",
                        "method": "tools/call",
                        "params": {
                            "name": "twin.get_node",
                            "arguments": {"node_id": "n1"},
                            "_meta": {"model": "claude-opus-5"},
                        },
                    }
                )
            )
        )


def test_the_event_carries_the_whole_stamp() -> None:
    store = _Store()
    _run_one_call(store)
    data = store.events[-1]["data"]
    assert data["actor"] == "user:fidel"
    assert data["actor_verified"] is True
    assert data["client"]["name"] == "claude-code"
    assert data["claimed"]["model"] == "claude-opus-5"


def test_the_session_is_filed_under_the_client() -> None:
    """agent_code is an existing column, so this is the part of the stamp
    that shows up in /sessions with no schema change. A list where every
    row says "mcp" cannot be filtered by who produced it."""
    store = _Store()
    _run_one_call(store, client="codex")
    assert store.created[-1]["agent_code"] == "codex"


def test_a_client_that_never_identified_itself_still_files_under_mcp() -> None:
    store = _Store()
    server = UnifiedMcpServer([_Adapter()], session_capture=SessionCapture(store))
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "2",
                    "method": "tools/call",
                    "params": {"name": "twin.get_node", "arguments": {}},
                }
            )
        )
    )
    assert store.created[-1]["agent_code"] == "mcp"


def test_attribution_never_breaks_a_tool_call() -> None:
    """Capture is best-effort by contract; attribution is a detail of it and
    must not become the thing that fails a call."""
    server = UnifiedMcpServer([_Adapter()])

    class _Exploding:
        def get(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    # A params object whose _meta misbehaves must not propagate.
    att = server.attribution({"_meta": _Exploding()})
    assert isinstance(att, dict)
