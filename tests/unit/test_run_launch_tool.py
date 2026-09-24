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


async def _noop_launch_flow(_run_id: str) -> None:
    """Async no-op stand-in for routes._launch_flow (now async, FORGE-87)."""
    return None


class _FakeCreatedProject:
    def __init__(self, project_id: str) -> None:
        self.id = project_id


class _FakeProjectBackend:
    """Records create_project calls, returns a fixed-id project each time.

    Mirrors ProjectBackend.create_project's real signature exactly (all
    three of name/description/status required, no catch-all **kwargs) --
    a looser fake here is exactly how FORGE-87's first cut shipped a
    missing required `status` kwarg without any test catching it: the
    real PgProjectBackend/InMemoryProjectBackend both require it with no
    default, but a permissive fake tolerated the call anyway.
    """

    def __init__(self, project_id: str = "auto-proj-1") -> None:
        self.project_id = project_id
        self.calls: list[dict[str, Any]] = []

    async def create_project(self, *, name: str, description: str, status: str) -> Any:
        self.calls.append({"name": name, "description": description, "status": status})
        return _FakeCreatedProject(self.project_id)


# --- launcher --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_launcher_creates_and_launches_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    launched: list[str] = []

    async def _launch(run_id: str) -> None:
        launched.append(run_id)

    monkeypatch.setattr(routes, "_launch_flow", _launch)

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
    # FORGE-48: G0 (intent)/G1 (needs) now precede every flow's requirements
    # phase (G2); FORGE-60 adds G3 (feasibility) right after it; FORGE-73
    # adds concept_selection (G5) right after architecture (G4) -- so
    # hardware_v1 is 11 phases starting intent -> needs -> requirements ->
    # feasibility.
    assert out["phases"][0] == "intent" and out["phases"][1] == "needs"
    assert out["phases"][2] == "requirements" and out["phases"][3] == "feasibility"
    assert len(out["phases"]) == 11
    assert "approval" in out["note"]


@pytest.mark.asyncio
async def test_launcher_rejects_unknown_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    monkeypatch.setattr(routes, "_launch_flow", _noop_launch_flow)
    with pytest.raises(ValueError, match="unknown flow"):
        await make_run_launcher().start(goal="g", flow="planning_v1")


@pytest.mark.asyncio
async def test_launcher_status_reports_gate_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.runs.routes as routes

    monkeypatch.setattr(routes, "_launch_flow", _noop_launch_flow)
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


# --- _ensure_run_project (FORGE-87) -----------------------------------------------
@pytest.mark.asyncio
async def test_ensure_run_project_autocreates_when_missing() -> None:
    import api_gateway.runs.routes as routes

    run = routes._store.create({"goal": "a 2-axis camera gimbal"})
    backend = _FakeProjectBackend("auto-proj-1")

    await routes._ensure_run_project(run, backend)

    assert run.request["project_id"] == "auto-proj-1"
    assert backend.calls == [
        {"name": "a 2-axis camera gimbal", "description": "", "status": "draft"}
    ]


@pytest.mark.asyncio
async def test_ensure_run_project_leaves_existing_project_id_alone() -> None:
    import api_gateway.runs.routes as routes

    run = routes._store.create({"goal": "a gimbal", "project_id": "p-already-there"})
    backend = _FakeProjectBackend("auto-proj-1")

    await routes._ensure_run_project(run, backend)

    assert run.request["project_id"] == "p-already-there"
    assert backend.calls == []  # never called -- nothing to auto-create


@pytest.mark.asyncio
async def test_ensure_run_project_truncates_long_goal_for_the_name() -> None:
    import api_gateway.runs.routes as routes

    long_goal = "x" * 200
    run = routes._store.create({"goal": long_goal})
    backend = _FakeProjectBackend()

    await routes._ensure_run_project(run, backend)

    assert backend.calls[0]["name"] == long_goal[:80]
    assert backend.calls[0]["description"] == long_goal


@pytest.mark.asyncio
async def test_ensure_run_project_defaults_name_when_no_goal() -> None:
    import api_gateway.runs.routes as routes

    run = routes._store.create({})
    backend = _FakeProjectBackend()

    await routes._ensure_run_project(run, backend)

    assert backend.calls[0]["name"] == "Untitled design"
