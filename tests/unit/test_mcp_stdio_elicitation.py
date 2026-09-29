"""stdio has to carry a question and its answer at once (FORGE-360).

Before elicitation the stream was one-directional: every inbound line was a
request, and the loop awaited each one before reading the next. Both of
those stop being true when the server can ask the client something.

* An inbound line can now be *our* answer. Dispatched as a request it came
  back as "Unknown method: None" -- a misroute that reads like a client bug.
* A held call waits on a response that arrives on the same stream. Awaiting
  the handler before reading the next line means never reading the line that
  would release it. That is a deadlock, not a slow approval, and it is the
  reason dispatch is a task.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest

from mcp_core.elicitation import ElicitAction
from mcp_core.guardrails import Caller
from metaforge.mcp import __main__ as entry
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Adapter(McpToolServer):
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
        return {}


# ---------------------------------------------------------------------------
# Telling a response from a request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"jsonrpc":"2.0","id":"elicit-1","result":{"action":"accept"}}', True),
        ('{"jsonrpc":"2.0","id":"elicit-1","error":{"code":-1,"message":"no"}}', True),
        ('{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}', False),
        # A request carrying a result-shaped argument is still a request.
        ('{"jsonrpc":"2.0","id":"1","method":"x","result":{}}', False),
        ('{"jsonrpc":"2.0","result":{}}', False),  # no id to correlate
        ("not json at all", False),
        ("[1,2,3]", False),
    ],
)
def test_response_detection(raw: str, expected: bool) -> None:
    assert entry._is_response(raw)[0] is expected


def test_unparseable_input_is_left_for_handle_request() -> None:
    """It already turns that into a proper JSON-RPC parse error. Classifying
    it here would swallow the diagnosis."""
    assert entry._is_response("{oops")[0] is False


# ---------------------------------------------------------------------------
# The elicitor itself
# ---------------------------------------------------------------------------


def test_elicitor_writes_a_request_and_takes_the_matching_answer() -> None:
    written: list[str] = []
    elicitor = entry.StdioElicitor(written.append)

    async def scenario() -> Any:
        task = asyncio.ensure_future(elicitor("Run it?", {"type": "object"}))
        await asyncio.sleep(0)
        sent = json.loads(written[0])
        assert sent["method"] == "elicitation/create"
        assert sent["params"]["message"] == "Run it?"
        elicitor.resolve(
            sent["id"], {"jsonrpc": "2.0", "id": sent["id"], "result": {"action": "accept"}}
        )
        return await task

    result = asyncio.run(scenario())
    assert result.action is ElicitAction.ACCEPT


def test_an_unmatched_answer_is_reported_not_swallowed() -> None:
    """``resolve`` returning False is what lets the read loop fall back to
    treating the line as a request rather than dropping it."""
    elicitor = entry.StdioElicitor(lambda _: None)
    assert elicitor.resolve("elicit-99", {"result": {}}) is False


def test_no_answer_is_a_cancel_not_a_decline() -> None:
    """A client that never answers has not refused. ``cancel`` maps to
    TIMED_OUT downstream, so the agent is told nobody looked."""
    elicitor = entry.StdioElicitor(lambda _: None, timeout_seconds=0.05)
    result = asyncio.run(elicitor("Run it?", {"type": "object"}))
    assert result.action is ElicitAction.CANCEL


def test_a_client_error_is_a_cancel_not_an_approval() -> None:
    written: list[str] = []
    elicitor = entry.StdioElicitor(written.append)

    async def scenario() -> Any:
        task = asyncio.ensure_future(elicitor("Run it?", {"type": "object"}))
        await asyncio.sleep(0)
        sent = json.loads(written[0])
        elicitor.resolve(
            sent["id"],
            {"jsonrpc": "2.0", "id": sent["id"], "error": {"code": -32601, "message": "no"}},
        )
        return await task

    assert asyncio.run(scenario()).action is ElicitAction.CANCEL


# ---------------------------------------------------------------------------
# The loop, end to end
# ---------------------------------------------------------------------------


def _run_stdio_over(lines: list[str], server: UnifiedMcpServer, monkeypatch, capsys) -> list[str]:
    """Feed ``lines`` to run_stdio through a real pipe; return stdout lines."""
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "wb", buffering=0) as writer:
        for line in lines:
            writer.write((line + "\n").encode())
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        asyncio.run(entry.run_stdio(server))
    finally:
        stdin.close()
    # structlog's console renderer also writes to stdout under pytest, so
    # take only the lines that are JSON-RPC.
    out: list[str] = []
    for line in capsys.readouterr().out.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
            out.append(line)
    return out


def test_ordinary_requests_still_answered(monkeypatch, capsys) -> None:
    monkeypatch.delenv("METAFORGE_MCP_API_KEY", raising=False)
    server = UnifiedMcpServer([_Adapter()])
    out = _run_stdio_over(
        [json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}})],
        server,
        monkeypatch,
        capsys,
    )
    replies = [json.loads(ln) for ln in out]
    assert any(r.get("id") == "1" and "result" in r for r in replies)


def test_a_held_call_and_its_answer_share_the_stream(monkeypatch, capsys) -> None:
    """The deadlock test.

    A real write is held: the server asks the client, and the client's
    answer has to arrive on the same stdin the loop is reading. With the
    handler awaited inline the loop is parked inside ``handle_request`` and
    never reads the line that would release it, so the call never returns
    and no reply is ever written.
    """
    monkeypatch.delenv("METAFORGE_MCP_API_KEY", raising=False)
    server = UnifiedMcpServer([_Adapter()], caller=Caller.REMOTE)

    read_fd, write_fd = os.pipe()
    writer = os.fdopen(write_fd, "wb", buffering=0)
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)

    def send(payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload) + "\n").encode())

    def stdout_messages() -> list[dict[str, Any]]:
        found = []
        for line in capsys.readouterr().out.splitlines():
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
                found.append(parsed)
        return found

    async def scenario() -> list[dict[str, Any]]:
        loop_task = asyncio.ensure_future(entry.run_stdio(server))
        send(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                    "clientInfo": {"name": "claude-code", "version": "2.1.4"},
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": "call",
                "method": "tools/call",
                "params": {"name": "twin.commit_geometry", "arguments": {"obj_id": "bracket"}},
            }
        )

        # Wait for the server to put the question. Bounded, because the bug
        # this guards against is precisely "it never does".
        ask = None
        seen: list[dict[str, Any]] = []
        for _ in range(100):
            await asyncio.sleep(0.05)
            seen += stdout_messages()
            ask = next((m for m in seen if m.get("method") == "elicitation/create"), None)
            if ask is not None:
                break
        assert ask is not None, "server never asked the client (the read loop is blocked)"
        assert "twin.commit_geometry" in ask["params"]["message"]

        send(
            {
                "jsonrpc": "2.0",
                "id": ask["id"],
                "result": {"action": "accept", "content": {"approve": True}},
            }
        )
        for _ in range(100):
            await asyncio.sleep(0.05)
            seen += stdout_messages()
            if any(m.get("id") == "call" for m in seen):
                break
        writer.close()
        await asyncio.wait_for(loop_task, timeout=5)
        return seen

    try:
        messages = asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    finally:
        stdin.close()

    reply = next(m for m in messages if m.get("id") == "call")
    assert "result" in reply, reply
