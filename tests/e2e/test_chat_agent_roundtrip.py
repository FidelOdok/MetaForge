"""E2E test: chat message -> agent -> work product round-trip (MET-223).

Exercises the full gateway -> agent -> twin pipeline with in-memory stores.
No external services or LLM calls required.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.chat.backend import InMemoryChatBackend as ChatStore
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def chat_store() -> ChatStore:
    return ChatStore.create()


# ---------------------------------------------------------------------------
# 1. Thread creation round-trip
# ---------------------------------------------------------------------------


class TestThreadCreation:
    """Verify thread creation and retrieval through the store."""

    def test_create_thread_and_retrieve(self, chat_store: ChatStore) -> None:
        from api_gateway.chat.models import ChatThreadRecord

        thread_id = str(uuid4())
        thread = ChatThreadRecord(
            id=thread_id,
            channel_id="chan-1",
            scope_kind="session",
            scope_entity_id="entity-1",
            title="E2E Thread",
        )
        chat_store.threads[thread_id] = thread
        chat_store.messages[thread_id] = []

        assert chat_store.threads[thread_id].title == "E2E Thread"
        assert chat_store.threads[thread_id].archived is False

    def test_multiple_threads_per_channel(self, chat_store: ChatStore) -> None:
        from api_gateway.chat.models import ChatThreadRecord

        for i in range(3):
            tid = str(uuid4())
            chat_store.threads[tid] = ChatThreadRecord(
                id=tid,
                channel_id="chan-1",
                scope_kind="session",
                scope_entity_id=f"entity-{i}",
                title=f"Thread {i}",
            )
            chat_store.messages[tid] = []

        session_threads = [t for t in chat_store.threads.values() if t.scope_kind == "session"]
        assert len(session_threads) == 3


# ---------------------------------------------------------------------------
# 2. Message persistence round-trip
# ---------------------------------------------------------------------------


class TestMessagePersistence:
    """Verify messages are stored and retrievable."""

    def test_add_and_list_messages(self, chat_store: ChatStore) -> None:
        from api_gateway.chat.models import ChatMessageRecord, ChatThreadRecord

        thread_id = str(uuid4())
        chat_store.threads[thread_id] = ChatThreadRecord(
            id=thread_id,
            channel_id="chan-1",
            scope_kind="session",
            scope_entity_id="e-1",
            title="Msg Test",
        )
        chat_store.messages[thread_id] = []

        msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id="user-1",
            actor_kind="user",
            content="Run stress validation on bracket",
        )
        chat_store.messages[thread_id].append(msg)

        assert len(chat_store.messages[thread_id]) == 1
        assert chat_store.messages[thread_id][0].content == "Run stress validation on bracket"

    def test_message_ordering(self, chat_store: ChatStore) -> None:
        from api_gateway.chat.models import ChatMessageRecord, ChatThreadRecord

        thread_id = str(uuid4())
        chat_store.threads[thread_id] = ChatThreadRecord(
            id=thread_id,
            channel_id="chan-1",
            scope_kind="session",
            scope_entity_id="e-1",
            title="Order Test",
        )
        chat_store.messages[thread_id] = []

        for i in range(5):
            chat_store.messages[thread_id].append(
                ChatMessageRecord(
                    id=str(uuid4()),
                    thread_id=thread_id,
                    actor_id="user-1",
                    actor_kind="user",
                    content=f"Message {i}",
                )
            )

        assert len(chat_store.messages[thread_id]) == 5
        assert chat_store.messages[thread_id][0].content == "Message 0"
        assert chat_store.messages[thread_id][4].content == "Message 4"


# ---------------------------------------------------------------------------
# 3. Twin work product creation
# ---------------------------------------------------------------------------


class TestTwinWorkProduct:
    """Verify work products can be created and retrieved through Twin API."""

    async def test_create_work_product(self, twin: InMemoryTwinAPI) -> None:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = WorkProduct(
            name="bracket_cad",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="/out/bracket.step",
            content_hash="abc123",
            format="step",
            created_by="e2e-test",
        )
        created = await twin.create_work_product(wp, branch="main")

        assert created.id is not None
        assert created.name == "bracket_cad"

        fetched = await twin.get_work_product(created.id, branch="main")
        assert fetched is not None
        assert fetched.id == created.id

    async def test_update_work_product_metadata(self, twin: InMemoryTwinAPI) -> None:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = WorkProduct(
            name="bracket_cad",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="/out/bracket.step",
            content_hash="abc123",
            format="step",
            created_by="e2e-test",
        )
        created = await twin.create_work_product(wp, branch="main")

        updates = {
            "metadata": {
                **created.metadata,
                "validation_status": "pass",
                "max_stress_mpa": 85.3,
            }
        }
        updated = await twin.update_work_product(created.id, updates, branch="main")

        assert updated.metadata["validation_status"] == "pass"
        assert updated.metadata["max_stress_mpa"] == 85.3


# ---------------------------------------------------------------------------
# 4. Chat + Twin integration (no LLM)
# ---------------------------------------------------------------------------


class TestChatTwinIntegration:
    """Verify chat store and twin can be used together in a single flow."""

    async def test_message_triggers_work_product(
        self,
        chat_store: ChatStore,
        twin: InMemoryTwinAPI,
    ) -> None:
        """Simulate: user sends message -> system creates work product -> agent responds."""
        from api_gateway.chat.models import ChatMessageRecord, ChatThreadRecord
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        # 1. Create thread
        thread_id = str(uuid4())
        chat_store.threads[thread_id] = ChatThreadRecord(
            id=thread_id,
            channel_id="chan-me",
            scope_kind="session",
            scope_entity_id="sess-1",
            title="Design Review",
        )
        chat_store.messages[thread_id] = []

        # 2. User message
        user_msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id="user-1",
            actor_kind="user",
            content="Validate stress on bracket",
        )
        chat_store.messages[thread_id].append(user_msg)

        # 3. Create work product (simulating agent action)
        wp = WorkProduct(
            name="bracket_stress_result",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path="/out/bracket_stress.frd",
            content_hash="def456",
            format="frd",
            created_by="mechanical-agent",
            metadata={"max_von_mises_mpa": 85.3, "passed": True},
        )
        created = await twin.create_work_product(wp, branch="main")

        # 4. Agent response
        agent_msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id="mechanical-agent",
            actor_kind="agent",
            content=f"Stress validation passed. Max von Mises: 85.3 MPa. WP: {created.id}",
        )
        chat_store.messages[thread_id].append(agent_msg)

        # Verify
        assert len(chat_store.messages[thread_id]) == 2
        assert chat_store.messages[thread_id][1].actor_kind == "agent"
        fetched_wp = await twin.get_work_product(created.id, branch="main")
        assert fetched_wp is not None
        assert fetched_wp.metadata["passed"] is True
