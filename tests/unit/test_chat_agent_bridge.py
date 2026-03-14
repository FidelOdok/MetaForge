"""Unit tests for the chat message -> agent invocation bridge (MET-218).

Verifies that user messages posted to a chat thread are routed to the
appropriate domain agent and that the agent's response is persisted in
the thread.

Tests cover three modes:
- LLM configured: agent processes the message and responds
- LLM not configured: only the user message is stored
- Agent error: a system error message is inserted
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat.routes import ChatStore, router
from api_gateway.chat.routes import store as _module_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Reset the module-level store before every test."""
    fresh = ChatStore.create()
    _module_store.channels.clear()
    _module_store.channels.update(fresh.channels)
    _module_store.threads.clear()
    _module_store.messages.clear()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_thread(
    client: TestClient,
    *,
    scope_kind: str = "session",
    scope_entity_id: str = "entity-1",
) -> dict:
    resp = client.post(
        "/v1/chat/threads",
        json={
            "scope_kind": scope_kind,
            "scope_entity_id": scope_entity_id,
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _send_user_message(
    client: TestClient,
    thread_id: str,
    content: str = "Validate the bracket stress",
) -> dict:
    resp = client.post(
        f"/v1/chat/threads/{thread_id}/messages",
        json={
            "content": content,
            "actor_id": "user-1",
            "actor_kind": "user",
        },
    )
    assert resp.status_code == 201
    return resp.json()


# ===================================================================
# With LLM configured -> agent responds
# ===================================================================


class TestAgentBridgeWithLLM:
    """When an LLM is configured the agent should process the message."""

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch("api_gateway.chat.routes.run_agent", new_callable=AsyncMock)
    def test_agent_response_created(
        self,
        mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        mock_run_agent.return_value = {
            "overall_passed": True,
            "max_stress_mpa": 120.5,
            "critical_region": "bracket_arm",
            "work_products": [],
            "analysis": {"summary": "All stress checks passed."},
            "recommendations": [],
            "tool_calls": [],
        }

        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        # Fetch the thread to see all messages
        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        messages = resp.json()["messages"]

        assert len(messages) == 2
        agent_msg = messages[1]
        assert agent_msg["actor_kind"] == "agent"
        assert agent_msg["actor_id"] == "mechanical-agent"
        assert "stress checks passed" in agent_msg["content"]

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch("api_gateway.chat.routes.run_agent", new_callable=AsyncMock)
    def test_agent_response_references_correct_thread(
        self,
        mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        mock_run_agent.return_value = {
            "overall_passed": True,
            "analysis": {"summary": "OK"},
            "recommendations": [],
            "tool_calls": [],
        }

        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        messages = resp.json()["messages"]
        assert len(messages) == 2
        assert messages[1]["thread_id"] == thread["id"]

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch("api_gateway.chat.routes.run_agent", new_callable=AsyncMock)
    def test_agent_response_has_correct_actor_kind(
        self,
        mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        mock_run_agent.return_value = {
            "overall_passed": True,
            "analysis": {},
            "recommendations": [],
            "tool_calls": [],
        }

        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        agent_msg = resp.json()["messages"][1]
        assert agent_msg["actor_kind"] == "agent"

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch("api_gateway.chat.routes.run_agent", new_callable=AsyncMock)
    def test_fallback_content_when_no_analysis_summary(
        self,
        mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        """When the analysis dict has no summary, a fallback is used."""
        mock_run_agent.return_value = {
            "overall_passed": False,
            "analysis": {},
            "recommendations": [],
            "tool_calls": [],
        }

        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        agent_msg = resp.json()["messages"][1]
        assert "Passed: False" in agent_msg["content"]


# ===================================================================
# Without LLM configured -> no agent response
# ===================================================================


class TestAgentBridgeWithoutLLM:
    """When no LLM is configured only the user message should be stored."""

    @patch("api_gateway.chat.routes.is_llm_available", return_value=False)
    def test_no_agent_response(
        self,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        messages = resp.json()["messages"]
        assert len(messages) == 1
        assert messages[0]["actor_kind"] == "user"


# ===================================================================
# Agent error -> system error message
# ===================================================================


class TestAgentBridgeError:
    """When the agent raises an exception a system error message is inserted."""

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch(
        "api_gateway.chat.routes.run_agent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM provider unreachable"),
    )
    def test_error_message_inserted(
        self,
        _mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        thread = _create_thread(client)
        _send_user_message(client, thread["id"])

        resp = client.get(f"/v1/chat/threads/{thread['id']}")
        messages = resp.json()["messages"]

        assert len(messages) == 2
        error_msg = messages[1]
        assert error_msg["actor_kind"] == "system"
        assert error_msg["status"] == "error"
        assert "LLM provider unreachable" in error_msg["content"]


# ===================================================================
# Non-user messages should NOT trigger agent invocation
# ===================================================================


class TestAgentBridgeNonUserMessages:
    """Messages from agents or system should not trigger agent invocation."""

    @patch("api_gateway.chat.routes.is_llm_available", return_value=True)
    @patch("api_gateway.chat.routes.run_agent", new_callable=AsyncMock)
    def test_agent_message_does_not_trigger_agent(
        self,
        mock_run_agent: AsyncMock,
        _mock_llm: object,
        client: TestClient,
    ) -> None:
        thread = _create_thread(client)
        resp = client.post(
            f"/v1/chat/threads/{thread['id']}/messages",
            json={
                "content": "Some agent output",
                "actor_id": "mechanical-agent",
                "actor_kind": "agent",
            },
        )
        assert resp.status_code == 201
        mock_run_agent.assert_not_called()
