"""Exposing a local gateway without exposing more than intended (FORGE-387).

I2a: "serve a local gateway to cloud harnesses via an off-the-shelf
tunnel, **with all controls in the local MCP server**". The tunnel is
cloudflared's job. What is ours is two things the tunnel cannot do:

* refusing to open one over a gateway that is not fit to be public, and
* the server actually enforcing its write gate on the callers that then
  arrive -- which it was not doing.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from cli.forge_cli.tunnel import TUNNEL_COMMANDS, preflight, tunnel_command
from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


def _serve(body: bytes, status: int = 200):
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", server.shutdown


_OK = (
    b'{"jsonrpc":"2.0","id":"x","result":{"protocolVersion":"2025-06-18",'
    b'"serverInfo":{"name":"metaforge-mcp","version":"0.1.0"}}}'
)


# ---------------------------------------------------------------------------
# The pre-flight
# ---------------------------------------------------------------------------


def test_an_open_gateway_is_refused() -> None:
    """The one combination that must be impossible to reach by accident.
    On a laptop an open gateway is the default and harmless; through a
    tunnel it is an unauthenticated write endpoint on the internet."""
    url, stop = _serve(_OK)
    try:
        result = preflight(url, timeout=3.0)
    finally:
        stop()
    assert result.ok is False
    assert "unauthenticated" in result.report()


def test_an_authenticated_gateway_passes() -> None:
    url, stop = _serve(b"{}", status=401)
    try:
        result = preflight(url, timeout=3.0)
    finally:
        stop()
    assert result.ok is True


def test_a_gateway_that_is_not_running_is_refused_with_advice() -> None:
    result = preflight("http://127.0.0.1:9/mcp", timeout=1.0)
    assert result.ok is False
    assert "forge connect" in result.report()


def test_somebody_elses_service_is_not_tunnelled() -> None:
    """Publishing a stranger's service by mistake is worse than failing."""
    url, stop = _serve(b'{"hello":"not metaforge"}')
    try:
        result = preflight(url, timeout=3.0)
    finally:
        stop()
    assert result.ok is False
    assert "somebody else" in result.report()


def test_a_passing_check_still_says_writes_are_held() -> None:
    """Otherwise the first held write looks like the tunnel breaking."""
    url, stop = _serve(b"{}", status=401)
    try:
        result = preflight(url, timeout=3.0)
    finally:
        stop()
    assert "held for approval" in result.report()


# ---------------------------------------------------------------------------
# The tunnel binary
# ---------------------------------------------------------------------------


def test_no_tunnel_installed_is_reported_not_installed_for_you(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binary that opens a public hostname is something the user should
    have chosen to have."""
    monkeypatch.setattr("cli.forge_cli.tunnel.shutil.which", lambda _: None)
    assert tunnel_command(8765) is None


def test_the_forwarded_port_is_the_local_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cli.forge_cli.tunnel.shutil.which", lambda name: "/usr/bin/" + name)
    name, argv = tunnel_command(8765)  # type: ignore[misc]
    assert name in TUNNEL_COMMANDS
    assert argv[-1] == "http://localhost:8765"


# ---------------------------------------------------------------------------
# The control the tunnel depends on
# ---------------------------------------------------------------------------


class _Spy(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.ran: list[str] = []
        self.register_tool(
            ToolManifest(
                tool_id="twin.commit_geometry",
                adapter_id="twin",
                name="x",
                description="d",
                capability="c",
            ),
            self._ok,
        )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        self.ran.append("twin.commit_geometry")
        return {}


def test_a_tunnelled_write_is_held() -> None:
    """The finding this ticket rests on: nothing ever set `caller`, the
    default was LOCAL, and `exempt_local_writes` therefore waved every
    HTTP caller's writes straight through. A tunnel on top of that would
    have published an ungated write endpoint."""
    spy = _Spy()
    server = UnifiedMcpServer(adapters=[spy], caller=Caller.UNTRUSTED)
    with pytest.raises(Exception):
        asyncio.run(server._authorise("twin.commit_geometry", {}))
    assert spy.ran == []


def test_the_conservative_value_is_the_default() -> None:
    """A default that is the most permissive value is how a guardrail
    goes missing quietly."""
    assert UnifiedMcpServer(adapters=[_Spy()])._caller is Caller.UNTRUSTED


def test_stdio_still_runs_local_writes() -> None:
    spy = _Spy()
    server = UnifiedMcpServer(adapters=[spy], caller=Caller.LOCAL)
    asyncio.run(server._authorise("twin.commit_geometry", {}))


def test_the_http_transport_declares_a_remote_caller() -> None:
    """The ratchet. Losing this line again silently restores the bug."""
    import inspect

    from metaforge.mcp import __main__ as entry

    source = inspect.getsource(entry.build_http_app)
    assert "declare_caller(" in source
    assert "Caller.REMOTE if identifies else Caller.UNTRUSTED" in source


def test_the_stdio_transport_declares_local() -> None:
    import inspect

    from metaforge.mcp import __main__ as entry

    assert "declare_caller(Caller.LOCAL)" in inspect.getsource(entry.run_stdio)
