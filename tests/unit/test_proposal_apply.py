"""Apply-on-approve executor + workflow.mark_applied (MET-548, Phase 3b)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.assistant.apply import make_apply_executor
from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.schemas import ChangeStatus, EventType, WebSocketEvent


@pytest.mark.asyncio
async def test_apply_executor_runs_record_decision() -> None:
    seen: dict = {}

    async def decision_recorder(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"node_id": "n-1", "content_hash": "abc"}

    apply = make_apply_executor(decision_recorder)
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="Record the slot decision",
        diff={"action": "record_decision", "rationale": "slots relieve stress"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is True
    assert result["node_id"] == "n-1"
    assert seen["rationale"] == "slots relieve stress"


@pytest.mark.asyncio
async def test_apply_executor_unsupported_action_is_explicit() -> None:
    apply = make_apply_executor(decision_recorder=None)
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="regen",
        diff={"action": "regenerate_geometry"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is False
    assert "not yet supported" in result["reason"]


@pytest.mark.asyncio
async def test_apply_executor_runs_regenerate_geometry(tmp_path: Any) -> None:
    result_file = tmp_path / "script_result.step"
    result_file.write_bytes(b"ISO-10303-21;\nfake\n")

    class _FakeBridge:
        async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            assert tool == "cadquery.execute_script"
            return {"status": "ok", "data": {"cad_file": str(result_file), "volume_mm3": 42.0}}

    recorded: dict[str, Any] = {}

    async def geometry_recorder(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"node_id": "n-2"}

    apply = make_apply_executor(
        decision_recorder=None, mcp_bridge=_FakeBridge(), geometry_recorder=geometry_recorder
    )
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="Bracket",
        diff={
            "action": "regenerate_geometry",
            "script_source": "pad(15)\n",
            "parameters": {"pad_length_mm": 15},
        },
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is True
    assert result["node_id"] == "n-2"
    assert recorded["script_source"] == "pad(15)\n"
    assert recorded["properties"]["volume_mm3"] == 42.0


@pytest.mark.asyncio
async def test_apply_executor_runs_regenerate_geometry_via_freecad() -> None:
    """cad_tool='freecad' dispatches to the session lifecycle, not cadquery."""
    import base64

    step_bytes = b"ISO-10303-21;\nfreecad body\n"

    class _FakeFreecadBridge:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(tool)
            if tool == "freecad.open_session":
                return {"status": "ok", "data": {"session_id": "sess-1"}}
            if tool == "freecad.execute_code":
                return {"status": "ok", "data": {"obj_id": "obj-1"}}
            if tool == "freecad.export_model":
                return {
                    "status": "ok",
                    "data": {"step_base64": base64.b64encode(step_bytes).decode("ascii")},
                }
            if tool == "freecad.close_session":
                return {"status": "ok", "data": {"closed": True}}
            raise AssertionError(f"unexpected tool: {tool}")

    bridge = _FakeFreecadBridge()
    recorded: dict[str, Any] = {}

    async def geometry_recorder(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"node_id": "n-4"}

    apply = make_apply_executor(
        decision_recorder=None, mcp_bridge=bridge, geometry_recorder=geometry_recorder
    )
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="Bracket",
        diff={
            "action": "regenerate_geometry",
            "script_source": "result = doc.addObject('Part::Box')\n",
            "cad_tool": "freecad",
        },
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is True
    assert result["node_id"] == "n-4"
    assert bridge.calls == [
        "freecad.open_session",
        "freecad.execute_code",
        "freecad.export_model",
        "freecad.close_session",
    ]
    assert base64.b64decode(recorded["step_base64"]) == step_bytes


@pytest.mark.asyncio
async def test_apply_executor_unknown_cad_tool_reports_failure_not_crash() -> None:
    async def geometry_recorder(**kwargs: Any) -> dict[str, Any]:
        return {}

    apply = make_apply_executor(
        decision_recorder=None, mcp_bridge=object(), geometry_recorder=geometry_recorder
    )
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="regen",
        diff={
            "action": "regenerate_geometry",
            "script_source": "pad(10)\n",
            "cad_tool": "solidworks",
        },
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is False
    assert "Unknown cad_tool" in result["reason"]


@pytest.mark.asyncio
async def test_apply_executor_regenerate_geometry_requires_script_source() -> None:
    async def geometry_recorder(**kwargs: Any) -> dict[str, Any]:
        return {}

    apply = make_apply_executor(
        decision_recorder=None, mcp_bridge=object(), geometry_recorder=geometry_recorder
    )
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="regen",
        diff={"action": "regenerate_geometry"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is False
    assert "script_source is required" in result["reason"]


@pytest.mark.asyncio
async def test_apply_executor_regenerate_geometry_failure_is_reported() -> None:
    class _FailBridge:
        async def invoke(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"status": "ok", "data": {}}  # no cad_file

    async def geometry_recorder(**kwargs: Any) -> dict[str, Any]:
        return {}

    apply = make_apply_executor(
        decision_recorder=None, mcp_bridge=_FailBridge(), geometry_recorder=geometry_recorder
    )
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="regen",
        diff={"action": "regenerate_geometry", "script_source": "pad(10)\n"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is False
    assert "no output file" in result["reason"]


@pytest.mark.asyncio
async def test_mark_applied_sets_status_and_emits() -> None:
    from api_gateway.assistant.schemas import ApprovalDecisionType

    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME", description="d", diff={"action": "record_decision"}, work_products=[]
    )
    queue = wf.subscribe(proposal.session_id)

    # can't apply before approval
    await wf.mark_applied(proposal.change_id)
    assert proposal.status == ChangeStatus.PENDING

    await wf.decide(
        change_id=proposal.change_id,
        decision=ApprovalDecisionType.APPROVE,
        reason="ok",
        reviewer="me",
    )
    await wf.mark_applied(proposal.change_id, {"node_id": "n-9"})
    assert proposal.status == ChangeStatus.APPLIED

    seen: list[WebSocketEvent] = []
    while not queue.empty():
        seen.append(queue.get_nowait())
    assert any(e.event_type == EventType.CHANGE_APPLIED for e in seen)
