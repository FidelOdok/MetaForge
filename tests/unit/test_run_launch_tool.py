"""Chat-triggered design flows: run.start_design_flow / run.get_status (MET-587).

Network- and LLM-free: the launcher's flow launch is monkeypatched at the
routes seam (a real launch spawns the phase-brain executor), and the adapter
is exercised with a fake launcher.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.runs.launcher import make_run_launcher
from tool_registry.tools.runs.adapter import RunsServer


# --- launcher --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_launcher_creates_and_launches_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    launched: list[str] = []
    monkeypatch.setattr(routes, "_launch_flow", launched.append)

    out = await make_run_launcher().start(
        goal="a 2-axis camera gimbal", flow="hardware_v1", project_id="p-1", session_id="s-1"
    )
    assert launched == [out["run_id"]]
    run = routes._store.get(out["run_id"])
    assert run.request == {
        "goal": "a 2-axis camera gimbal",
        "flow": "hardware_v1",
        "project_id": "p-1",
        "session_id": "s-1",
    }
    assert out["phases"][0] == "requirements" and len(out["phases"]) == 7
    assert "approval" in out["note"]


@pytest.mark.asyncio
async def test_launcher_rejects_unknown_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    monkeypatch.setattr(routes, "_launch_flow", lambda _rid: None)
    with pytest.raises(ValueError, match="unknown flow"):
        await make_run_launcher().start(goal="g", flow="planning_v1")


@pytest.mark.asyncio
async def test_launcher_status_reports_gate_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    monkeypatch.setattr(routes, "_launch_flow", lambda _rid: None)
    launcher = make_run_launcher()
    out = await launcher.start(goal="g", flow="mech_v1")
    rid = out["run_id"]
    routes._store.start(rid)
    routes._store.request_approval(rid, reason="[Requirements sign-off] ... | Constraints: OK")

    status = await launcher.status(run_id=rid)
    assert status["status"] == "awaiting_approval"
    assert "Constraints" in status["awaiting_approval_reason"]
    assert status["flow"] == "mech_v1"


# --- adapter ----------------------------------------------------------------------
class _FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", kwargs))
        return {"run_id": "r-1", "flow": kwargs["flow"], "phases": [], "status": "running"}

    async def status(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("status", kwargs))
        return {"run_id": kwargs["run_id"], "status": "completed"}


@pytest.mark.asyncio
async def test_adapter_registers_both_tools() -> None:
    server = RunsServer(launcher=_FakeLauncher())
    assert {"run.start_design_flow", "run.get_status"} <= set(server.tool_ids)


@pytest.mark.asyncio
async def test_adapter_start_defaults_flow_and_passes_project() -> None:
    launcher = _FakeLauncher()
    server = RunsServer(launcher=launcher)
    out = await server.start_design_flow({"goal": "a drone", "project_id": "p-9"})
    assert out["run_id"] == "r-1"
    _, kwargs = launcher.calls[0]
    assert kwargs["flow"] == "hardware_v1"  # default lifecycle
    assert kwargs["project_id"] == "p-9"


@pytest.mark.asyncio
async def test_adapter_validates_arguments() -> None:
    server = RunsServer(launcher=_FakeLauncher())
    with pytest.raises(ValueError, match="goal"):
        await server.start_design_flow({})
    with pytest.raises(ValueError, match="run_id"):
        await server.get_status({})


@pytest.mark.asyncio
async def test_adapter_status_passthrough() -> None:
    launcher = _FakeLauncher()
    server = RunsServer(launcher=launcher)
    out = await server.get_status({"run_id": "r-7"})
    assert out == {"run_id": "r-7", "status": "completed"}
