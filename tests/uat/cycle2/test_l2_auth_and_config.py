"""UAT-C2-L2 — Transport auth + .mcp.json (MET-338, MET-339).

Acceptance bullets validated:

* MET-338: ``verify_api_key`` constant-time compare, server-side stdio
  auth check rejects mismatch with ``auth_error``, HTTP server rejects
  with HTTP 401, ``/health`` remains open.
* MET-339: Repo-root ``.mcp.json`` is parseable JSON with the expected
  ``mcpServers.metaforge`` schema.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from mcp_core.auth import AUTH_DENIED, redact, verify_api_key
from metaforge.mcp.__main__ import build_http_app
from metaforge.mcp.server import UnifiedMcpServer
from tests.uat.conftest import REPO_ROOT, assert_validates
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

pytestmark = [pytest.mark.uat]


# ---------------------------------------------------------------------------
# MET-338 — Auth helper + redact
# ---------------------------------------------------------------------------


def test_met338_verify_api_key_open_mode_when_expected_unset() -> None:
    assert_validates(
        "MET-338",
        "open mode (no key configured) accepts every connection",
        verify_api_key("anything", None).ok and verify_api_key(None, None).ok,
    )


def test_met338_verify_api_key_rejects_missing_and_mismatch() -> None:
    assert_validates(
        "MET-338",
        "missing key rejected when expected is set",
        not verify_api_key(None, "secret").ok,
    )
    assert_validates(
        "MET-338",
        "wrong key rejected when expected is set",
        not verify_api_key("oops", "secret").ok,
    )


def test_met338_verify_api_key_accepts_match() -> None:
    assert_validates(
        "MET-338",
        "matching key passes constant-time compare",
        verify_api_key("secret-123", "secret-123").ok,
    )


def test_met338_redact_keeps_only_last_four_chars() -> None:
    assert_validates(
        "MET-338",
        "redact() preserves last 4 chars and masks the rest",
        redact("supersecret-1234") == "********1234",
        f"got: {redact('supersecret-1234')!r}",
    )


# ---------------------------------------------------------------------------
# MET-338 — HTTP transport auth
# ---------------------------------------------------------------------------


class _StubAdapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="stub", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="stub.echo",
                adapter_id="stub",
                name="echo",
                description="",
                capability="test",
            ),
            self._echo,
        )

    async def _echo(self, args: dict) -> dict:  # type: ignore[type-arg]
        return {"echo": args}


def test_met338_http_rejects_missing_authorization() -> None:
    server = UnifiedMcpServer(adapters=[_StubAdapter()])
    app = build_http_app(server, enable_sse=False, api_key="secret-123")
    client = TestClient(app)
    resp = client.post(
        "/mcp",
        content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
    )
    assert_validates(
        "MET-338",
        "POST /mcp without Authorization returns 401",
        resp.status_code == 401,
        f"got status={resp.status_code}",
    )
    assert_validates(
        "MET-338",
        "rejection payload uses error_type=auth_error",
        resp.json()["detail"]["error_type"] == AUTH_DENIED,
    )


def test_met338_http_health_endpoint_remains_open() -> None:
    server = UnifiedMcpServer(adapters=[_StubAdapter()])
    app = build_http_app(server, enable_sse=False, api_key="secret-123")
    client = TestClient(app)
    resp = client.get("/health")
    assert_validates(
        "MET-338",
        "GET /health returns 200 even with auth enabled",
        resp.status_code == 200,
        f"got status={resp.status_code}",
    )


def test_met338_http_accepts_correct_bearer() -> None:
    server = UnifiedMcpServer(adapters=[_StubAdapter()])
    app = build_http_app(server, enable_sse=False, api_key="secret-123")
    client = TestClient(app)
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer secret-123"},
        content='{"jsonrpc":"2.0","id":"1","method":"tool/list","params":{}}',
    )
    assert_validates(
        "MET-338",
        "matching bearer token returns 200",
        resp.status_code == 200,
        f"got status={resp.status_code}",
    )


# ---------------------------------------------------------------------------
# MET-338 — Stdio transport auth (live subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_met338_stdio_rejects_mismatch_at_launch() -> None:
    """Subprocess with mismatched key emits auth_error on stdout and exits."""
    proc = subprocess.run(
        [sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        env={
            "METAFORGE_MCP_API_KEY": "server-secret",
            "METAFORGE_MCP_CLIENT_KEY": "wrong-key",
            "METAFORGE_ADAPTERS": "cadquery,calculix",
            "PATH": "/usr/bin:/bin",
        },
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert_validates(
        "MET-338",
        "subprocess emits a single auth_error JSON-RPC response on stdout",
        '"auth_error"' in proc.stdout,
        f"stdout snippet: {proc.stdout[:300]!r}",
    )


# ---------------------------------------------------------------------------
# MET-339 — .mcp.json shape
# ---------------------------------------------------------------------------


def test_met339_mcp_json_parses_with_metaforge_entry() -> None:
    payload = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    assert_validates(
        "MET-339",
        ".mcp.json contains an mcpServers.metaforge entry",
        "metaforge" in payload.get("mcpServers", {}),
    )


def test_met339_metaforge_entry_targets_stdio_module() -> None:
    entry = json.loads((REPO_ROOT / ".mcp.json").read_text())["mcpServers"]["metaforge"]
    assert_validates(
        "MET-339",
        ".mcp.json metaforge entry targets `python -m metaforge.mcp ... --transport stdio`",
        entry["command"] == "python"
        and entry["args"][:2] == ["-m", "metaforge.mcp"]
        and "stdio" in entry["args"],
        f"entry: {entry!r}",
    )


def test_met339_config_examples_doc_exists() -> None:
    examples = REPO_ROOT / "docs" / "integrations" / "mcp-config-examples.md"
    assert_validates(
        "MET-339",
        "docs/integrations/mcp-config-examples.md exists",
        examples.exists() and examples.stat().st_size > 1000,
    )
