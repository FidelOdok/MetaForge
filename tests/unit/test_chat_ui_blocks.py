"""Generative UI: typed blocks on an agent reply.

The agent attaches blocks with ``chat.render_ui``; a filed proposal gets its
card automatically. Blocks are validated at the tool call, stored on the
reply, and returned on the message, so the dashboard renders components
rather than parsing prose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.chat import routes as chat_routes
from api_gateway.chat.backend import InMemoryChatBackend
from api_gateway.chat.harness_backend import make_render_ui_tool, proposal_blocks
from api_gateway.chat.models import ChatThreadRecord
from api_gateway.chat.schemas import MessageResponse
from api_gateway.chat.ui_blocks import (
    MAX_BLOCKS,
    InvalidBlocksError,
    stored_blocks,
    validate_blocks,
)
from orchestrator.harness.react import ReActStep, ToolCall

EVERY_KIND: list[dict[str, Any]] = [
    {"type": "choice", "prompt": "Which rail?", "options": [{"label": "5V"}, {"label": "3V3"}]},
    {
        "type": "comparison",
        "criteria": ["Cost"],
        "options": [
            {"name": "A", "pros": ["cheap"], "values": {"Cost": "£2"}, "recommended": True},
            {"name": "B", "cons": ["slow"]},
        ],
        "basis": "estimate",
    },
    {"type": "measurement_request", "label": "Desk thickness", "unit": "mm", "critical": True},
    {"type": "table", "columns": ["Part", "Qty"], "rows": [["M3 screw", 4], ["Nut", None]]},
    {"type": "metrics", "items": [{"label": "5V draw", "value": 2.61, "unit": "A", "limit": 2.4}]},
    {"type": "callout", "tone": "risk", "title": "Over budget"},
    {"type": "proposal", "change_id": "c-1"},
    {"type": "part", "node_id": "n-1", "label": "enclosure.step"},
]


class TestValidation:
    def test_every_kind_validates_and_round_trips(self) -> None:
        data = validate_blocks(EVERY_KIND)
        assert [b["type"] for b in data] == [b["type"] for b in EVERY_KIND]
        # Plain JSON data, with defaults filled and absent optionals left out.
        assert data[0]["multiple"] is False
        assert "value" not in data[0]["options"][0]

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(InvalidBlocksError):
            validate_blocks([{"type": "html", "html": "<script>alert(1)</script>"}])

    def test_extra_fields_are_refused_so_markup_cannot_ride_along(self) -> None:
        with pytest.raises(InvalidBlocksError, match="html"):
            validate_blocks([{"type": "callout", "title": "x", "html": "<b>x</b>"}])

    def test_a_table_row_must_match_its_columns(self) -> None:
        with pytest.raises(InvalidBlocksError, match="row 0"):
            validate_blocks([{"type": "table", "columns": ["a", "b"], "rows": [["only one"]]}])

    def test_the_block_count_is_bounded(self) -> None:
        block = {"type": "callout", "title": "x"}
        with pytest.raises(InvalidBlocksError, match="at most"):
            validate_blocks([block] * (MAX_BLOCKS + 1))

    def test_a_basis_outside_the_vocabulary_is_refused(self) -> None:
        with pytest.raises(InvalidBlocksError):
            validate_blocks(
                [{"type": "metrics", "items": [{"label": "x", "value": 1}], "basis": "guess"}]
            )

    def test_a_stale_stored_block_is_dropped_not_fatal(self) -> None:
        kept = stored_blocks([{"type": "callout", "title": "ok"}, {"type": "gone"}])
        assert kept == [{"type": "callout", "tone": "info", "title": "ok"}]
        assert stored_blocks(None) == []


class TestRenderUiTool:
    async def test_valid_blocks_land_in_the_reply(self) -> None:
        sink: list[dict[str, Any]] = []
        tool = make_render_ui_tool(sink)
        result = await tool.handler({"blocks": EVERY_KIND[:2]})
        assert result["rendered"] == 2
        assert [b["type"] for b in sink] == ["choice", "comparison"]

    async def test_an_invalid_block_is_a_tool_error_the_agent_can_fix(self) -> None:
        sink: list[dict[str, Any]] = []
        tool = make_render_ui_tool(sink)
        with pytest.raises(ValueError, match="chat.render_ui"):
            await tool.handler({"blocks": [{"type": "choice", "prompt": "?", "options": []}]})
        assert sink == []

    async def test_the_total_per_reply_is_capped_across_calls(self) -> None:
        sink: list[dict[str, Any]] = []
        tool = make_render_ui_tool(sink)
        await tool.handler({"blocks": [{"type": "callout", "title": "x"}] * MAX_BLOCKS})
        with pytest.raises(ValueError, match="already has"):
            await tool.handler({"blocks": [{"type": "callout", "title": "y"}]})
        assert len(sink) == MAX_BLOCKS


def _propose(observation: Any, error: str | None = None) -> ReActStep:
    return ReActStep(
        thought="",
        tool_call=ToolCall(
            name="twin.propose_change", arguments={"description": "Move GPS to 3V3"}
        ),
        observation=observation,
        error=error,
    )


class TestProposalCards:
    def test_a_filed_change_gets_its_card(self) -> None:
        filed = _propose({"data": {"change_id": "c-9", "status": "pending"}})
        blocks = proposal_blocks([filed], [])
        assert blocks == [{"type": "proposal", "change_id": "c-9", "summary": "Move GPS to 3V3"}]

    def test_one_the_agent_already_showed_is_not_repeated(self) -> None:
        shown = [{"type": "proposal", "change_id": "c-9"}]
        assert proposal_blocks([_propose({"change_id": "c-9"})], shown) == []

    def test_a_failed_proposal_gets_no_card(self) -> None:
        assert proposal_blocks([_propose(None, error="refused")], []) == []
        assert proposal_blocks([_propose({"change_id": "c-1", "success": False})], []) == []


class TestStorageAndResponse:
    async def test_blocks_are_stored_and_returned_on_the_message(self) -> None:
        backend = InMemoryChatBackend()
        blocks = validate_blocks(EVERY_KIND)
        msg = await backend.add_message(
            thread_id="t", actor_id="harness-agent", actor_kind="agent", content="x", blocks=blocks
        )
        response = chat_routes._make_message_response(msg)
        assert isinstance(response, MessageResponse)
        assert response.model_dump(mode="json", exclude_none=True)["blocks"] == blocks

    async def test_a_message_without_blocks_returns_an_empty_list(self) -> None:
        backend = InMemoryChatBackend()
        msg = await backend.add_message(
            thread_id="t", actor_id="u", actor_kind="user", content="hi"
        )
        assert chat_routes._make_message_response(msg).blocks == []

    async def test_the_turn_s_blocks_reach_the_agent_reply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(chat_routes, "chat_harness_enabled", lambda: True)

        async def _turn(*args: object, ui_blocks: list[dict[str, Any]], **kwargs: object) -> str:
            ui_blocks.extend(validate_blocks([{"type": "callout", "title": "Over budget"}]))
            return "See below."

        monkeypatch.setattr(chat_routes, "run_chat_turn_streaming", _turn)
        now = datetime.now(UTC)
        thread = ChatThreadRecord(
            id=str(uuid4()),
            title="t",
            scope_kind="assistant",
            scope_entity_id="s",
            channel_id="c",
            created_at=now,
            updated_at=now,
        )
        reply = await chat_routes._invoke_agent(thread, "why?")
        assert reply is not None
        assert reply.content == "See below."
        assert reply.blocks == [{"type": "callout", "tone": "info", "title": "Over budget"}]
