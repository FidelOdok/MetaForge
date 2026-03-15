"""Unit tests for the assistant API layer (MET-36, MET-38).

Covers:
- Pydantic schema validation and serialization
- ApprovalWorkflow lifecycle (propose, decide, list)
- FastAPI route integration via TestClient
- WebSocket event schemas
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.routes import router, workflow
from api_gateway.assistant.schemas import (
    ApprovalDecision,
    ApprovalDecisionType,
    AssistantRequest,
    AssistantResponse,
    ChangeStatus,
    DesignChangeProposal,
    EventType,
    ProposalListResponse,
    WebSocketEvent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_workflow() -> None:
    """Clear the module-level workflow between tests."""
    workflow.clear()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ===================================================================
# Schema tests — AssistantRequest
# ===================================================================


class TestAssistantRequestSchema:
    def test_valid_request(self) -> None:
        req = AssistantRequest(
            action="validate_stress",
            target_id=uuid4(),
            project_id="proj-001",
            parameters={"load": 500},
        )
        assert req.action == "validate_stress"
        assert isinstance(req.session_id, UUID)

    def test_default_session_id(self) -> None:
        req = AssistantRequest(action="run_drc", target_id=uuid4(), project_id="proj-001")
        assert isinstance(req.session_id, UUID)

    def test_default_parameters(self) -> None:
        req = AssistantRequest(action="run_drc", target_id=uuid4(), project_id="proj-001")
        assert req.parameters == {}

    def test_empty_action_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            AssistantRequest(action="", target_id=uuid4(), project_id="proj-001")

    def test_serialization_roundtrip(self) -> None:
        req = AssistantRequest(
            action="check_bom",
            target_id=uuid4(),
            project_id="proj-001",
            parameters={"strict": True},
        )
        data = req.model_dump()
        restored = AssistantRequest(**data)
        assert restored.action == req.action
        assert restored.target_id == req.target_id


# ===================================================================
# Schema tests — AssistantResponse
# ===================================================================


class TestAssistantResponseSchema:
    def test_minimal_response(self) -> None:
        resp = AssistantResponse(status="accepted")
        assert resp.status == "accepted"
        assert isinstance(resp.request_id, UUID)
        assert resp.result == {}
        assert resp.errors == []

    def test_response_with_errors(self) -> None:
        resp = AssistantResponse(
            status="failed",
            errors=["timeout", "agent unavailable"],
        )
        assert len(resp.errors) == 2


# ===================================================================
# Schema tests — DesignChangeProposal
# ===================================================================


class TestDesignChangeProposalSchema:
    def test_defaults(self) -> None:
        p = DesignChangeProposal(
            agent_code="mechanical",
            description="Update stress results",
        )
        assert p.status == ChangeStatus.PENDING
        assert p.requires_approval is True
        assert isinstance(p.change_id, UUID)
        assert isinstance(p.created_at, datetime)
        assert p.decided_at is None

    def test_with_artifacts(self) -> None:
        ids = [uuid4(), uuid4()]
        p = DesignChangeProposal(
            agent_code="electronics",
            description="Add decoupling cap",
            work_products_affected=ids,
        )
        assert len(p.work_products_affected) == 2

    def test_empty_agent_code_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            DesignChangeProposal(agent_code="", description="test")

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            DesignChangeProposal(agent_code="mech", description="")


# ===================================================================
# Schema tests — ApprovalDecision
# ===================================================================


class TestApprovalDecisionSchema:
    def test_approve(self) -> None:
        d = ApprovalDecision(
            change_id=uuid4(),
            decision=ApprovalDecisionType.APPROVE,
            reason="looks good",
            reviewer="alice",
        )
        assert d.decision == "approve"

    def test_reject(self) -> None:
        d = ApprovalDecision(
            change_id=uuid4(),
            decision=ApprovalDecisionType.REJECT,
            reason="needs work",
            reviewer="bob",
        )
        assert d.decision == "reject"

    def test_empty_reason_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            ApprovalDecision(
                change_id=uuid4(),
                decision=ApprovalDecisionType.APPROVE,
                reason="",
                reviewer="alice",
            )

    def test_empty_reviewer_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            ApprovalDecision(
                change_id=uuid4(),
                decision=ApprovalDecisionType.APPROVE,
                reason="fine",
                reviewer="",
            )


# ===================================================================
# Schema tests — WebSocketEvent
# ===================================================================


class TestWebSocketEventSchema:
    def test_event_creation(self) -> None:
        evt = WebSocketEvent(
            event_type=EventType.AGENT_STARTED,
            payload={"agent": "mechanical"},
            session_id=uuid4(),
        )
        assert evt.event_type == "agent_started"
        assert isinstance(evt.timestamp, datetime)

    def test_all_event_types(self) -> None:
        expected = {
            "agent_started",
            "agent_completed",
            "change_proposed",
            "change_approved",
            "change_rejected",
            "skill_started",
            "skill_completed",
            "twin_updated",
        }
        actual = {e.value for e in EventType}
        assert actual == expected

    def test_event_serialization(self) -> None:
        sid = uuid4()
        evt = WebSocketEvent(
            event_type=EventType.TWIN_UPDATED,
            payload={"node_id": "abc"},
            session_id=sid,
        )
        data = evt.model_dump()
        assert data["event_type"] == "twin_updated"
        assert data["session_id"] == sid


# ===================================================================
# Schema tests — Enums
# ===================================================================


class TestEnums:
    def test_change_status_values(self) -> None:
        assert ChangeStatus.PENDING == "pending"
        assert ChangeStatus.APPROVED == "approved"
        assert ChangeStatus.REJECTED == "rejected"
        assert ChangeStatus.APPLIED == "applied"
        assert ChangeStatus.EXPIRED == "expired"

    def test_approval_decision_type_values(self) -> None:
        assert ApprovalDecisionType.APPROVE == "approve"
        assert ApprovalDecisionType.REJECT == "reject"


# ===================================================================
# Schema tests — ProposalListResponse
# ===================================================================


class TestProposalListResponseSchema:
    def test_empty_list(self) -> None:
        r = ProposalListResponse(proposals=[], total=0)
        assert r.total == 0
        assert r.proposals == []

    def test_with_proposals(self) -> None:
        p = DesignChangeProposal(agent_code="mech", description="test")
        r = ProposalListResponse(proposals=[p], total=1)
        assert r.total == 1
        assert r.proposals[0].agent_code == "mech"


# ===================================================================
# ApprovalWorkflow unit tests
# ===================================================================


class TestApprovalWorkflow:
    @pytest.mark.asyncio
    async def test_propose_creates_proposal(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change(
            agent_code="mechanical",
            description="Stress update",
            diff={"field": "value"},
            work_products=[uuid4()],
        )
        assert proposal.status == ChangeStatus.PENDING
        assert proposal.agent_code == "mechanical"
        assert wf.proposal_count == 1

    @pytest.mark.asyncio
    async def test_get_pending_proposals(self) -> None:
        wf = ApprovalWorkflow()
        sid = uuid4()
        await wf.propose_change("mech", "a", {}, [], session_id=sid)
        await wf.propose_change("elec", "b", {}, [], session_id=sid)
        pending = wf.get_pending_proposals()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_get_pending_by_session(self) -> None:
        wf = ApprovalWorkflow()
        sid1 = uuid4()
        sid2 = uuid4()
        await wf.propose_change("mech", "a", {}, [], session_id=sid1)
        await wf.propose_change("elec", "b", {}, [], session_id=sid2)
        pending = wf.get_pending_proposals(session_id=sid1)
        assert len(pending) == 1
        assert pending[0].agent_code == "mech"

    @pytest.mark.asyncio
    async def test_approve_proposal(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change("mech", "test", {}, [])
        result = await wf.decide(proposal.change_id, ApprovalDecisionType.APPROVE, "ok", "alice")
        assert result is not None
        assert result.status == ChangeStatus.APPROVED
        assert result.reviewer == "alice"
        assert result.decision_reason == "ok"
        assert result.decided_at is not None

    @pytest.mark.asyncio
    async def test_reject_proposal(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change("mech", "test", {}, [])
        result = await wf.decide(proposal.change_id, ApprovalDecisionType.REJECT, "bad", "bob")
        assert result is not None
        assert result.status == ChangeStatus.REJECTED

    @pytest.mark.asyncio
    async def test_decide_unknown_returns_none(self) -> None:
        wf = ApprovalWorkflow()
        result = await wf.decide(uuid4(), ApprovalDecisionType.APPROVE, "ok", "alice")
        assert result is None

    @pytest.mark.asyncio
    async def test_decide_non_pending_is_noop(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change("mech", "test", {}, [])
        await wf.decide(proposal.change_id, ApprovalDecisionType.APPROVE, "ok", "a")
        # Try to reject an already-approved proposal
        result = await wf.decide(proposal.change_id, ApprovalDecisionType.REJECT, "too late", "b")
        assert result is not None
        assert result.status == ChangeStatus.APPROVED  # unchanged

    @pytest.mark.asyncio
    async def test_get_proposal(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change("mech", "test", {}, [])
        fetched = wf.get_proposal(proposal.change_id)
        assert fetched is not None
        assert fetched.change_id == proposal.change_id

    @pytest.mark.asyncio
    async def test_get_proposal_unknown(self) -> None:
        wf = ApprovalWorkflow()
        assert wf.get_proposal(uuid4()) is None

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        wf = ApprovalWorkflow()
        await wf.propose_change("mech", "test", {}, [])
        wf.clear()
        assert wf.proposal_count == 0

    @pytest.mark.asyncio
    async def test_approved_not_in_pending(self) -> None:
        wf = ApprovalWorkflow()
        proposal = await wf.propose_change("mech", "test", {}, [])
        await wf.decide(proposal.change_id, ApprovalDecisionType.APPROVE, "ok", "alice")
        pending = wf.get_pending_proposals()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_event_emitted_on_propose(self) -> None:
        wf = ApprovalWorkflow()
        sid = uuid4()
        queue = wf.subscribe(sid)
        await wf.propose_change("mech", "test", {}, [], session_id=sid)
        event = queue.get_nowait()
        assert event.event_type == EventType.CHANGE_PROPOSED

    @pytest.mark.asyncio
    async def test_event_emitted_on_approve(self) -> None:
        wf = ApprovalWorkflow()
        sid = uuid4()
        queue = wf.subscribe(sid)
        proposal = await wf.propose_change("mech", "test", {}, [], session_id=sid)
        # Drain the CHANGE_PROPOSED event
        queue.get_nowait()
        await wf.decide(proposal.change_id, ApprovalDecisionType.APPROVE, "ok", "alice")
        event = queue.get_nowait()
        assert event.event_type == EventType.CHANGE_APPROVED

    @pytest.mark.asyncio
    async def test_event_emitted_on_reject(self) -> None:
        wf = ApprovalWorkflow()
        sid = uuid4()
        queue = wf.subscribe(sid)
        proposal = await wf.propose_change("mech", "test", {}, [], session_id=sid)
        queue.get_nowait()
        await wf.decide(proposal.change_id, ApprovalDecisionType.REJECT, "no", "bob")
        event = queue.get_nowait()
        assert event.event_type == EventType.CHANGE_REJECTED

    @pytest.mark.asyncio
    async def test_unsubscribe(self) -> None:
        wf = ApprovalWorkflow()
        sid = uuid4()
        queue = wf.subscribe(sid)
        wf.unsubscribe(sid, queue)
        await wf.propose_change("mech", "test", {}, [], session_id=sid)
        assert queue.empty()


# ===================================================================
# Route tests — POST /v1/assistant/request
# ===================================================================


class TestSubmitRequest:
    def test_submit_returns_accepted(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": str(uuid4()),
                "project_id": "proj-001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "request_id" in data

    def test_submit_includes_action_in_result(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/assistant/request",
            json={
                "action": "run_drc",
                "target_id": str(uuid4()),
                "project_id": "proj-001",
                "parameters": {"strict": True},
            },
        )
        data = resp.json()
        assert data["result"]["action"] == "run_drc"

    def test_submit_empty_action_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/assistant/request",
            json={
                "action": "",
                "target_id": str(uuid4()),
                "project_id": "proj-001",
            },
        )
        assert resp.status_code == 422

    def test_submit_invalid_target_id(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/assistant/request",
            json={
                "action": "run_drc",
                "target_id": "not-a-uuid",
                "project_id": "proj-001",
            },
        )
        assert resp.status_code == 422


# ===================================================================
# Route tests — GET /v1/assistant/proposals
# ===================================================================


class TestListProposals:
    def test_empty_proposals(self, client: TestClient) -> None:
        resp = client.get("/v1/assistant/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposals"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_proposals_after_creation(self, client: TestClient) -> None:
        await workflow.propose_change("mech", "test proposal", {}, [])
        resp = client.get("/v1/assistant/proposals")
        data = resp.json()
        assert data["total"] == 1
        assert data["proposals"][0]["agent_code"] == "mech"

    @pytest.mark.asyncio
    async def test_filter_by_session_id(self, client: TestClient) -> None:
        sid = uuid4()
        await workflow.propose_change("mech", "a", {}, [], session_id=sid)
        await workflow.propose_change("elec", "b", {}, [], session_id=uuid4())
        resp = client.get(f"/v1/assistant/proposals?session_id={sid}")
        data = resp.json()
        assert data["total"] == 1


# ===================================================================
# Route tests — GET /v1/assistant/proposals/{change_id}
# ===================================================================


class TestGetProposal:
    @pytest.mark.asyncio
    async def test_get_existing_proposal(self, client: TestClient) -> None:
        proposal = await workflow.propose_change("mech", "test", {}, [])
        resp = client.get(f"/v1/assistant/proposals/{proposal.change_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_code"] == "mech"

    def test_get_nonexistent_proposal(self, client: TestClient) -> None:
        resp = client.get(f"/v1/assistant/proposals/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Proposal not found"


# ===================================================================
# Route tests — POST /v1/assistant/proposals/{change_id}/decide
# ===================================================================


class TestDecideProposal:
    @pytest.mark.asyncio
    async def test_approve_proposal(self, client: TestClient) -> None:
        proposal = await workflow.propose_change("mech", "test", {}, [])
        resp = client.post(
            f"/v1/assistant/proposals/{proposal.change_id}/decide",
            json={
                "change_id": str(proposal.change_id),
                "decision": "approve",
                "reason": "looks good",
                "reviewer": "alice",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["reviewer"] == "alice"

    @pytest.mark.asyncio
    async def test_reject_proposal(self, client: TestClient) -> None:
        proposal = await workflow.propose_change("mech", "test", {}, [])
        resp = client.post(
            f"/v1/assistant/proposals/{proposal.change_id}/decide",
            json={
                "change_id": str(proposal.change_id),
                "decision": "reject",
                "reason": "not ready",
                "reviewer": "bob",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"

    def test_decide_nonexistent(self, client: TestClient) -> None:
        cid = uuid4()
        resp = client.post(
            f"/v1/assistant/proposals/{cid}/decide",
            json={
                "change_id": str(cid),
                "decision": "approve",
                "reason": "ok",
                "reviewer": "alice",
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_decide_invalid_decision(self, client: TestClient) -> None:
        proposal = await workflow.propose_change("mech", "test", {}, [])
        resp = client.post(
            f"/v1/assistant/proposals/{proposal.change_id}/decide",
            json={
                "change_id": str(proposal.change_id),
                "decision": "maybe",
                "reason": "hmm",
                "reviewer": "alice",
            },
        )
        assert resp.status_code == 422


# ===================================================================
# WebSocket tests
# ===================================================================


class TestWebSocket:
    def test_websocket_connect(self, client: TestClient) -> None:
        sid = str(uuid4())
        with client.websocket_connect(f"/v1/assistant/ws/{sid}") as ws:
            # Just verifying connection succeeds
            ws.send_text('{"type": "ping"}')
            # Close immediately — no events to receive
