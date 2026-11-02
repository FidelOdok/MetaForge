"""Pending approvals are mentioned on a client's tool results (FORGE-724).

Gates and held writes from a server-run flow only reached the dashboard, so a
person working in Claude Code never heard of them. The sidecar now adds one
sentence to the next tool result after an approval appears, once per item per
session, so the agent tells the person and offers /metaforge:approvals.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from mcp_core.context import McpCallContext, with_context
from mcp_core.plugin_hooks.metaforge_hook import SESSION_RULES
from metaforge.mcp.server import UnifiedMcpServer


class _Lister:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.calls = 0
        self.fail = False

    async def __call__(self, project_id: str | None) -> list[dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("gateway down")
        return list(self.items)


GATE = {"id": "gate:run_1", "status": "pending", "title": "Design gate"}
HELD = {"id": "tool:run_2", "status": "pending", "title": "Tool call: twin.record_decision"}


def _server(lister: _Lister) -> UnifiedMcpServer:
    server = UnifiedMcpServer([])
    server.attach_approval_notices(lister)
    server._pending_cache.clear()  # noqa: SLF001
    return server


def _notice(server: UnifiedMcpServer, tool: str = "twin.get_node", **ctx: Any) -> Any:
    async def go() -> Any:
        context = McpCallContext(session_id=ctx.pop("session", uuid.UUID(int=1)), **ctx)
        with with_context(context):
            return await server._approval_notice(tool)  # noqa: SLF001

    return asyncio.run(go())


def test_new_approvals_are_mentioned_once_per_session() -> None:
    server = _server(_Lister([GATE]))
    first = _notice(server)
    assert first is not None
    sentence, count = first
    assert count == 1 and "Design gate" in sentence and "/metaforge:approvals" in sentence
    assert "cannot decide them yourself" in sentence
    assert _notice(server) is None  # already told


def test_a_new_item_is_mentioned_again() -> None:
    lister = _Lister([GATE])
    server = _server(lister)
    _notice(server)
    lister.items = [GATE, HELD]
    server._pending_cache.clear()  # noqa: SLF001
    sentence, count = _notice(server)
    assert count == 2 and "1 new: Tool call: twin.record_decision" in sentence


def test_each_session_is_told_separately() -> None:
    server = _server(_Lister([GATE]))
    assert _notice(server, session=uuid.UUID(int=1)) is not None
    assert _notice(server, session=uuid.UUID(int=2)) is not None


def test_no_pending_no_notice_and_a_failing_gateway_is_silent() -> None:
    assert _notice(_server(_Lister([]))) is None
    lister = _Lister([GATE])
    lister.fail = True
    assert _notice(_server(lister)) is None


def test_the_approval_tools_themselves_carry_no_notice() -> None:
    server = _server(_Lister([GATE]))
    assert _notice(server, tool="approvals.list") is None
    assert _notice(server, tool="flow.await_gate") is None


def test_the_answer_is_cached_between_calls() -> None:
    lister = _Lister([GATE])
    server = _server(lister)
    _notice(server, session=uuid.UUID(int=1))
    _notice(server, session=uuid.UUID(int=2))
    assert lister.calls == 1


def test_off_unless_attached() -> None:
    server = UnifiedMcpServer([])

    async def go() -> Any:
        return await server._approval_notice("twin.get_node")  # noqa: SLF001

    assert asyncio.run(go()) is None


def test_the_plugin_session_rules_say_to_relay_it() -> None:
    assert "approvals are waiting" in SESSION_RULES
    assert "/metaforge:approvals" in SESSION_RULES
