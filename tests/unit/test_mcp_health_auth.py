"""health/check has to answer the other three A5 questions (FORGE-332).

A5 asks /metaforge:doctor about four things: gateway, adapters, auth and
version skew. The adapter probe covered one. The other three had no source:

* Nothing told the server what the transport in front of it enforces, so
  the doctor prompt's "report the auth mode" could only be answered by
  inventing one. Open mode -- no API key, no OAuth, every connection
  accepted -- is what an unset environment variable gives you, so it is
  the state most likely to be in force without anyone having chosen it.
* ``_initialize`` discarded ``params`` whole, including the only two
  facts about the other end the server ever learns: who connected, and
  which protocol revision they asked for.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest

from mcp_core.auth import AuthPosture
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description="stub",
        capability="test",
    )


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(_manifest("twin.get_node", "twin"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}


def _server() -> UnifiedMcpServer:
    return UnifiedMcpServer([_Adapter()], version="0.9.9")


def _health(server: UnifiedMcpServer) -> dict[str, Any]:
    return asyncio.run(server._health_check())


# ---------------------------------------------------------------------------
# Auth posture
# ---------------------------------------------------------------------------


def test_undeclared_posture_says_unknown_not_open() -> None:
    """The dangerous default is to guess.

    A server nobody told is not the same as a server with nothing
    configured, and reporting "open" for both would make a real open-mode
    deployment indistinguishable from a reporting gap.
    """
    auth = _health(_server())["auth"]
    assert auth["mode"] == "unknown"
    assert "Assume nothing" in auth["detail"]


def test_open_mode_is_reported_and_explained() -> None:
    server = _server()
    server.declare_auth_posture(AuthPosture(transport="stdio"))
    auth = _health(server)["auth"]
    assert auth["mode"] == "open"
    assert auth["transport"] == "stdio"
    assert auth["identifies_caller"] is False
    # Not just a mode string: the consequence, so a report that quotes the
    # detail verbatim still tells the reader what is wrong.
    assert "every connection" in auth["detail"]


@pytest.mark.parametrize(
    ("posture", "mode", "identifies"),
    [
        (AuthPosture(api_key=True, transport="http"), "api_key", False),
        (AuthPosture(oauth=True, transport="http"), "oauth", False),
        (AuthPosture(api_key=True, oauth=True, transport="http"), "api_key+oauth", False),
        (
            AuthPosture(oauth=True, transport="http", identifies_caller=True),
            "oauth",
            True,
        ),
    ],
)
def test_configured_modes(posture: AuthPosture, mode: str, identifies: bool) -> None:
    """A shared credential authorises the call and identifies nobody.

    FORGE-330: that is true of the shared-secret OAuth login too, not only
    the static API key -- one secret held by a team proves someone on the
    team, never which one. So ``identifies_caller`` is declared by the
    transport rather than inferred from "OAuth is on", and only an
    identity provider that authenticates individuals sets it.
    """
    server = _server()
    server.declare_auth_posture(posture)
    auth = _health(server)["auth"]
    assert auth["mode"] == mode
    assert auth["identifies_caller"] is identifies


def test_a_shared_credential_says_what_it_can_and_cannot_prove() -> None:
    """Not silent: a reader seeing `mode: oauth` would otherwise assume
    the timeline names people."""
    server = _server()
    server.declare_auth_posture(AuthPosture(oauth=True, transport="http"))
    detail = _health(server)["auth"]["detail"]
    assert "shared credential" in detail
    assert "not to a person" in detail


def test_a_real_identity_provider_has_nothing_to_warn_about() -> None:
    server = _server()
    server.declare_auth_posture(AuthPosture(oauth=True, transport="http", identifies_caller=True))
    assert "detail" not in _health(server)["auth"]


# ---------------------------------------------------------------------------
# Client identity and protocol skew
# ---------------------------------------------------------------------------


def test_no_handshake_reports_not_connected() -> None:
    """Rather than an anonymous client that failed to identify itself."""
    client = _health(_server())["client"]
    assert client["connected"] is False
    assert client["protocol_negotiated"] == UnifiedMcpServer._DEFAULT_PROTOCOL_VERSION


def test_initialize_is_remembered() -> None:
    server = _server()
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": UnifiedMcpServer._DEFAULT_PROTOCOL_VERSION,
                        "clientInfo": {"name": "claude-code", "version": "2.1.4"},
                    },
                }
            )
        )
    )
    assert "result" in json.loads(raw)
    client = _health(server)["client"]
    assert client["connected"] is True
    assert client["name"] == "claude-code"
    assert client["version"] == "2.1.4"
    assert "protocol_skew" not in client


def test_protocol_skew_is_named() -> None:
    """Pinning is right. Pinning silently is not.

    A client asking for a revision this server does not speak still gets a
    working connection, minus anything added after the pinned revision --
    which is exactly the sort of thing a bug report describes as "the tool
    is just missing".
    """
    server = _server()
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2099-01-01",
                        "clientInfo": {"name": "future-harness", "version": "9.0"},
                    },
                }
            )
        )
    )
    client = _health(server)["client"]
    assert client["protocol_skew"] is True
    assert client["protocol_requested"] == "2099-01-01"
    # FORGE-416: the newest revision we support, not `_MCP_PROTOCOL_VERSION`
    # (our oldest), which is what this used to assert -- and which is why
    # Claude Code never got elicitation.
    assert client["protocol_negotiated"] == max(UnifiedMcpServer._SUPPORTED_PROTOCOL_VERSIONS)
    assert "not available" in client["detail"]


def test_malformed_client_info_does_not_break_the_report() -> None:
    """A health call is what you reach for when things are already odd."""
    server = _server()
    asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "initialize",
                    "params": {"protocolVersion": 2024, "clientInfo": "claude-code"},
                }
            )
        )
    )
    client = _health(server)["client"]
    assert client["connected"] is False


# ---------------------------------------------------------------------------
# The entrypoints must actually declare a posture
# ---------------------------------------------------------------------------


def test_http_app_declares_posture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise every real deployment reports ``unknown`` and the block is
    decoration. This is the ratchet that keeps it wired."""
    from metaforge.mcp import __main__ as entry

    server = _server()
    entry.build_http_app(server, enable_sse=False, api_key="s3cret")
    auth = _health(server)["auth"]
    assert auth["mode"] == "api_key"
    assert auth["transport"] == "http"


def test_stdio_declares_posture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drives the real ``run_stdio`` against an immediately-closed stdin.

    Asserting a copy of the production logic here would pass whatever the
    entrypoint did, which is the failure this whole ticket is about.
    """
    from metaforge.mcp import __main__ as entry

    monkeypatch.delenv("METAFORGE_MCP_API_KEY", raising=False)
    server = _server()

    read_fd, write_fd = os.pipe()
    os.close(write_fd)  # instant EOF: run_stdio sets up, then falls out
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        asyncio.run(entry.run_stdio(server))
    finally:
        stdin.close()

    auth = _health(server)["auth"]
    assert auth["mode"] == "open"
    assert auth["transport"] == "stdio"


def test_stdio_with_a_key_reports_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from metaforge.mcp import __main__ as entry

    monkeypatch.setenv("METAFORGE_MCP_API_KEY", "s3cret")
    monkeypatch.setenv("METAFORGE_MCP_CLIENT_KEY", "s3cret")
    server = _server()

    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        asyncio.run(entry.run_stdio(server))
    finally:
        stdin.close()

    assert _health(server)["auth"]["mode"] == "api_key"
