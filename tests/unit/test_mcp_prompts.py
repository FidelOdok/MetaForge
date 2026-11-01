"""The curated workflows, exposed once and served twice (FORGE-340/341).

Six things an engineer asks a harness to do. They reach a Claude Code user as
slash commands and every other client as MCP prompts — from one definition,
because two would mean the Claude Code user and the ChatGPT user getting
different instructions for the same task, and the one that drifts being
whichever nobody is currently testing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_core.workflows import WORKFLOWS, prompt_body
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.server import McpToolServer

REPO = Path(__file__).resolve().parents[2]


class _Bare(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")


async def _rpc(server: UnifiedMcpServer, method: str, params: dict | None = None) -> dict:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    )
    return json.loads(raw)


@pytest.mark.asyncio
class TestServed:
    async def test_the_capability_is_advertised(self) -> None:
        # Without it a client never calls prompts/list, so the workflows may
        # as well not exist.
        result = (await _rpc(UnifiedMcpServer(adapters=[_Bare()]), "initialize"))["result"]
        assert "prompts" in result["capabilities"]

    async def test_every_workflow_is_listed(self) -> None:
        result = (await _rpc(UnifiedMcpServer(adapters=[_Bare()]), "prompts/list"))["result"]
        assert {p["name"] for p in result["prompts"]} == set(WORKFLOWS)
        assert all(p["description"] for p in result["prompts"])

    async def test_get_returns_the_instructions(self) -> None:
        result = (await _rpc(UnifiedMcpServer(adapters=[_Bare()]), "prompts/get", {"name": "fea"}))[
            "result"
        ]
        assert result["messages"][0]["content"]["text"] == prompt_body("fea")

    async def test_an_unknown_name_lists_the_real_ones(self) -> None:
        response = await _rpc(UnifiedMcpServer(adapters=[_Bare()]), "prompts/get", {"name": "feaa"})
        message = response["error"]["message"]
        assert "fea" in message and "Available:" in message


class TestOneDefinitionTwoSurfaces:
    """The invariant. Both surfaces come from WORKFLOWS or neither is trusted."""

    def test_the_generated_commands_match_the_prompts(self) -> None:
        commands = REPO / "integrations" / "claude-code" / "commands"
        assert commands.is_dir(), "run scripts/build_integrations.py"
        on_disk = {p.stem for p in commands.glob("*.md")}
        assert on_disk == set(WORKFLOWS)

    def test_each_command_carries_its_workflow_body(self) -> None:
        commands = REPO / "integrations" / "claude-code" / "commands"
        for name, (description, body) in WORKFLOWS.items():
            text = (commands / f"{name}.md").read_text(encoding="utf-8")
            assert description in text, name
            assert body.strip() in text, name


class TestTheWordingStaysHonest:
    """These sentences are the reason the workflows exist as text at all.

    An agent told only the happy path reports the happy path, so each of
    these is asserted rather than left to a future edit.
    """

    def test_status_calls_missing_evidence_a_gap(self) -> None:
        assert "gap, not a pass" in prompt_body("status")

    def test_design_says_a_held_write_is_expected(self) -> None:
        body = prompt_body("design")
        assert "held for approval" in body
        assert "not an error" in body

    def test_doctor_says_to_check_for_missing_adapters(self) -> None:
        """A shorter tool list looks like a smaller system rather than a broken
        one, which is the whole failure mode.

        This used to assert the prompt mentioned `_meta`. FORGE-409 pointed
        doctor at the `health.check` tool instead of protocol methods a
        harness cannot call, and that tool's `adapters` array is now where the
        answer lives. The property is unchanged; the mechanism moved.
        """
        body = prompt_body("doctor")
        assert "adapters" in body
        assert "contributes no tools" in body

    def test_gate_does_not_offer_to_supply_the_human(self) -> None:
        """The prompt used to say "do not supply one on the user's behalf",
        which left the model holding an argument it was asked politely not to
        use. FORGE-393 removed the argument, so the prompt has to say the
        stronger thing: passing it is an error, and the authority comes from
        whoever approves."""
        body = prompt_body("gate")
        assert "Do not pass" in body and "`decided_by`" in body
        assert "supplying it is an error" in body
        assert "whoever approves it is recorded" in body
