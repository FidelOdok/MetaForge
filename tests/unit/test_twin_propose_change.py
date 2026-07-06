"""twin.propose_change tool + gateway proposal recorder (MET-548). Network-free."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.proposal_recorder import make_proposal_recorder
from tool_registry.tools.twin.adapter import TwinServer


class _FakeTwin:
    async def list_work_products(self) -> list:
        return []


@pytest.mark.asyncio
async def test_propose_change_tool_registered_only_with_recorder() -> None:
    seen: dict = {}

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"change_id": "cid-1", "status": "pending"}

    # Without a recorder → tool absent.
    plain = TwinServer(_FakeTwin())
    assert "twin.propose_change" not in plain.tool_ids

    # With a recorder → tool present + invokes the recorder.
    server = TwinServer(_FakeTwin(), proposal_recorder=recorder)
    assert "twin.propose_change" in server.tool_ids

    out = await server.propose_change(
        {
            "description": "Reduce wall thickness to 2mm",
            "diff": {"action": "update_properties", "wall_mm": 2},
            "work_products_affected": ["node-9"],
            "agent_code": "ME",
        }
    )
    assert out == {"change_id": "cid-1", "status": "pending"}
    assert seen["description"] == "Reduce wall thickness to 2mm"
    assert seen["work_products"] == ["node-9"]
    assert seen["agent_code"] == "ME"


@pytest.mark.asyncio
async def test_propose_change_validates_inputs() -> None:
    async def recorder(**kwargs: Any) -> dict[str, Any]:
        return {"change_id": "x", "status": "pending"}

    server = TwinServer(_FakeTwin(), proposal_recorder=recorder)
    with pytest.raises(ValueError, match="description"):
        await server.propose_change({"diff": {"action": "x"}})
    with pytest.raises(ValueError, match="diff"):
        await server.propose_change({"description": "hi"})


@pytest.mark.asyncio
async def test_recorder_files_pending_proposal_on_workflow() -> None:
    workflow = ApprovalWorkflow()
    propose = make_proposal_recorder(workflow)
    result = await propose(
        agent_code="ME",
        description="Slot the clip",
        diff={"action": "regenerate_geometry", "script": "..."},
        work_products=["11111111-1111-1111-1111-111111111111", "not-a-uuid"],
    )
    assert result["status"] == "pending"
    pending = workflow.get_pending_proposals()
    assert len(pending) == 1
    assert pending[0].description == "Slot the clip"
    # invalid uuid dropped, valid one kept
    assert len(pending[0].work_products_affected) == 1
