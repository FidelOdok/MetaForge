"""Find the gateway, or say plainly there isn't one (FORGE-329).

A2 is "guided connect: auto-detect a local gateway or enter a team URL".
What shipped was the second half: the plugin prompts for a URL with a
localhost default, and nothing checked whether anything was there. Getting
it wrong installed cleanly and then failed on every tool call -- which in
a harness reads as "the plugin is broken", not "you typed the wrong port".

Most of these tests are about *not* finding something. Reporting a random
service as a gateway is worse than finding nothing: the user pastes it in
and gets a stranger set of failures, further from the cause.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from cli.forge_cli.discover import (
    DEFAULT_CANDIDATES,
    GatewayProbe,
    candidates_for,
    describe,
    detect_gateway,
    probe_gateway,
)


def _serve(body: bytes, status: int = 200):
    """A one-shot local HTTP server. Returns (url, shutdown)."""

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
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}/mcp", server.shutdown


def _initialize_result(name: str, version: str = "0.1.0") -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "x",
            "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": name, "version": version},
            },
        }
    ).encode()


# ---------------------------------------------------------------------------
# Finding one
# ---------------------------------------------------------------------------


def test_a_real_gateway_is_found() -> None:
    url, stop = _serve(_initialize_result("metaforge-mcp"))
    try:
        probe = probe_gateway(url, timeout=3.0)
    finally:
        stop()
    assert probe.usable is True
    assert probe.is_metaforge is True
    assert probe.server_version == "0.1.0"
    assert probe.protocol == "2025-06-18"


def test_a_gateway_that_wants_credentials_still_counts() -> None:
    """The URL is right; the token is a separate thing to supply. Saying
    "not found" would send the user looking for the wrong problem."""
    url, stop = _serve(b"{}", status=401)
    try:
        probe = probe_gateway(url, timeout=3.0)
    finally:
        stop()
    assert probe.usable is True
    assert probe.requires_auth is True
    assert "credentials" in probe.detail


# ---------------------------------------------------------------------------
# Not finding one
# ---------------------------------------------------------------------------


def test_some_other_service_on_the_port_is_not_a_gateway() -> None:
    url, stop = _serve(b'{"hello":"i am something else"}')
    try:
        probe = probe_gateway(url, timeout=3.0)
    finally:
        stop()
    assert probe.reachable is True
    assert probe.usable is False


def test_another_mcp_server_is_not_a_gateway() -> None:
    """The sharpest case: it answers `initialize` perfectly happily. Only
    the name distinguishes it."""
    url, stop = _serve(_initialize_result("github-mcp"))
    try:
        probe = probe_gateway(url, timeout=3.0)
    finally:
        stop()
    assert probe.usable is False
    assert "github-mcp" in probe.detail


def test_nothing_listening_is_reported_with_its_reason() -> None:
    probe = probe_gateway("http://127.0.0.1:9/mcp", timeout=1.0)
    assert probe.reachable is False
    assert probe.detail


def test_a_non_http_url_is_refused_without_a_request() -> None:
    assert probe_gateway("file:///etc/passwd").reachable is False


def test_an_http_error_is_not_a_match() -> None:
    url, stop = _serve(b"nope", status=404)
    try:
        probe = probe_gateway(url, timeout=3.0)
    finally:
        stop()
    assert probe.usable is False
    assert "404" in probe.detail


# ---------------------------------------------------------------------------
# Which candidates, in what order
# ---------------------------------------------------------------------------


def test_the_configured_gateway_is_tried_first() -> None:
    """Someone who set METAFORGE_GATEWAY_URL has already said where their
    gateway is; probing localhost ahead of it could hand them a different
    one."""
    assert candidates_for("http://team.example.com:8000")[0] == "http://team.example.com:8000/mcp"


def test_the_sidecar_port_is_derived_from_the_same_host() -> None:
    """In the standard compose file the gateway is :8000 and the MCP
    sidecar is a separate service on :8765, so <gateway>/mcp 404s and the
    right answer is the same host on 8765."""
    assert "http://team.example.com:8765/mcp" in candidates_for("http://team.example.com:8000")


def test_a_derived_url_is_still_verified() -> None:
    """Deriving is a guess. It only becomes an answer once something at
    the other end says it is MetaForge -- which is what detect_gateway
    does with each candidate."""
    found, probes = detect_gateway(("http://127.0.0.1:9/mcp",), timeout=1.0)
    assert found is None
    assert len(probes) == 1


def test_no_configured_url_falls_back_to_the_local_defaults() -> None:
    assert candidates_for("") == DEFAULT_CANDIDATES


def test_detect_returns_every_probe_not_just_the_winner() -> None:
    """ "Nothing found" is only actionable if the user can see what was
    tried and what each one said."""
    found, probes = detect_gateway(
        ("http://127.0.0.1:9/mcp", "http://127.0.0.1:10/mcp"), timeout=1.0
    )
    assert found is None
    assert len(probes) == 2


# ---------------------------------------------------------------------------
# What the user reads
# ---------------------------------------------------------------------------


def test_the_failure_message_names_every_option() -> None:
    _, probes = detect_gateway(("http://127.0.0.1:9/mcp",), timeout=1.0)
    text = describe(None, probes)
    assert "docker compose up gateway" in text
    assert "team or hosted gateway" in text
    assert "metaforge-local" in text  # the no-gateway package from FORGE-374


def test_the_success_message_gives_the_url_to_paste() -> None:
    found = GatewayProbe("http://x/mcp", True, True, server_version="0.1.0", protocol="2025-06-18")
    text = describe(found, [found])
    assert "http://x/mcp" in text
    assert "gateway URL" in text


def test_an_authed_gateway_is_told_it_needs_a_token() -> None:
    found = GatewayProbe("http://x/mcp", True, False, requires_auth=True)
    assert "api_token" in describe(found, [found])
