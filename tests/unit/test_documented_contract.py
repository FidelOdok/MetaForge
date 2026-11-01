"""What capability-matrix.md promises, checked through the HTTP app.

FORGE-387 found a guarantee the docs stated and the server did not keep:
writes from a remote caller were documented as held, and ran. It survived
because every test that touched it constructed the server directly, with
arguments the real transport never passes -- so the contract was verified
everywhere except where it is actually assembled.

These go through ``build_http_app`` for that reason. They are shallow on
purpose: each one is a sentence from the docs, checked against the thing
a client would see.
"""

from __future__ import annotations

from typing import Any

import pytest

from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Twin(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for tool_id in ("twin.get_node", "twin.commit_geometry"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._ok,
            )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"node_id": "n-1"}


@pytest.fixture
def client():
    starlette = pytest.importorskip("starlette.testclient")
    from metaforge.mcp.__main__ import build_http_app

    server = UnifiedMcpServer([_Twin()])
    return starlette.TestClient(build_http_app(server, enable_sse=False))


def _rpc(client, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": "v", "method": method, "params": params or {}}
    )
    return response.json() if response.content else {}


def _init(client, **caps: Any) -> dict[str, Any]:
    return _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": caps,
            "clientInfo": {"name": "verifier", "version": "1"},
        },
    )["result"]


# ---------------------------------------------------------------------------
# "A tool that writes is held for a human when the request comes from a
# remote caller, whatever client asked."
# ---------------------------------------------------------------------------


def test_a_write_over_http_is_held(client) -> None:
    """The one that was false. Asserted through the app, not against a
    hand-built server, because the bug was that the app never passed the
    argument the hand-built servers were given."""
    _init(client)
    error = _rpc(client, "tools/call", {"name": "twin.commit_geometry", "arguments": {}})["error"]
    assert error["data"]["code"] == "approval_required"


def test_a_read_over_http_is_not_held(client) -> None:
    """The gate has to let reads through, or it is just an outage."""
    _init(client)
    assert "result" in _rpc(client, "tools/call", {"name": "twin.get_node", "arguments": {}})


# ---------------------------------------------------------------------------
# "health/check ... status is healthy only when every adapter answered"
# ---------------------------------------------------------------------------


def test_health_answers_the_four_doctor_questions(client) -> None:
    _init(client)
    report = _rpc(client, "health/check")["result"]
    assert report["status"] in ("healthy", "degraded")
    assert report["auth"]["mode"] in ("open", "api_key", "oauth", "api_key+oauth", "unknown")
    assert "dashboard_links" in report
    assert report["client"]["name"] == "verifier"


def test_adapters_report_registration_not_availability(client) -> None:
    """`tools_registered`, never `tools_available`: the number comes from
    the registry and does not change when an adapter dies."""
    _init(client)
    for adapter in _rpc(client, "health/check")["result"]["adapters"]:
        assert "tools_registered" in adapter
        assert "tools_available" not in adapter
        assert "reachable" in adapter


# ---------------------------------------------------------------------------
# "Every tool reports readOnlyHint, destructiveHint, idempotentHint and
# openWorldHint on tools/list"
# ---------------------------------------------------------------------------


def test_every_tool_is_annotated(client) -> None:
    tools = _rpc(client, "tools/list")["result"]["tools"]
    assert tools
    for tool in tools:
        assert set(tool["annotations"]) >= {
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }


# ---------------------------------------------------------------------------
# "resources/list now returns concrete resources only ... and
# resources/templates/list returns the project templates"
# ---------------------------------------------------------------------------


def test_templates_are_served_under_the_spec_method_and_field(client) -> None:
    assert _rpc(client, "resources/list")["result"]["resources"] == []
    result = _rpc(client, "resources/templates/list")["result"]
    assert "resourceTemplates" in result
    for entry in result["resourceTemplates"]:
        assert "uriTemplate" in entry
        assert "uri_template" not in entry


# ---------------------------------------------------------------------------
# Protocol negotiation and elicitation
# ---------------------------------------------------------------------------


def test_the_client_revision_is_negotiated(client) -> None:
    assert _init(client)["protocolVersion"] == "2025-06-18"


def test_http_cannot_elicit_and_says_so(client) -> None:
    """A plain HTTP POST has no server-to-client channel, so declaring the
    capability is not enough. Reporting `can_elicit: true` here would send
    an approval into a channel that does not exist."""
    _init(client, elicitation={})
    assert _rpc(client, "health/check")["result"]["client"]["can_elicit"] is False


# ---------------------------------------------------------------------------
# "An unknown tool names the ones that exist"
# ---------------------------------------------------------------------------


def test_an_unknown_tool_suggests_the_near_miss(client) -> None:
    error = _rpc(client, "tools/call", {"name": "twin.get_nod", "arguments": {}})["error"]
    assert "twin.get_node" in error["data"]["did_you_mean"]


def test_the_workflows_are_served_as_prompts(client) -> None:
    names = {p["name"] for p in _rpc(client, "prompts/list")["result"]["prompts"]}
    assert {"connect", "doctor", "use", "new"} <= names


def test_a_result_carries_its_call_id(client) -> None:
    """A reply claiming "I did X" can name a call id, and that id either
    appears in the session timeline or the claim is unsupported."""
    _init(client)
    result = _rpc(client, "tools/call", {"name": "twin.get_node", "arguments": {}})["result"]
    assert result["_meta"]["callId"]
