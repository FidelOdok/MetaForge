"""Doctor has to work without a shell (FORGE-409).

`/metaforge:doctor` told the agent to call `health/check`, `tools/list` and
`resources/list`. Those are JSON-RPC *methods*. A harness exposes *tools*, so
the agent could not call them — in the FORGE-409 test it reached for Bash and
curl, which means doctor worked only where a shell was available and not at
all in Codex, ChatGPT or claude.ai.

The protocol method stays (transports and the gateway use it). What is new is
a `health.check` tool and a `metaforge://health/connection` resource over the
same report.

Two findings came out of building it, both recorded below: a tool with no
annotation entry is treated as destructive, so the diagnostic was held for
approval; and a resource reader that returns one block instead of a list of
blocks yields the block's *keys* as the response content, silently.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.annotations import annotations_for
from mcp_core.guardrails import Caller, decide
from mcp_core.workflows import prompt_body
from metaforge.mcp.health_adapter import HEALTH_RESOURCE_URI, render_health_markdown


async def _call(server: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    return json.loads(raw)


def _server(**kwargs: Any) -> Any:
    from metaforge.mcp.server import UnifiedMcpServer

    return UnifiedMcpServer(adapters=[], **kwargs)


@pytest.mark.asyncio
class TestHealthIsReachableAsATool:
    async def test_the_tool_is_registered(self) -> None:
        assert "health.check" in _server()._tool_index

    async def test_an_untrusted_caller_can_run_it(self) -> None:
        """The whole point. A plugin connects as an untrusted HTTP caller; a
        diagnostic it cannot run is a diagnostic that does not exist."""
        response = await _call(
            _server(caller=Caller.UNTRUSTED),
            "tools/call",
            {"name": "health.check", "arguments": {}},
        )
        assert "error" not in response, response
        body = json.loads(response["result"]["content"][0]["text"])
        assert (body.get("data") or body)["status"]

    async def test_it_reports_the_adapters_not_just_a_status(self) -> None:
        response = await _call(
            _server(caller=Caller.UNTRUSTED),
            "tools/call",
            {"name": "health.check", "arguments": {}},
        )
        report = json.loads(response["result"]["content"][0]["text"])
        data = report.get("data") or report
        # An agent with no shell has to be able to name what is down.
        assert isinstance(data.get("adapters"), list)
        assert "auth" in data


class TestItIsClassifiedAsARead:
    def test_health_check_is_read_only(self) -> None:
        assert annotations_for("health.check")["readOnlyHint"] is True

    def test_and_is_therefore_not_held(self) -> None:
        """The first version of this was held for approval, because a tool with
        no entry in `annotations` inherits the destructive default. Same shape
        as FORGE-407: a read refused because nothing had classified it."""
        assert not decide("health.check", caller=Caller.UNTRUSTED).requires_approval


@pytest.mark.asyncio
class TestHealthIsAlsoAResource:
    async def test_it_reads(self) -> None:
        response = await _call(
            _server(caller=Caller.UNTRUSTED), "resources/read", {"uri": HEALTH_RESOURCE_URI}
        )
        assert "error" not in response, response
        text = response["result"]["contents"][0]["text"]
        assert "MetaForge connection" in text

    async def test_the_uri_conforms_to_the_scheme(self) -> None:
        """`metaforge://health` alone is rejected as malformed — the scheme is
        `metaforge://<adapter>/<path>`. Worth a test because the failure
        surfaced only at read time, not registration."""
        from mcp_core.resources import parse_resource_uri

        parse_resource_uri(HEALTH_RESOURCE_URI)  # must not raise

    async def test_contents_is_a_list_of_blocks_not_a_block(self) -> None:
        """The silent failure. `list()` on a mapping yields its keys, so a
        reader returning one block answers successfully with
        ["uri", "mimeType", "text"] and no content. Two adapters shipped that
        way before anyone noticed."""
        response = await _call(
            _server(caller=Caller.UNTRUSTED), "resources/read", {"uri": HEALTH_RESOURCE_URI}
        )
        contents = response["result"]["contents"]
        assert isinstance(contents[0], dict), contents
        assert contents[0]["text"]


class TestTheDispatcherRejectsASingleBlock:
    @pytest.mark.asyncio
    async def test_a_mapping_returning_reader_fails_loudly(self) -> None:
        """Nothing about the old behaviour looked like a failure, which is why
        it survived two adapters. It now raises with the fix in the message."""
        from tool_registry.mcp_server.handlers import (
            ResourceManifestEntry,
            ResourceReadError,
        )
        from tool_registry.mcp_server.server import McpToolServer

        class _Bad(McpToolServer):
            def __init__(self) -> None:
                super().__init__(adapter_id="bad", version="0.1.0")
                self.register_resource(
                    manifest=ResourceManifestEntry(
                        uri_template="metaforge://bad/thing",
                        adapter_id="bad",
                        name="bad",
                        description="returns one block",
                        mime_type="text/plain",
                    ),
                    reader=self._read,
                    matcher=lambda uri: uri == "metaforge://bad/thing",
                )

            async def _read(self, uri: str) -> dict[str, Any]:
                return {"uri": uri, "mimeType": "text/plain", "text": "hi"}

        raw = await _Bad().handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "resources/read",
                    "params": {"uri": "metaforge://bad/thing"},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response
        # The actionable text lives in `data.details`; `message` is the
        # generic envelope.
        details = response["error"]["data"]["details"]
        assert "list of blocks" in details
        assert "wrap it in [ ]" in details
        del ResourceReadError  # imported to document what is raised


class TestTheDoctorPromptMatchesReality:
    def test_it_tells_the_agent_to_call_the_tool(self) -> None:
        body = prompt_body("doctor")
        assert "health.check" in body

    def test_it_no_longer_tells_the_agent_to_call_a_protocol_method(self) -> None:
        # The bug, stated as an assertion: an instruction a harness cannot
        # follow is worse than no instruction, because the agent improvises.
        assert "Call `health/check`" not in prompt_body("doctor")

    def test_it_warns_about_the_same_server_registered_twice(self) -> None:
        """Only the client can see this; the server has no view of the other
        registration. The test machine had both and every tool appeared
        twice, so calls could land on the wrong gateway."""
        body = prompt_body("doctor")
        assert "twice" in body
        assert "appears more than once" in body


class TestTheMarkdownLeadsWithWhatIsWrong:
    def test_a_degraded_report_names_the_adapters_first(self) -> None:
        text = render_health_markdown(
            {
                "status": "degraded",
                "unreachable_adapters": ["freecad", "calculix"],
                "version": "0.1.0",
                "adapters": [{"adapter_id": "freecad", "reachable": False, "error": "timeout"}],
            }
        )
        assert text.index("freecad") < text.index("Server version")
        assert "A shorter tool list is not the symptom" in text

    def test_a_healthy_report_does_not_invent_problems(self) -> None:
        text = render_health_markdown({"status": "healthy", "version": "0.1.0"})
        assert "healthy" in text
        assert "did not answer" not in text
