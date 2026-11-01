"""Design flows survive the things that used to end them (FORGE-401).

These run against a real Temporal test server, not a double. The whole ticket
is about durability, and a double would cheerfully pass every assertion here
while proving nothing: the old in-process executor also "worked" right up to
the moment the gateway restarted.

The time-skipping server makes the durable-timer test tractable — a 24-hour
gate timeout is asserted in milliseconds of wall clock without weakening it
to a value nobody would ship.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest

pytest.importorskip("temporalio")

from temporalio.testing import WorkflowEnvironment  # noqa: E402

from orchestrator.design_flow.frozen import (  # noqa: E402
    FrozenFlow,
    FrozenGate,
    FrozenPhase,
    freeze_flow,
)
from orchestrator.design_flow.launcher import (  # noqa: E402
    DesignFlowLauncher,
    TemporalUnavailableError,
    connect_temporal,
)
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


# ── fixtures ─────────────────────────────────────────────────────────────


def _flow(*, gated: bool = True, phases: int = 2) -> FrozenFlow:
    built = [
        FrozenPhase(
            id=f"phase{i}",
            title=f"Phase {i}",
            objective=f"do step {i}",
            enforce_deliverables=False,
            gate=(FrozenGate(name=f"gate{i}") if (gated and i == 0) else None),
        )
        for i in range(phases)
    ]
    flow = FrozenFlow(template_id="test_v1", name="Test", phases=built)
    flow.content_hash = flow.compute_hash()
    return flow


@dataclass
class _Phases:
    """A phase runner that records what it was asked and can be made to fail."""

    ran: list[str] = field(default_factory=list)
    fail_times: int = 0
    block: asyncio.Event | None = None

    async def __call__(self, request: PhaseRequest) -> PhaseResult:
        self.ran.append(request.phase.id)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(f"transient failure in {request.phase.id}")
        if self.block is not None:
            await self.block.wait()
        return PhaseResult(summary=f"did {request.phase.id}", artifacts=["wp-1"])


async def _ok_gate(payload: dict) -> GateCheck:
    return GateCheck(ready=True, checked=True, constraints_checked=True, reason="all good")


async def _announced(run_id: str, gate: str, reason: str) -> None:
    return None


def _worker(env: WorkflowEnvironment, activities: DesignFlowActivities):
    # Deliberately the production builder, not a hand-rolled Worker: the
    # sandbox passthrough it configures is exactly the thing that is easy to
    # get right here and wrong in the real wiring.
    return build_design_flow_worker(env.client, activities)


@pytest.fixture
async def env():
    """A real Temporal dev server with a real clock.

    Not ``start_time_skipping()``. That environment jumps its clock forward
    whenever the client goes idle, which a polling test does constantly -- so
    a 24-hour gate timer fires while the test is on its way to answering the
    gate, and every durability test fails as a timeout. Turning skipping off
    is worse: it freezes the clock outright, and nothing progresses at all.

    A real clock costs a few seconds per test and removes a whole class of
    result that depends on when the harness happened to yield. The timeout
    test uses a deliberately short window rather than a skipped-forward long
    one.

    Function-scoped despite the start-up cost: pytest-asyncio gives each
    test its own event loop, and a longer-lived server bound to the first
    test's loop hangs rather than failing when the second test reaches it.
    """
    environment = await WorkflowEnvironment.start_local()
    try:
        yield environment
    finally:
        await environment.shutdown()


# ── the acceptance criteria ──────────────────────────────────────────────


class TestTheRunSurvives:
    async def test_a_gate_waits_across_a_worker_restart(self, env) -> None:
        """The headline. A run parked at a gate used to die with the process
        holding its coroutine. Here the worker is stopped entirely while the
        run waits, a *new* worker is started, and the approval still lands."""
        run_id = str(uuid.uuid4())
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        handle = env.client.get_workflow_handle(f"design-flow-{run_id}")

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="build a thing", flow=_flow())
            await _wait_for_gate(env, launcher, run_id)

        # No worker is running at all now -- the equivalent of the gateway
        # being restarted while a reviewer is at lunch.
        #
        # The check here is `describe()`, not `state()`, and that difference
        # is worth knowing: a workflow *query* is answered by a worker, so
        # with none running there is nobody to answer it. `describe()` is
        # served by the Temporal server itself, so it still reports the truth
        # -- the run exists and is waiting, held by the server rather than by
        # any process. Under the old executor there would be nothing left at
        # all, because the wait lived in a coroutine that died with the task.
        description = await handle.describe()
        assert description.status is not None and description.status.name == "RUNNING"

        acts2 = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        async with _worker(env, acts2):
            # The gate this answers was opened by a worker that no longer
            # exists.
            await launcher.answer_gate(run_id, approved=True, decided_by="user:reviewer")
            result = await handle.result()

        assert result["status"] == "completed"
        assert [p["phase"] for p in result["phases"]] == ["phase0", "phase1"]

    async def test_a_failed_phase_is_retried_and_the_run_completes(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases = _Phases(fail_times=2)
        acts = DesignFlowActivities(phase_runner=phases, gate_checker=_ok_gate)
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow(gated=False, phases=1))
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "completed"
        # Two failures, then success: the activity really was retried rather
        # than the run being abandoned.
        assert phases.ran == ["phase0", "phase0", "phase0"]

    async def test_a_provider_configuration_error_fails_the_run_once_with_the_error(
        self, env
    ) -> None:
        """FORGE-475: a missing key is not a transient failure.

        It used to retry and then fail the workflow with an opaque activity
        error, so run status showed neither the cause nor an end.
        """
        from temporalio.exceptions import ApplicationError

        run_id = str(uuid.uuid4())
        attempts: list[str] = []

        async def no_key(request: PhaseRequest) -> PhaseResult:
            attempts.append(request.phase.id)
            raise ApplicationError(
                "no model provider could serve this phase: missing API key",
                type="ProviderUnavailable",
                non_retryable=True,
            )

        acts = DesignFlowActivities(phase_runner=no_key, gate_checker=_ok_gate)
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow(gated=False, phases=2))
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
            state = await launcher.state(run_id)

        assert attempts == ["phase0"], "a configuration error must not be retried"
        assert result["status"] == "failed"
        assert "phase0" in result["error"]
        assert "missing API key" in result["error"]
        assert state["status"] == "failed"
        assert state["error"] == result["error"]

    async def test_an_unanswered_gate_ends_rejected(self, env) -> None:
        """Timeout means reject. Not "carry on" (which would promote work
        nobody looked at) and not "wait forever" (which leaves a run that
        reads as live)."""
        run_id = str(uuid.uuid4())
        acts = DesignFlowActivities(
            phase_runner=_Phases(), gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(
                run_id=run_id,
                goal="g",
                flow=_flow(),
                # Short on purpose: a real durable timer, just a small one.
                # The property under test is "the window closes and the run
                # ends rejected", and that is the same property at 3 seconds
                # as at 24 hours.
                gate_timeout_seconds=3.0,
            )
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "rejected"
        assert "not answered" in result["error"]

    async def test_a_rejection_stops_the_run(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow())
            await _wait_for_gate(env, launcher, run_id)
            await launcher.answer_gate(
                run_id, approved=False, decided_by="user:reviewer", comment="not yet"
            )
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "rejected"
        assert "user:reviewer" in result["error"]
        # The second phase must not have run.
        assert phases.ran == ["phase0"]


class TestTheApproverIsCarried:
    async def test_the_decision_records_who_made_it(self, env) -> None:
        """FORGE-393's rule reaches the workflow: the name comes from the
        approval, and the workflow writes down what it was given."""
        run_id = str(uuid.uuid4())
        acts = DesignFlowActivities(
            phase_runner=_Phases(), gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow())
            await _wait_for_gate(env, launcher, run_id)
            await launcher.answer_gate(run_id, approved=True, decided_by="user:abc-123")
            events = await launcher.events(run_id)
            await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        approved = [e for e in events if e["event"] == "gate_approved"]
        assert approved and approved[0]["detail"] == "user:abc-123"


class TestTheFlowIsFrozen:
    async def test_a_flow_edited_after_approval_is_refused(self, env) -> None:
        """The reason the flow is input data rather than a module lookup. An
        approved flow that changed underneath is not the flow a human agreed
        to, and running it anyway is the quiet version of not having a gate."""
        flow = _flow()
        flow.phases[0].objective = "something else entirely"  # after hashing

        launcher = DesignFlowLauncher(client=env.client)
        with pytest.raises(ValueError, match="does not match the hash"):
            await launcher.start(run_id=str(uuid.uuid4()), goal="g", flow=flow)

    async def test_an_unfrozen_flow_is_refused(self, env) -> None:
        flow = FrozenFlow(template_id="t", name="T", phases=[])  # no hash
        launcher = DesignFlowLauncher(client=env.client)
        with pytest.raises(ValueError, match="no content_hash"):
            await launcher.start(run_id=str(uuid.uuid4()), goal="g", flow=flow)

    async def test_freezing_a_real_flow_round_trips(self) -> None:
        from orchestrator.design_flow.spec import get_flow

        frozen = freeze_flow(get_flow("hardware_v1"))
        assert frozen.template_id == "hardware_v1"
        assert frozen.phases
        frozen.verify()  # must not raise


class TestMidRunChange:
    async def test_a_change_applies_at_the_gate_and_keeps_finished_work(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        replacement = FrozenFlow(
            template_id="test_v2",
            name="Test v2",
            phases=[
                FrozenPhase(id="phase0", title="P0", objective="x", enforce_deliverables=False),
                FrozenPhase(id="extra", title="Extra", objective="y", enforce_deliverables=False),
            ],
        )
        replacement.content_hash = replacement.compute_hash()

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow())
            await _wait_for_gate(env, launcher, run_id)
            await launcher.request_change(
                run_id, flow=replacement, requested_by="user:pm", rationale="scope change"
            )
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "completed"
        assert result["flow"] == "test_v2"
        # phase0 was already done before the change; it must not run twice.
        assert phases.ran == ["phase0", "extra"]


class TestTemporalDown:
    async def test_connecting_to_nothing_raises_rather_than_falling_back(self) -> None:
        """The most important test in the file, and the one with nothing to
        observe if it regresses -- a silent fallback passes every other test
        here. There is no in-process fallback on purpose."""
        with pytest.raises(TemporalUnavailableError) as exc:
            await connect_temporal("127.0.0.1:1", namespace="default")
        assert "no run was created" in str(exc.value)
        assert "no in-process fallback" in str(exc.value)


class TestReplay:
    async def test_a_completed_run_replays_deterministically(self, env) -> None:
        """Replay is what catches non-determinism that unit tests cannot: a
        stray ``time.time()`` or ``random`` in workflow code passes happily
        until a worker redeploys mid-run."""
        from temporalio.worker import Replayer

        run_id = str(uuid.uuid4())
        acts = DesignFlowActivities(
            phase_runner=_Phases(), gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)

        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=_flow())
            await _wait_for_gate(env, launcher, run_id)
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            handle = env.client.get_workflow_handle(f"design-flow-{run_id}")
            await handle.result()
            history = await handle.fetch_history()

        # Same sandbox configuration as the worker: a replayer built without
        # it fails on the import rather than on any real non-determinism,
        # which would make this test look like it was doing its job while
        # never reaching the check.
        await Replayer(
            workflows=[DesignFlowWorkflow], workflow_runner=design_flow_runner()
        ).replay_workflow(history)


class TestGateEvidence:
    async def test_a_gate_with_no_checker_says_so_rather_than_passing(self, env) -> None:
        """ "Nothing was wrong" and "nothing was looked at" must not render the
        same -- the FORGE-361 rule, applied to gates."""
        acts = DesignFlowActivities(phase_runner=_Phases())
        check = await acts.evaluate_gate({"run_id": "r"})
        assert check.checked is False
        assert "no gate checker" in check.reason

    async def test_missing_deliverables_park_and_cannot_be_approved(self, env) -> None:
        """Asking somebody to approve work the system already knows is
        incomplete trains them to click through. FORGE-495: the gate parks
        with its findings (retry or reject) instead of failing the run, and an
        approve is ignored."""
        run_id = str(uuid.uuid4())
        asked: list[str] = []

        async def announcer(rid: str, gate: str, reason: str) -> None:
            asked.append(gate)

        async def not_ready(payload: dict) -> GateCheck:
            return GateCheck(
                ready=False, checked=True, missing=["cad_model"], present=[], reason="missing"
            )

        flow = _flow()
        flow.phases[0].enforce_deliverables = True
        flow.content_hash = flow.compute_hash()

        acts = DesignFlowActivities(
            phase_runner=_Phases(), gate_checker=not_ready, gate_announcer=announcer
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _wait_for_gate(env, launcher, run_id)
            state = await launcher.state(run_id)
            assert state["gate_ready"] is False
            assert "cad_model" in state["gate_reason"]
            await launcher.answer_gate(run_id, approved=True, decided_by="user:reviewer")
            await asyncio.sleep(0.3)
            assert (await launcher.state(run_id))["awaiting_gate"], "approve must be ignored"
            await launcher.answer_gate(run_id, approved=False, decided_by="user:reviewer")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "rejected"
        assert asked == ["gate0"]


# ── helpers ──────────────────────────────────────────────────────────────


async def _wait_for_gate(
    env: WorkflowEnvironment, launcher: DesignFlowLauncher, run_id: str, tries: int = 200
) -> None:
    """Poll until the run parks at a gate."""
    del env  # kept in the signature so callers read the same at every site
    for _ in range(tries):
        state = await launcher.state(run_id)
        if state["awaiting_gate"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached a gate: {await launcher.state(run_id)}")


class TestDeliverableGateOnTemporal:
    """FORGE-484: same gate contract as the in-process executor."""

    async def _run(self, env, phases, checker):
        """Run to the (not ready) gate, reject it, and return the result."""
        run_id = str(uuid.uuid4())
        asked: list[str] = []

        async def announcer(rid: str, gate: str, reason: str) -> None:
            asked.append(gate)

        flow = _flow()
        flow.phases[0].enforce_deliverables = True
        flow.phases[0].required_deliverables = ["intent"]
        flow.content_hash = flow.compute_hash()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=checker, gate_announcer=announcer
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _wait_for_gate(env, launcher, run_id)
            await launcher.answer_gate(run_id, approved=False, decided_by="user:reviewer")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        return result, asked

    async def test_no_deliverable_parks_with_names_and_phase_failed(self, env) -> None:
        async def none_recorded(payload: dict) -> GateCheck:
            return GateCheck(ready=False, checked=True, missing=["intent"], present=[])

        result, asked = await self._run(env, _Phases(), none_recorded)
        assert result["status"] == "rejected"
        assert result["phases"][0]["status"] == "failed"
        assert asked == ["gate0"]

    async def test_ungrounded_phase_is_not_passed(self, env) -> None:
        from orchestrator.design_flow.grounding import UNGROUNDED_BANNER

        class _Ungrounded(_Phases):
            async def __call__(self, request: PhaseRequest) -> PhaseResult:
                return PhaseResult(summary=f"{UNGROUNDED_BANNER}\n\nbuilt it")

        result, asked = await self._run(env, _Ungrounded(), _ok_gate)
        assert result["status"] == "rejected"
        assert result["phases"][0]["status"] == "failed"
        assert asked == ["gate0"]

    async def test_recorded_deliverable_opens_a_ready_gate_and_lists_artifact(self, env) -> None:
        async def present(payload: dict) -> GateCheck:
            return GateCheck(ready=True, checked=True, present=["intent"], reason="ok")

        run_id = str(uuid.uuid4())
        flow = _flow()
        flow.phases[0].enforce_deliverables = True
        flow.phases[0].required_deliverables = ["intent"]
        flow.content_hash = flow.compute_hash()
        acts = DesignFlowActivities(
            phase_runner=_Phases(), gate_checker=present, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=flow)
            await _wait_for_gate(env, launcher, run_id)
            await launcher.answer_gate(run_id, approved=True, decided_by="user:reviewer")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        assert result["status"] == "completed"
        assert "intent" in result["phases"][0]["artifacts"]


class TestRetryPhase:
    """FORGE-495: a third gate decision re-runs the phase."""

    @staticmethod
    def _flow3() -> FrozenFlow:
        built = [
            FrozenPhase(
                id=f"phase{i}",
                title=f"Phase {i}",
                objective=f"do step {i}",
                enforce_deliverables=(i == 1),
                required_deliverables=(["cad_model"] if i == 1 else []),
                gate=FrozenGate(name=f"gate{i}"),
            )
            for i in range(3)
        ]
        flow = FrozenFlow(template_id="test_v1", name="Test", phases=built)
        flow.content_hash = flow.compute_hash()
        return flow

    @staticmethod
    async def _wait_gate_n(launcher, run_id: str, gate: str, tries: int = 400) -> dict:
        for _ in range(tries):
            state = await launcher.state(run_id)
            if state["awaiting_gate"] == gate:
                return state
            await asyncio.sleep(0.05)
        raise AssertionError(f"never reached {gate}: {await launcher.state(run_id)}")

    async def test_retry_reruns_only_that_phase_with_findings_earlier_kept(self, env) -> None:
        run_id = str(uuid.uuid4())
        requests: list[PhaseRequest] = []
        checks = {"n": 0}

        class _Recording(_Phases):
            async def __call__(self, request: PhaseRequest) -> PhaseResult:
                requests.append(request)
                return await super().__call__(request)

        async def gate(payload: dict) -> GateCheck:
            if payload["phase"]["id"] != "phase1":
                return GateCheck(ready=True, checked=True, constraints_checked=True)
            checks["n"] += 1
            if checks["n"] == 1:
                return GateCheck(ready=False, checked=True, missing=["cad_model"])
            return GateCheck(ready=True, checked=True, present=["cad_model"])

        acts = DesignFlowActivities(
            phase_runner=_Recording(), gate_checker=gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3())
            await self._wait_gate_n(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            state = await self._wait_gate_n(launcher, run_id, "gate1")
            assert state["gate_ready"] is False and state["attempt"] == 1
            assert state["retries_left"] == 3
            await launcher.answer_gate(
                run_id, approved=False, retry=True, decided_by="user:r", comment="record the model"
            )
            # Same gate again, second attempt, now ready.
            for _ in range(400):
                state = await launcher.state(run_id)
                if state["attempt"] == 2 and state["awaiting_gate"] == "gate1":
                    break
                await asyncio.sleep(0.05)
            assert state["gate_ready"] is True and state["retries_left"] == 2
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait_gate_n(launcher, run_id, "gate2")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
            events = await launcher.events(run_id)

        assert result["status"] == "completed"
        assert [r.phase.id for r in requests] == ["phase0", "phase1", "phase1", "phase2"]
        assert requests[2].attempt == 2
        assert requests[2].retry_feedback.startswith("RETRY")
        assert "cad_model" in requests[2].retry_feedback
        assert "record the model" in requests[2].retry_feedback
        assert requests[1].retry_feedback == "" and requests[3].retry_feedback == ""
        assert [p["phase"] for p in result["phases"]] == ["phase0", "phase1", "phase2"]
        names = [e["event"] for e in events]
        assert "gate_not_ready" in names and "phase_retry_requested" in names

    async def test_retry_brief_carries_the_turned_down_revision(self, env) -> None:
        """FORGE-530: the twin read runs in the collect_revision_notes activity and
        the workflow puts the plain notes into the retried phase's brief."""
        from orchestrator.design_flow.rework_context import RevisionNote

        run_id = str(uuid.uuid4())
        requests: list[PhaseRequest] = []
        asked: list[tuple[str, str, str]] = []
        checks = {"n": 0}

        class _Recording(_Phases):
            async def __call__(self, request: PhaseRequest) -> PhaseResult:
                requests.append(request)
                return await super().__call__(request)

        async def gate(payload: dict) -> GateCheck:
            if payload["phase"]["id"] != "phase1":
                return GateCheck(ready=True, checked=True, constraints_checked=True)
            checks["n"] += 1
            if checks["n"] == 1:
                return GateCheck(ready=False, checked=True, missing=["cad_model"])
            return GateCheck(ready=True, checked=True, present=["cad_model"])

        async def notes(run: str, phase_id: str, project_id, reason: str):
            asked.append((run, phase_id, reason))
            return [
                RevisionNote(
                    ref="CAD-BRACKET@2",
                    item_type="cad_model",
                    name="Bracket",
                    status="abandoned",
                    reason=reason,
                    previous_ref="CAD-BRACKET@1",
                    changes=("volume 8000 -> 4000 mm3 (-4000, -50.0%)",),
                )
            ]

        acts = DesignFlowActivities(
            phase_runner=_Recording(),
            gate_checker=gate,
            gate_announcer=_announced,
            revision_notes=notes,
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3())
            await self._wait_gate_n(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait_gate_n(launcher, run_id, "gate1")
            await launcher.answer_gate(
                run_id, approved=False, retry=True, decided_by="user:r", comment="too thin"
            )
            for _ in range(400):
                state = await launcher.state(run_id)
                if state["attempt"] == 2 and state["awaiting_gate"] == "gate1":
                    break
                await asyncio.sleep(0.05)
            await launcher.answer_gate(run_id, approved=False, decided_by="user:r")
            await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert [r.phase.id for r in requests] == ["phase0", "phase1", "phase1"]
        assert len(asked) == 1 and asked[0][:2] == (run_id, "phase1")
        assert "cad_model" in asked[0][2]  # the gate's findings are the reason
        feedback = requests[2].retry_feedback
        assert "  - CAD-BRACKET@2 cad_model 'Bracket' (abandoned)" in feedback
        assert "    Gate's reason: " in feedback and "cad_model" in feedback
        assert "      volume 8000 -> 4000 mm3 (-4000, -50.0%)" in feedback

    async def test_retry_cap_is_enforced(self, env) -> None:
        run_id = str(uuid.uuid4())

        async def never_ready(payload: dict) -> GateCheck:
            if payload["phase"]["id"] == "phase1":
                return GateCheck(ready=False, checked=True, missing=["cad_model"])
            return GateCheck(ready=True, checked=True, constraints_checked=True)

        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=never_ready, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3(), max_phase_retries=1)
            await self._wait_gate_n(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait_gate_n(launcher, run_id, "gate1")
            await launcher.answer_gate(run_id, approved=False, retry=True, decided_by="user:r")
            for _ in range(400):
                state = await launcher.state(run_id)
                if state["attempt"] == 2 and state["awaiting_gate"] == "gate1":
                    break
                await asyncio.sleep(0.05)
            assert state["retries_left"] == 0
            await launcher.answer_gate(run_id, approved=False, retry=True, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "failed"
        assert "after 1 retry" in result["error"]
        assert phases.ran.count("phase1") == 2

    async def test_reject_still_ends_the_run(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3())
            await self._wait_gate_n(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=False, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
        assert result["status"] == "rejected"
        assert phases.ran == ["phase0"]


class TestReworkPhase:
    """FORGE-500: a fourth gate decision sends the run back to an earlier phase."""

    @staticmethod
    def _flow3() -> FrozenFlow:
        built = [
            FrozenPhase(
                id=f"phase{i}",
                title=f"Phase {i}",
                objective=f"do step {i}",
                enforce_deliverables=False,
                gate=FrozenGate(name=f"gate{i}", enforce_constraints=(i == 2)),
            )
            for i in range(3)
        ]
        flow = FrozenFlow(template_id="test_v1", name="Test", phases=built)
        flow.content_hash = flow.compute_hash()
        return flow

    @staticmethod
    def _recording():
        requests: list[PhaseRequest] = []
        announced: list[str] = []

        class _Recording(_Phases):
            async def __call__(self, request: PhaseRequest) -> PhaseResult:
                requests.append(request)
                return await super().__call__(request)

        async def announce(run_id: str, gate: str, reason: str) -> None:
            announced.append(gate)

        return _Recording(), requests, announce, announced

    @staticmethod
    def _violating_final_gate(times: int):
        """A gate check that fails phase2's constraints ``times`` times, then passes."""
        seen = {"n": 0}

        async def gate(payload: dict) -> GateCheck:
            if payload["phase"]["id"] != "phase2":
                return GateCheck(ready=True, checked=True, constraints_checked=True)
            seen["n"] += 1
            if seen["n"] <= times:
                return GateCheck(
                    ready=True,
                    checked=True,
                    constraints_checked=True,
                    constraints_passed=False,
                    violations=["safety factor 1.56 < 2"],
                )
            return GateCheck(ready=True, checked=True, constraints_checked=True)

        return gate

    async def _wait(self, launcher, run_id: str, gate: str, **expect) -> dict:
        for _ in range(400):
            state = await launcher.state(run_id)
            if state["awaiting_gate"] == gate and all(state[k] == v for k, v in expect.items()):
                return state
            await asyncio.sleep(0.05)
        raise AssertionError(f"never reached {gate} {expect}: {await launcher.state(run_id)}")

    async def test_rework_from_the_last_gate_reruns_target_and_later_phases(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases, requests, announce, announced = self._recording()
        acts = DesignFlowActivities(
            phase_runner=phases,
            gate_checker=self._violating_final_gate(1),
            gate_announcer=announce,
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3())
            await self._wait(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate1")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            state = await self._wait(launcher, run_id, "gate2")
            assert state["gate_ready"] is False
            assert state["rework_cycles"] == 0 and state["reworks_left"] == 3
            await launcher.answer_gate(
                run_id,
                approved=False,
                decided_by="user:r",
                comment="thicken the arm",
                rework_to="phase1",
            )
            # Phase 1 re-runs and its gate re-opens; earlier approvals stand.
            state = await self._wait(launcher, run_id, "gate1", rework_cycles=1)
            assert state["reworks_left"] == 2
            assert [c["phase"] for c in state["completed"]] == ["phase0", "phase1"]
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            state = await self._wait(launcher, run_id, "gate2", rework_cycles=1)
            assert state["gate_ready"] is True
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
            events = await launcher.events(run_id)

        assert result["status"] == "completed"
        # phase0 ran once (kept); phase1 and phase2 ran twice, in order.
        assert [r.phase.id for r in requests] == ["phase0", "phase1", "phase2", "phase1", "phase2"]
        # gate0 was never asked again; every later gate was re-opened.
        assert announced == ["gate0", "gate1", "gate2", "gate1", "gate2"]
        first = requests[3].retry_feedback
        assert first.startswith("REWORK")
        assert "safety factor 1.56 < 2" in first  # the gate's findings
        assert "did phase2" in first  # the failing phase's summary
        assert "thicken the arm" in first  # the reviewer's reason
        assert all(r.retry_feedback == "" for r in (requests[0], requests[1], requests[2]))
        assert requests[4].retry_feedback == ""  # only the target phase gets it
        assert [p["phase"] for p in result["phases"]] == ["phase0", "phase1", "phase2"]
        rework = [e for e in events if e["event"] == "phase_rework_requested"]
        assert len(rework) == 1
        assert (rework[0]["from"], rework[0]["to"], rework[0]["cycle"]) == ("phase2", "phase1", 1)

    async def test_rework_cap_is_enforced(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases, _requests, announce, _announced = self._recording()
        acts = DesignFlowActivities(
            phase_runner=phases,
            gate_checker=self._violating_final_gate(99),
            gate_announcer=announce,
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3(), max_rework_cycles=1)
            await self._wait(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate1")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate2")
            await launcher.answer_gate(run_id, approved=False, rework_to="phase1", decided_by="u")
            await self._wait(launcher, run_id, "gate1", rework_cycles=1)
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            state = await self._wait(launcher, run_id, "gate2", rework_cycles=1)
            assert state["reworks_left"] == 0
            await launcher.answer_gate(run_id, approved=False, rework_to="phase1", decided_by="u")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "failed"
        assert "rework cycle" in result["error"] and "per-run cap" in result["error"]

    async def test_an_invalid_target_is_ignored_and_the_gate_stays_answerable(self, env) -> None:
        run_id = str(uuid.uuid4())
        phases, _requests, announce, _announced = self._recording()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=announce
        )
        launcher = DesignFlowLauncher(client=env.client)
        async with _worker(env, acts):
            await launcher.start(run_id=run_id, goal="g", flow=self._flow3())
            await self._wait(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate1")
            # One at a time: signals landing in the same workflow task share one
            # answer slot, and the last wins.
            for n, target in enumerate(("phase1", "nope", "phase2"), start=1):
                await launcher.answer_gate(run_id, approved=False, rework_to=target, decided_by="u")
                for _ in range(200):
                    seen = [
                        e
                        for e in await launcher.events(run_id)
                        if e["event"] == "gate_decision_ignored"
                    ]
                    if len(seen) == n:
                        break
                    await asyncio.sleep(0.05)
            # Still parked at the same gate, and it still takes a real decision.
            state = await self._wait(launcher, run_id, "gate1")
            assert state["rework_cycles"] == 0
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate2")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
            events = await launcher.events(run_id)

        assert result["status"] == "completed"
        ignored = [e for e in events if e["event"] == "gate_decision_ignored"]
        assert len(ignored) == 3
        assert any("not part of this run's flow" in e["detail"] for e in ignored)
        assert any("not earlier" in e["detail"] for e in ignored)

    async def test_a_run_parked_at_its_last_gate_before_the_change_accepts_rework(
        self, env
    ) -> None:
        """Replay safety for runs already parked when FORGE-500 deploys.

        The run is started with the *pre-change* input shape (no rework fields)
        and parked at its final gate. Rework is only ever recorded as new
        history after the wait, and ``workflow.patched`` is consulted only then,
        so the history up to the park is what older code wrote. The test replays
        that history, restarts the worker (a deploy), and sends the rework.
        """
        import dataclasses

        from temporalio.worker import Replayer

        from orchestrator.design_flow.temporal_flow import TASK_QUEUE

        run_id = str(uuid.uuid4())
        flow = self._flow3()
        old_input = {
            "run_id": run_id,
            "goal": "g",
            "flow": dataclasses.asdict(flow),
            "project_id": None,
            "session_id": None,
            "gate_timeout_seconds": 86400.0,
            "completed": [],
            "max_phase_retries": 3,
        }
        assert "max_rework_cycles" not in old_input
        phases, requests, announce, announced = self._recording()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=announce
        )
        launcher = DesignFlowLauncher(client=env.client)
        handle = env.client.get_workflow_handle(f"design-flow-{run_id}")
        async with _worker(env, acts):
            await env.client.start_workflow(
                "DesignFlow", old_input, id=f"design-flow-{run_id}", task_queue=TASK_QUEUE
            )
            await self._wait(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            await self._wait(launcher, run_id, "gate1")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            state = await self._wait(launcher, run_id, "gate2")
            # Defaults apply to the old input shape.
            assert state["max_rework_cycles"] == 3 and state["rework_cycles"] == 0
            history = await handle.fetch_history()

        await Replayer(
            workflows=[DesignFlowWorkflow], workflow_runner=design_flow_runner()
        ).replay_workflow(history)

        # A deploy: no worker, then a new one that must rebuild the run by replay.
        acts2 = DesignFlowActivities(
            phase_runner=phases, gate_checker=_ok_gate, gate_announcer=announce
        )
        async with _worker(env, acts2):
            await launcher.answer_gate(
                run_id, approved=False, decided_by="user:r", comment="redo", rework_to="phase0"
            )
            await self._wait(launcher, run_id, "gate0", rework_cycles=1)
            for gate in ("gate0", "gate1", "gate2"):
                await self._wait(launcher, run_id, gate)
                await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            result = await handle.result()
            final = await handle.fetch_history()

        assert result["status"] == "completed"
        assert [r.phase.id for r in requests] == [
            "phase0",
            "phase1",
            "phase2",
            "phase0",
            "phase1",
            "phase2",
        ]
        assert requests[3].retry_feedback.startswith("REWORK")
        assert "redo" in requests[3].retry_feedback
        assert announced[-3:] == ["gate0", "gate1", "gate2"]
        # The patch marker is recorded with the rework, and the whole history
        # (old part plus new) replays.
        await Replayer(
            workflows=[DesignFlowWorkflow], workflow_runner=design_flow_runner()
        ).replay_workflow(final)


class TestRepairStallForge573:
    """The Temporal engine flags and stops a repair that is not converging."""

    async def test_identical_findings_stop_the_run_before_the_retry_cap(self, env) -> None:
        run_id = str(uuid.uuid4())

        async def never_ready(payload: dict) -> GateCheck:
            if payload["phase"]["id"] == "phase1":
                return GateCheck(ready=False, checked=True, missing=["cad_model"])
            return GateCheck(ready=True, checked=True, constraints_checked=True)

        phases = _Phases()
        acts = DesignFlowActivities(
            phase_runner=phases, gate_checker=never_ready, gate_announcer=_announced
        )
        launcher = DesignFlowLauncher(client=env.client)
        stalled: list[bool] = []
        async with _worker(env, acts):
            await launcher.start(
                run_id=run_id, goal="g", flow=TestRetryPhase._flow3(), max_phase_retries=5
            )
            await TestRetryPhase._wait_gate_n(launcher, run_id, "gate0")
            await launcher.answer_gate(run_id, approved=True, decided_by="user:r")
            for attempt in (1, 2, 3):
                for _ in range(400):
                    state = await launcher.state(run_id)
                    if state["status"] == "failed" or (
                        state["awaiting_gate"] == "gate1" and state["attempt"] == attempt
                    ):
                        break
                    await asyncio.sleep(0.05)
                if state["status"] == "failed":
                    break
                stalled.append(bool(state.get("stalled")))
                await launcher.answer_gate(run_id, approved=False, retry=True, decided_by="user:r")
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

        assert result["status"] == "failed"
        assert "not converging" in result["error"]
        assert stalled == [False, True]
        assert phases.ran.count("phase1") == 3
