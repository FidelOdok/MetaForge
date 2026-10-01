"""A tool's refusal has to reach the model (FORGE-419).

`twin.query_cypher` in a project-scoped session refused with a precise,
actionable reason:

    call context is scoped to project ... but no 'project_id' parameter was
    bound. Add WHERE n.project_id = $project_id and pass {'project_id': ...}

That text lived in JSON-RPC `error.data.details`. Claude Code shows
`error.message`, which was the constant string "Tool execution failed". So
the agent retried a different query, failed the same way, gave up, and
reported `twin.query_cypher` as broken -- when the server had told it exactly
what to change.

The MCP spec's own recommendation covers this: a tool that *ran and refused*
is a result with `isError: true`, not a protocol error, so the model can
self-correct. Protocol errors -- unknown method, unknown tool -- stay
JSON-RPC errors, because there is nothing in the arguments to fix.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.guardrails import Caller
from metaforge.mcp.server import UnifiedMcpServer, _tool_error_text
from tool_registry.mcp_server.handlers import ToolHandlerError, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

_REASON = (
    "call context is scoped to project 7b8a but no 'project_id' parameter was bound. "
    "Add WHERE n.project_id = $project_id and pass {'project_id': ...}"
)


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for tool_id in ("twin.query_cypher", "twin.get_node"):
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._handler,
            )

    async def _handler(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("ok"):
            return {"rows": []}
        raise ValueError(_REASON)


def _server() -> UnifiedMcpServer:
    return UnifiedMcpServer([_Adapter()], caller=Caller.LOCAL, exempt_local_writes=True)


async def _mcp_call(server: UnifiedMcpServer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
    )
    return dict(json.loads(raw))


@pytest.mark.asyncio
class TestTheReasonReachesTheModel:
    async def test_the_refusal_is_a_result_not_a_protocol_error(self) -> None:
        response = await _mcp_call(_server(), "twin.query_cypher", {})
        assert "error" not in response, response
        assert response["result"]["isError"] is True

    async def test_the_text_content_carries_the_actionable_reason(self) -> None:
        """The whole bug in one assertion: the fix was in the message all
        along, and the client never saw it."""
        result = (await _mcp_call(_server(), "twin.query_cypher", {}))["result"]
        text = result["content"][0]["text"]
        assert "project_id = $project_id" in text
        assert "Tool execution failed" not in text

    async def test_the_failing_tool_is_named(self) -> None:
        """A transcript shows several calls; an unattributed error is a guess
        about which one failed."""
        result = (await _mcp_call(_server(), "twin.query_cypher", {}))["result"]
        assert result["content"][0]["text"].startswith("twin.query_cypher failed:")

    async def test_structured_detail_is_still_there_for_clients_that_want_it(self) -> None:
        meta = (await _mcp_call(_server(), "twin.query_cypher", {}))["result"]["_meta"]
        assert meta["error"]["toolId"] == "twin.query_cypher"
        assert _REASON in meta["error"]["details"]
        assert "durationMs" in meta["error"]

    async def test_a_failed_call_is_still_citable(self) -> None:
        """FORGE-362: "I tried this and it refused" is exactly as worth
        checking as a claimed success, so the reference survives the
        conversion."""
        meta = (await _mcp_call(_server(), "twin.query_cypher", {}))["result"]["_meta"]
        assert meta["callId"]

    async def test_a_successful_call_is_untouched(self) -> None:
        result = (await _mcp_call(_server(), "twin.query_cypher", {"ok": True}))["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["status"] == "success"


@pytest.mark.asyncio
class TestProtocolErrorsStayProtocolErrors:
    async def test_an_unknown_tool_is_still_a_json_rpc_error(self) -> None:
        """Nothing in the arguments to fix, and the error already carries a
        did-you-mean. Converting this one would bury it."""
        response = await _mcp_call(_server(), "twin.nope", {})
        assert "error" in response
        assert "result" not in response

    async def test_an_unknown_method_is_still_a_json_rpc_error(self) -> None:
        raw = await _server().handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "nope/nope", "params": {}})
        )
        assert "error" in json.loads(raw)


@pytest.mark.asyncio
class TestTheLegacyDialectIsUnchanged:
    async def test_tool_call_still_returns_a_json_rpc_error(self) -> None:
        """Its callers are internal and already read `data.details`. Changing
        both at once would be a wider blast radius for no gain."""
        raw = await _server().handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tool/call",
                    "params": {"tool_id": "twin.query_cypher", "arguments": {}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response
        assert _REASON in response["error"]["data"]["details"]


class TestTheWording:
    def test_it_leads_with_the_details(self) -> None:
        text = _tool_error_text(ToolHandlerError("twin.query_cypher", _REASON, 1.0))
        assert text == f"twin.query_cypher failed: {_REASON}"

    def test_a_reasonless_failure_says_so_rather_than_being_blank(self) -> None:
        """An empty string would read as "no reason given, so try something
        else" -- which is the behaviour this ticket is fixing."""
        text = _tool_error_text(ToolHandlerError("twin.get_node", "", 1.0))
        assert "gave no reason" in text
        assert "server-side gap" in text
        assert "retrying the same call will fail the same way" in text

    def test_whitespace_only_details_count_as_absent(self) -> None:
        assert "gave no reason" in _tool_error_text(ToolHandlerError("t", "   ", 0.0))


@pytest.mark.asyncio
class TestTelemetryStillSeesTheFailure:
    async def test_the_span_is_still_marked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The conversion lives in `handle_request`, after `_mark_failed`,
        for exactly this reason: an earlier return would have traded a
        visible client error for an invisible server one."""
        marked: list[str] = []
        monkeypatch.setattr(
            UnifiedMcpServer,
            "_mark_failed",
            staticmethod(lambda _span, exc: marked.append(type(exc).__name__)),
        )
        await _mcp_call(_server(), "twin.query_cypher", {})
        assert marked == ["ToolHandlerError"]

    async def test_the_error_metric_still_fires(self) -> None:
        """FORGE-413 had just made these metrics real; silently stopping them
        here would have undone it within the hour."""
        recorded: list[tuple[str, str]] = []

        class _Metrics:
            def record_mcp_tool_call(self, *a: Any, **k: Any) -> None: ...
            def record_mcp_adapter_probe(self, *a: Any, **k: Any) -> None: ...
            def record_mcp_code_version(self, *a: Any, **k: Any) -> None: ...

            def record_mcp_error(self, tool_id: str, error_class: str, client: str) -> None:
                recorded.append((tool_id, error_class))

        server = UnifiedMcpServer(
            [_Adapter()],
            caller=Caller.LOCAL,
            exempt_local_writes=True,
            metrics=_Metrics(),
        )
        await _mcp_call(server, "twin.query_cypher", {})
        assert recorded == [("twin.query_cypher", "tool_execution_error")]


@pytest.mark.asyncio
class TestMetricsDoNotDependOnSessionCapture:
    """FORGE-421, found by the test above.

    `_tool_call` returned early when no session capture was configured, and
    `_record_call` lives below that return -- so every `metaforge_mcp_*`
    metric recorded only on a deployment that happened to run with
    `--capture-sessions`. Two unrelated concerns tangled by one early return.

    It quietly undid FORGE-413 the same day it shipped: the collector was
    wired, and the alerts still could not fire anywhere capture was off. The
    live check passed only because fidel-dev runs with it.
    """

    class _Metrics:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.errors: list[str] = []

        def record_mcp_tool_call(
            self, tool_id: str, status: str, _duration: float, _client: str
        ) -> None:
            self.calls.append((tool_id, status))

        def record_mcp_error(self, tool_id: str, _cls: str, _client: str) -> None:
            self.errors.append(tool_id)

        def record_mcp_adapter_probe(self, *a: Any, **k: Any) -> None: ...
        def record_mcp_code_version(self, *a: Any, **k: Any) -> None: ...

    async def _run(self, capture: Any, args: dict[str, Any]) -> _Metrics:
        metrics = self._Metrics()
        server = UnifiedMcpServer(
            [_Adapter()],
            caller=Caller.LOCAL,
            exempt_local_writes=True,
            metrics=metrics,
            session_capture=capture,
        )
        await _mcp_call(server, "twin.query_cypher", args)
        return metrics

    async def test_a_successful_call_records_with_capture_off(self) -> None:
        metrics = await self._run(None, {"ok": True})
        assert metrics.calls == [("twin.query_cypher", "ok")]

    async def test_a_failed_call_records_with_capture_off(self) -> None:
        metrics = await self._run(None, {})
        assert metrics.calls == [("twin.query_cypher", "error")]
        assert metrics.errors == ["twin.query_cypher"]

    async def test_capture_still_runs_when_it_is_configured(self) -> None:
        """The decoupling must not turn capture off instead."""
        seen: list[str] = []

        class _Capture:
            async def on_tool_call(self, tool_id: str, *a: Any, **k: Any) -> None:
                seen.append(tool_id)

        metrics = await self._run(_Capture(), {"ok": True})
        assert metrics.calls == [("twin.query_cypher", "ok")]
        assert seen == ["twin.query_cypher"]
