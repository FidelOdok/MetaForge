"""Graph flows on the real Temporal engine (FORGE-539).

Against a real Temporal dev server, like ``test_design_flow_temporal``: the
properties here are concurrency, durable gates and replay determinism, and a
double would pass every one of them while proving nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest

pytest.importorskip("temporalio")

from temporalio.testing import WorkflowEnvironment  # noqa: E402

from orchestrator.design_flow.frozen import FrozenFlow, FrozenGate, FrozenPhase  # noqa: E402
from orchestrator.design_flow.launcher import DesignFlowLauncher  # noqa: E402
from orchestrator.design_flow.temporal_activities import DesignFlowActivities  # noqa: E402
from orchestrator.design_flow.temporal_flow import (  # noqa: E402
    DesignFlowWorkflow,
    GateCheck,
    PhaseRequest,
    PhaseResult,
)
from orchestrator.design_flow.worker import (  # noqa: E402
    build_design_flow_worker,
    design_flow_runner,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _phase(
    pid: str,
    depends_on: list[str] | None = None,
    *,
    gated: bool = False,
    condition: str | None = None,
) -> FrozenPhase:
    return FrozenPhase(
        id=pid,
        title=pid,
        objective=f"do {pid}",
        enforce_deliverables=False,
        gate=FrozenGate(name=f"{pid} gate") if gated else None,
        depends_on=depends_on,
        condition=condition,
    )


def _flow(phases: list[FrozenPhase], facts: dict[str, str] | None = None) -> FrozenFlow:
    flow = FrozenFlow(template_id="graph_v1", name="Graph", phases=phases, facts=facts or {})
    flow.content_hash = flow.compute_hash()
    return flow


@dataclass
class _Phases:
    """Records what ran; optionally holds every phase until released."""

    ran: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    release: asyncio.Event | None = None

    async def __call__(self, request: PhaseRequest) -> PhaseResult:
        self.started.append(request.phase.id)
        if self.release is not None:
            await self.release.wait()
        self.ran.append(request.phase.id)
        return PhaseResult(summary=f"did {request.phase.id}")


async def _ok_gate(payload: dict) -> GateCheck:
    return GateCheck(ready=True, checked=True, constraints_checked=True, reason="ok")


async def _announced(run_id: str, gate: str, reason: str) -> None:
    return None


@pytest.fixture
async def env():
    environment = await WorkflowEnvironment.start_local()
    try:
        yield environment
    finally:
        await environment.shutdown()


async def _gate_on(launcher: DesignFlowLauncher, run_id: str, phase: str) -> dict:
    for _ in range(300):
        state = await launcher.state(run_id)
        if state["awaiting_gate"] and state["current_phase"] == phase:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {phase}'s gate: {await launcher.state(run_id)}")


class TestGraphRuns:
    async def test_independent_phases_run_together(self, env) -> None:
        # mech and elec both depend only on req. Both must be in flight before
        # either is released: that is concurrency, not just a different order.
        phases = _Phases(release=asyncio.Event())
        acts = DesignFlowActivities(phase_runner=phases, gate_checker=_ok_gate)
        launcher = DesignFlowLauncher(client=env.client)
        flow = _flow(
            [
                _phase("req"),
                _phase("mech", ["req"]),
                _phase("elec", ["req"]),
                _phase("verify", ["mech", "elec"]),
            ]
        )
        run_id = str(uuid.uuid4())
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            for _ in range(200):
                if phases.started == ["req"]:
                    break
                await asyncio.sleep(0.02)
            phases.release.set()
            phases.release = asyncio.Event()  # hold the next wave
            for _ in range(300):
                if sorted(phases.started) == ["elec", "mech", "req"]:
                    break
                await asyncio.sleep(0.02)
            state = await launcher.state(run_id)
            assert sorted(state["running"]) == ["elec", "mech"]
            assert state["mode"] == "graph"
            phases.release.set()
            phases.release = None
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        assert result["status"] == "completed"
        assert phases.started[-1] == "verify"
        assert sorted(p["phase"] for p in result["phases"]) == ["elec", "mech", "req", "verify"]

    async def test_a_false_condition_is_skipped(self, env) -> None:
        phases = _Phases()
        acts = DesignFlowActivities(phase_runner=phases, gate_checker=_ok_gate)
        launcher = DesignFlowLauncher(client=env.client)
        flow = _flow(
            [
                _phase("req"),
                _phase("route_selection", ["req"], condition="route == undecided"),
                _phase("design", ["req", "route_selection"]),
            ],
            facts={"route": "in_house"},
        )
        run_id = str(uuid.uuid4())
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        assert phases.ran == ["req", "design"]
        assert result["skipped"] == ["route_selection"]

    async def test_rework_reruns_only_what_depends_on_the_target(self, env) -> None:
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        flow = _flow(
            [
                _phase("req"),
                _phase("mech", ["req"]),
                _phase("elec", ["req"]),
                _phase("verify", ["mech", "elec"], gated=True),
            ]
        )
        run_id = str(uuid.uuid4())
        handle = env.client.get_workflow_handle(f"design-flow-{run_id}")
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _gate_on(launcher, run_id, "verify")
            await launcher.answer_gate(
                run_id, approved=False, decided_by="user:r", rework_to="mech", comment="SF low"
            )
            for _ in range(300):
                if phases.ran.count("verify") == 2:
                    break
                await asyncio.sleep(0.05)
            await _gate_on(launcher, run_id, "verify")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            result = await handle.result()
            history = await handle.fetch_history()

        assert result["status"] == "completed"
        # req and elec ran once; mech and verify twice.
        assert phases.ran.count("req") == 1
        assert phases.ran.count("elec") == 1
        assert phases.ran.count("mech") == 2
        assert phases.ran.count("verify") == 2
        # Replay is what catches non-determinism in the new path.
        await Replayer_replay(history)

    async def test_rework_to_a_phase_verify_does_not_need_is_ignored(self, env) -> None:
        # 'notes' does not feed 'verify', so verify's gate cannot send the run
        # there: the decision is ignored and the gate stays open.
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        flow = _flow(
            [
                _phase("req"),
                _phase("notes", ["req"]),
                _phase("verify", ["req"], gated=True),
            ]
        )
        run_id = str(uuid.uuid4())
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _gate_on(launcher, run_id, "verify")
            await launcher.answer_gate(run_id, approved=False, decided_by="u", rework_to="notes")
            await asyncio.sleep(0.5)
            state = await launcher.state(run_id)
            assert state["awaiting_gate"] == "verify gate"
            await launcher.answer_gate(run_id, approved=True, decided_by="u")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        assert result["status"] == "completed"
        assert phases.ran.count("notes") == 1

    async def test_a_linear_flow_records_no_graph_marker(self, env) -> None:
        # The replay-safety property for every flow approved before graphs.
        phases = _Phases()
        acts = DesignFlowActivities(phase_runner=phases, gate_checker=_ok_gate)
        launcher = DesignFlowLauncher(client=env.client)
        run_id = str(uuid.uuid4())
        handle = env.client.get_workflow_handle(f"design-flow-{run_id}")
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow([_phase("a"), _phase("b")]))
            await handle.result()
            history = await handle.fetch_history()
        markers = [e for e in history.events if e.HasField("marker_recorded_event_attributes")]
        assert not any(
            b"forge-539-graph" in m.marker_recorded_event_attributes.SerializeToString()
            for m in markers
        )
        await Replayer_replay(history)


async def Replayer_replay(history) -> None:  # noqa: N802 - reads as the call it wraps
    from temporalio.worker import Replayer

    await Replayer(
        workflows=[DesignFlowWorkflow], workflow_runner=design_flow_runner()
    ).replay_workflow(history)


class TestPatchedRuns:
    """FORGE-539: an approved patch re-runs only what it touches."""

    async def _patched(
        self, env, flow: FrozenFlow, new_flow: FrozenFlow, gate_phase: str, rerun: list[str]
    ) -> tuple[_Phases, dict]:
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        run_id = str(uuid.uuid4())
        handle = env.client.get_workflow_handle(f"design-flow-{run_id}")
        async with build_design_flow_worker(env.client, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _gate_on(launcher, run_id, gate_phase)
            await launcher.request_change(
                run_id,
                flow=new_flow,
                requested_by="user:r",
                rationale="payload 15 kg",
                rerun=rerun,
            )
            # The change applies at this gate boundary (continue-as-new); the
            # re-run phases then reach their gates again.
            for _ in range(300):
                if phases.ran.count(gate_phase) >= 2:
                    break
                await asyncio.sleep(0.05)
            for _ in range(10):
                state = await launcher.state(run_id)
                if state.get("status") in ("completed", "failed", "rejected"):
                    break
                if state.get("awaiting_gate"):
                    await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
                await asyncio.sleep(0.3)
            result = await handle.result()
        return phases, result

    async def test_a_graph_patch_keeps_the_untouched_branch(self, env) -> None:
        flow = _flow(
            [
                _phase("req"),
                _phase("mech", ["req"]),
                _phase("elec", ["req"]),
                _phase("verify", ["mech", "elec"], gated=True),
            ]
        )
        new_flow = _flow(
            [
                _phase("req"),
                _phase("mech", ["req"]),
                _phase("elec", ["req"]),
                _phase("verify", ["mech", "elec"], gated=True),
            ],
            facts={"loads_known": "true"},
        )
        phases, result = await self._patched(env, flow, new_flow, "verify", ["mech", "verify"])
        assert result["status"] == "completed"
        assert phases.ran.count("req") == 1 and phases.ran.count("elec") == 1
        assert phases.ran.count("mech") == 2 and phases.ran.count("verify") == 2

    async def test_a_linear_patch_reruns_from_the_first_touched_phase(self, env) -> None:
        flow = _flow([_phase("a"), _phase("b"), _phase("c", gated=True)])
        new_flow = _flow([_phase("a"), _phase("b"), _phase("c", gated=True)], facts={"x": "1"})
        phases, result = await self._patched(env, flow, new_flow, "c", ["b", "c"])
        assert result["status"] == "completed"
        assert phases.ran == ["a", "b", "c", "b", "c"]
