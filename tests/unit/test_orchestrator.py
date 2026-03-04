"""Unit tests for the orchestrator core modules.

Covers:
- EventBus: pub/sub, filtering, error isolation, event log
- WorkflowDAG: models, engine CRUD, start_run, update_step, cancel, status recomputation
- DependencyEngine: linear/diamond/parallel DAGs, cycles, topo sort, ready steps, $ref
- Scheduler: queue, cancel, mock agent, retry, concurrency, priority
- IterationController: single-pass, multi-iteration, max exhausted, auto-approve, branch isolation
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from orchestrator.dependency_engine import CyclicDependencyError, DependencyGraph
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import (
    AuditEventSubscriber,
    EventBus,
    EventSubscriber,
    WorkflowEventSubscriber,
    create_default_bus,
)
from orchestrator.iteration_controller import (
    IterationConfig,
    IterationController,
    IterationStatus,
)
from orchestrator.scheduler import (
    InMemoryScheduler,
    RetryPolicy,
    ScheduledStep,
    SchedulerPriority,
)
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepResult,
    StepStatus,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: EventType = EventType.ARTIFACT_CREATED,
    data: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=str(uuid4()),
        type=event_type,
        timestamp=datetime.now(UTC).isoformat(),
        source="test",
        data=data or {},
    )


def _linear_workflow() -> WorkflowDefinition:
    """A -> B -> C linear chain."""
    return WorkflowDefinition(
        name="linear",
        steps=[
            WorkflowStep(step_id="a", agent_code="MECH", task_type="validate"),
            WorkflowStep(
                step_id="b", agent_code="MECH", task_type="mesh",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="c", agent_code="MECH", task_type="stress",
                depends_on=["b"],
            ),
        ],
    )


def _diamond_workflow() -> WorkflowDefinition:
    """Diamond: A -> (B, C) -> D."""
    return WorkflowDefinition(
        name="diamond",
        steps=[
            WorkflowStep(step_id="a", agent_code="MECH", task_type="start"),
            WorkflowStep(
                step_id="b", agent_code="MECH", task_type="left",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="c", agent_code="EE", task_type="right",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="d", agent_code="MECH", task_type="merge",
                depends_on=["b", "c"],
            ),
        ],
    )


def _parallel_workflow() -> WorkflowDefinition:
    """Two independent parallel steps."""
    return WorkflowDefinition(
        name="parallel",
        steps=[
            WorkflowStep(step_id="x", agent_code="MECH", task_type="mesh"),
            WorkflowStep(step_id="y", agent_code="EE", task_type="erc"),
        ],
    )


def _cyclic_workflow() -> WorkflowDefinition:
    """A -> B -> C -> A (cycle)."""
    return WorkflowDefinition(
        name="cyclic",
        steps=[
            WorkflowStep(
                step_id="a", agent_code="MECH", task_type="x",
                depends_on=["c"],
            ),
            WorkflowStep(
                step_id="b", agent_code="MECH", task_type="y",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="c", agent_code="MECH", task_type="z",
                depends_on=["b"],
            ),
        ],
    )


class _SpySubscriber(EventSubscriber):
    """Records all received events for test assertions."""

    def __init__(
        self,
        sub_id: str = "spy",
        types: set[EventType] | None = None,
    ) -> None:
        self._id = sub_id
        self._types = types
        self.received: list[Event] = []

    @property
    def subscriber_id(self) -> str:
        return self._id

    @property
    def event_types(self) -> set[EventType] | None:
        return self._types

    async def on_event(self, event: Event) -> None:
        self.received.append(event)


class _BrokenSubscriber(EventSubscriber):
    """Always raises on event delivery."""

    @property
    def subscriber_id(self) -> str:
        return "broken"

    @property
    def event_types(self) -> set[EventType] | None:
        return None

    async def on_event(self, event: Event) -> None:
        raise RuntimeError("I am broken")


class _MockAgent:
    """Mock agent implementing AgentProtocol."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[Any] = []

    async def run_task(self, request: Any) -> Any:
        self.calls.append(request)
        if self._error:
            raise self._error
        return self._result or {"status": "ok"}


# =========================================================================
# TestEventBus
# =========================================================================


class TestEventBus:
    """Tests for EventBus pub/sub."""

    @pytest.fixture()
    def bus(self) -> EventBus:
        return EventBus()

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self, bus: EventBus) -> None:
        spy = _SpySubscriber()
        bus.subscribe(spy)
        event = _make_event()
        await bus.publish(event)
        assert len(spy.received) == 1
        assert spy.received[0].id == event.id

    @pytest.mark.asyncio
    async def test_filtered_subscriber(self, bus: EventBus) -> None:
        spy = _SpySubscriber(types={EventType.AGENT_TASK_COMPLETED})
        bus.subscribe(spy)
        await bus.publish(_make_event(EventType.ARTIFACT_CREATED))
        await bus.publish(_make_event(EventType.AGENT_TASK_COMPLETED))
        assert len(spy.received) == 1
        assert spy.received[0].type == EventType.AGENT_TASK_COMPLETED

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus: EventBus) -> None:
        s1 = _SpySubscriber("s1")
        s2 = _SpySubscriber("s2")
        bus.subscribe(s1)
        bus.subscribe(s2)
        await bus.publish(_make_event())
        assert len(s1.received) == 1
        assert len(s2.received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus: EventBus) -> None:
        spy = _SpySubscriber()
        bus.subscribe(spy)
        bus.unsubscribe("spy")
        await bus.publish(_make_event())
        assert len(spy.received) == 0

    @pytest.mark.asyncio
    async def test_error_isolation(self, bus: EventBus) -> None:
        broken = _BrokenSubscriber()
        spy = _SpySubscriber()
        bus.subscribe(broken)
        bus.subscribe(spy)
        await bus.publish(_make_event())
        # spy still receives despite broken subscriber
        assert len(spy.received) == 1

    @pytest.mark.asyncio
    async def test_event_log(self, bus: EventBus) -> None:
        for _ in range(5):
            await bus.publish(_make_event(EventType.ARTIFACT_CREATED))
        for _ in range(3):
            await bus.publish(_make_event(EventType.SESSION_STARTED))

        all_events = bus.get_event_log()
        assert len(all_events) == 8

        artifact_events = bus.get_event_log(event_type=EventType.ARTIFACT_CREATED)
        assert len(artifact_events) == 5

    @pytest.mark.asyncio
    async def test_event_log_limit(self, bus: EventBus) -> None:
        for _ in range(10):
            await bus.publish(_make_event())
        assert len(bus.get_event_log(limit=3)) == 3

    @pytest.mark.asyncio
    async def test_clear(self, bus: EventBus) -> None:
        spy = _SpySubscriber()
        bus.subscribe(spy)
        await bus.publish(_make_event())
        bus.clear()
        assert bus.subscriber_count == 0
        assert len(bus.get_event_log()) == 0

    @pytest.mark.asyncio
    async def test_subscriber_count(self, bus: EventBus) -> None:
        assert bus.subscriber_count == 0
        bus.subscribe(_SpySubscriber("a"))
        bus.subscribe(_SpySubscriber("b"))
        assert bus.subscriber_count == 2

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, bus: EventBus) -> None:
        await bus.publish(_make_event())
        assert len(bus.get_event_log()) == 1

    def test_unsubscribe_nonexistent(self, bus: EventBus) -> None:
        bus.unsubscribe("doesnt_exist")  # no error

    @pytest.mark.asyncio
    async def test_audit_subscriber(self, bus: EventBus) -> None:
        audit = AuditEventSubscriber()
        bus.subscribe(audit)
        await bus.publish(_make_event(EventType.SESSION_STARTED))
        await bus.publish(_make_event(EventType.AGENT_TASK_FAILED))
        # Audit subscribes to all — no assertion on output, just no crash
        assert bus.subscriber_count == 1

    def test_create_default_bus_no_engine(self) -> None:
        bus = create_default_bus()
        assert bus.subscriber_count == 1  # audit only

    def test_create_default_bus_with_engine(self) -> None:
        engine = InMemoryWorkflowEngine.create()
        bus = create_default_bus(workflow_engine=engine)
        assert bus.subscriber_count == 2  # audit + workflow


# =========================================================================
# TestWorkflowDAG
# =========================================================================


class TestWorkflowDAG:
    """Tests for workflow models and InMemoryWorkflowEngine."""

    @pytest.fixture()
    def engine(self) -> InMemoryWorkflowEngine:
        return InMemoryWorkflowEngine.create()

    def test_workflow_step_model(self) -> None:
        step = WorkflowStep(step_id="s1", agent_code="MECH", task_type="mesh")
        assert step.step_id == "s1"
        assert step.timeout_seconds == 300
        assert step.retry_max == 0
        assert step.depends_on == []

    def test_workflow_definition_model(self) -> None:
        defn = _linear_workflow()
        assert defn.name == "linear"
        assert len(defn.steps) == 3

    def test_step_result_defaults(self) -> None:
        sr = StepResult(step_id="x")
        assert sr.status == StepStatus.PENDING
        assert sr.task_result == {}
        assert sr.error is None

    @pytest.mark.asyncio
    async def test_register_and_get_workflow(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        registered = await engine.register_workflow(defn)
        assert registered.id == defn.id
        fetched = await engine.get_workflow(defn.id)
        assert fetched is not None
        assert fetched.name == "linear"

    @pytest.mark.asyncio
    async def test_get_unknown_workflow(self, engine: InMemoryWorkflowEngine) -> None:
        assert await engine.get_workflow("nope") is None

    @pytest.mark.asyncio
    async def test_start_run_unknown_workflow(self, engine: InMemoryWorkflowEngine) -> None:
        with pytest.raises(ValueError, match="Unknown workflow"):
            await engine.start_run("nonexistent")

    @pytest.mark.asyncio
    async def test_start_run_initialises_steps(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        assert run.status == WorkflowStatus.RUNNING
        assert run.step_results["a"].status == StepStatus.READY
        assert run.step_results["b"].status == StepStatus.WAITING
        assert run.step_results["c"].status == StepStatus.WAITING

    @pytest.mark.asyncio
    async def test_start_run_parallel_all_ready(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)
        assert run.step_results["x"].status == StepStatus.READY
        assert run.step_results["y"].status == StepStatus.READY

    @pytest.mark.asyncio
    async def test_update_step_running(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        sr = await engine.update_step(run.id, "a", StepStatus.RUNNING)
        assert sr is not None
        assert sr.status == StepStatus.RUNNING
        assert sr.started_at is not None

    @pytest.mark.asyncio
    async def test_update_step_completed(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "a", StepStatus.RUNNING)
        sr = await engine.update_step(
            run.id, "a", StepStatus.COMPLETED, result={"mesh": "ok"}
        )
        assert sr is not None
        assert sr.status == StepStatus.COMPLETED
        assert sr.task_result == {"mesh": "ok"}
        assert sr.completed_at is not None

    @pytest.mark.asyncio
    async def test_update_step_failed_sets_run_failed(
        self, engine: InMemoryWorkflowEngine
    ) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "a", StepStatus.FAILED, error="boom")
        updated_run = await engine.get_run(run.id)
        assert updated_run is not None
        assert updated_run.status == WorkflowStatus.FAILED

    @pytest.mark.asyncio
    async def test_all_completed_sets_run_completed(
        self, engine: InMemoryWorkflowEngine
    ) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "x", StepStatus.COMPLETED)
        await engine.update_step(run.id, "y", StepStatus.COMPLETED)
        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.status == WorkflowStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel_run(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        cancelled = await engine.cancel_run(run.id)
        assert cancelled is not None
        assert cancelled.status == WorkflowStatus.CANCELLED
        assert cancelled.step_results["b"].status == StepStatus.SKIPPED
        assert cancelled.step_results["c"].status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_cancel_unknown_run(self, engine: InMemoryWorkflowEngine) -> None:
        assert await engine.cancel_run("nope") is None

    @pytest.mark.asyncio
    async def test_update_step_unknown_run(self, engine: InMemoryWorkflowEngine) -> None:
        assert await engine.update_step("nope", "a", StepStatus.RUNNING) is None

    @pytest.mark.asyncio
    async def test_update_step_unknown_step(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)
        assert await engine.update_step(run.id, "zzz", StepStatus.RUNNING) is None

    @pytest.mark.asyncio
    async def test_list_runs_all(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        await engine.start_run(defn.id)
        await engine.start_run(defn.id)
        runs = await engine.list_runs()
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_list_runs_filter_by_status(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run1 = await engine.start_run(defn.id)
        await engine.start_run(defn.id)

        await engine.update_step(run1.id, "x", StepStatus.COMPLETED)
        await engine.update_step(run1.id, "y", StepStatus.COMPLETED)

        completed = await engine.list_runs(status=WorkflowStatus.COMPLETED)
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_run_metadata(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id, metadata={"user": "alice"})
        assert run.metadata == {"user": "alice"}

    @pytest.mark.asyncio
    async def test_start_run_with_branch(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _linear_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id, branch="feat/test")
        assert run.branch == "feat/test"


# =========================================================================
# TestDependencyEngine
# =========================================================================


class TestDependencyEngine:
    """Tests for DependencyGraph."""

    def test_linear_dag_validates(self) -> None:
        graph = DependencyGraph(_linear_workflow())
        graph.validate()  # no exception

    def test_diamond_dag_validates(self) -> None:
        graph = DependencyGraph(_diamond_workflow())
        graph.validate()

    def test_parallel_dag_validates(self) -> None:
        graph = DependencyGraph(_parallel_workflow())
        graph.validate()

    def test_cyclic_dag_raises(self) -> None:
        graph = DependencyGraph(_cyclic_workflow())
        with pytest.raises(CyclicDependencyError):
            graph.validate()

    def test_unknown_dependency_raises(self) -> None:
        defn = WorkflowDefinition(
            name="bad",
            steps=[
                WorkflowStep(
                    step_id="a", agent_code="X", task_type="y",
                    depends_on=["nonexistent"],
                ),
            ],
        )
        with pytest.raises(ValueError, match="unknown step"):
            DependencyGraph(defn)

    def test_topological_sort_linear(self) -> None:
        graph = DependencyGraph(_linear_workflow())
        order = graph.topological_sort()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_topological_sort_diamond(self) -> None:
        graph = DependencyGraph(_diamond_workflow())
        order = graph.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    @pytest.mark.asyncio
    async def test_get_ready_steps_initial(self) -> None:
        defn = _diamond_workflow()
        engine = InMemoryWorkflowEngine.create()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        graph = DependencyGraph(defn)
        ready = graph.get_ready_steps(run)
        assert ready == ["a"]

    @pytest.mark.asyncio
    async def test_get_ready_steps_after_completion(self) -> None:
        defn = _diamond_workflow()
        engine = InMemoryWorkflowEngine.create()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "a", StepStatus.COMPLETED)
        graph = DependencyGraph(defn)
        ready = sorted(graph.get_ready_steps(run))
        assert ready == ["b", "c"]

    @pytest.mark.asyncio
    async def test_get_ready_steps_diamond_convergence(self) -> None:
        defn = _diamond_workflow()
        engine = InMemoryWorkflowEngine.create()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "a", StepStatus.COMPLETED)
        await engine.update_step(run.id, "b", StepStatus.COMPLETED)

        graph = DependencyGraph(defn)
        ready = graph.get_ready_steps(run)
        # d needs both b AND c — only c is ready
        assert "c" in ready
        assert "d" not in ready

    @pytest.mark.asyncio
    async def test_get_ready_steps_both_converged(self) -> None:
        defn = _diamond_workflow()
        engine = InMemoryWorkflowEngine.create()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        await engine.update_step(run.id, "a", StepStatus.COMPLETED)
        await engine.update_step(run.id, "b", StepStatus.COMPLETED)
        await engine.update_step(run.id, "c", StepStatus.COMPLETED)

        graph = DependencyGraph(defn)
        ready = graph.get_ready_steps(run)
        assert ready == ["d"]

    def test_get_dependents(self) -> None:
        graph = DependencyGraph(_diamond_workflow())
        deps = sorted(graph.get_dependents("a"))
        assert deps == ["b", "c"]

    def test_get_dependencies(self) -> None:
        graph = DependencyGraph(_diamond_workflow())
        deps = sorted(graph.get_dependencies("d"))
        assert deps == ["b", "c"]

    def test_resolve_step_inputs_no_refs(self) -> None:
        step = WorkflowStep(
            step_id="s", agent_code="X", task_type="y",
            parameters={"key": "value", "num": 42},
        )
        graph = DependencyGraph(_linear_workflow())
        resolved = graph.resolve_step_inputs(step, {})
        assert resolved == {"key": "value", "num": 42}

    def test_resolve_step_inputs_with_refs(self) -> None:
        step = WorkflowStep(
            step_id="s", agent_code="X", task_type="y",
            parameters={"mesh": "$ref:a.mesh_file", "plain": "hello"},
        )
        completed = {"a": {"mesh_file": "/path/mesh.inp", "nodes": 1000}}
        graph = DependencyGraph(_linear_workflow())
        resolved = graph.resolve_step_inputs(step, completed)
        assert resolved == {"mesh": "/path/mesh.inp", "plain": "hello"}

    def test_resolve_step_inputs_missing_ref(self) -> None:
        step = WorkflowStep(
            step_id="s", agent_code="X", task_type="y",
            parameters={"missing": "$ref:unknown.field"},
        )
        graph = DependencyGraph(_linear_workflow())
        resolved = graph.resolve_step_inputs(step, {})
        assert resolved == {"missing": "$ref:unknown.field"}

    def test_get_step(self) -> None:
        graph = DependencyGraph(_linear_workflow())
        assert graph.get_step("a") is not None
        assert graph.get_step("a").agent_code == "MECH"
        assert graph.get_step("zzz") is None


# =========================================================================
# TestScheduler
# =========================================================================


class TestScheduler:
    """Tests for InMemoryScheduler."""

    @pytest.fixture()
    def engine(self) -> InMemoryWorkflowEngine:
        return InMemoryWorkflowEngine.create()

    @pytest.fixture()
    def bus(self) -> EventBus:
        return EventBus()

    def _make_step(self, **overrides: Any) -> ScheduledStep:
        defaults = {
            "run_id": "run-1",
            "step_id": "step-1",
            "agent_code": "MECH",
            "task_type": "validate",
        }
        defaults.update(overrides)
        return ScheduledStep(**defaults)

    @pytest.mark.asyncio
    async def test_schedule_step(self, engine: InMemoryWorkflowEngine) -> None:
        sched = InMemoryScheduler(engine)
        step = self._make_step()
        await sched.schedule_step(step)
        assert sched.get_queue_size() == 1

    @pytest.mark.asyncio
    async def test_active_count_starts_zero(self, engine: InMemoryWorkflowEngine) -> None:
        sched = InMemoryScheduler(engine)
        assert sched.get_active_count() == 0

    @pytest.mark.asyncio
    async def test_register_and_execute(
        self, engine: InMemoryWorkflowEngine, bus: EventBus
    ) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        agent = _MockAgent(result={"status": "ok"})
        sched = InMemoryScheduler(engine, event_bus=bus)
        sched.register_agent("MECH", agent)

        await sched.start()
        await sched.schedule_step(self._make_step(
            run_id=run.id, step_id="x", agent_code="MECH",
        ))

        # Give the loop time to process
        await asyncio.sleep(0.2)
        await sched.stop()

        assert len(agent.calls) == 1
        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_agent_not_found_fails_step(
        self, engine: InMemoryWorkflowEngine
    ) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        sched = InMemoryScheduler(engine)
        # No agent registered for MECH
        await sched.start()
        await sched.schedule_step(self._make_step(
            run_id=run.id, step_id="x", agent_code="MECH",
        ))

        await asyncio.sleep(0.2)
        await sched.stop()

        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.FAILED

    @pytest.mark.asyncio
    async def test_retry_on_failure(
        self, engine: InMemoryWorkflowEngine
    ) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        # Fail on first call, succeed on second
        call_count = 0

        class _FlakeyAgent:
            async def run_task(self, req: Any) -> dict[str, str]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("transient error")
                return {"status": "ok"}

        sched = InMemoryScheduler(engine)
        sched.register_agent("MECH", _FlakeyAgent())  # type: ignore[arg-type]

        await sched.start()
        await sched.schedule_step(self._make_step(
            run_id=run.id,
            step_id="x",
            agent_code="MECH",
            retry_policy=RetryPolicy(max_retries=1, backoff_seconds=0.05),
        ))

        await asyncio.sleep(0.5)
        await sched.stop()

        assert call_count == 2
        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_priority_ordering(self, engine: InMemoryWorkflowEngine) -> None:
        sched = InMemoryScheduler(engine)

        urgent = self._make_step(step_id="urgent", priority=SchedulerPriority.URGENT)
        low = self._make_step(step_id="low", priority=SchedulerPriority.LOW)
        normal = self._make_step(step_id="normal", priority=SchedulerPriority.NORMAL)

        # Schedule in wrong order
        await sched.schedule_step(low)
        await sched.schedule_step(normal)
        await sched.schedule_step(urgent)

        # Dequeue and check priority order
        items = []
        while not sched._queue.empty():
            prio, ts, step = sched._queue.get_nowait()
            items.append((prio, step.step_id))

        assert items[0][1] == "urgent"
        assert items[1][1] == "normal"
        assert items[2][1] == "low"

    @pytest.mark.asyncio
    async def test_cancel_step(self, engine: InMemoryWorkflowEngine) -> None:
        sched = InMemoryScheduler(engine)
        result = await sched.cancel_step("run-1", "step-1")
        # Nothing active, but it marks the key as cancelled
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_run(self, engine: InMemoryWorkflowEngine) -> None:
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        graph = DependencyGraph(defn)
        sched = InMemoryScheduler(engine, dependency_graph=graph)
        sched.register_agent("MECH", _MockAgent())
        sched.register_agent("EE", _MockAgent())

        await sched.start()
        await sched.execute_run(run)

        await asyncio.sleep(0.3)
        await sched.stop()

        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.COMPLETED
        assert updated.step_results["y"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, engine: InMemoryWorkflowEngine) -> None:
        sched = InMemoryScheduler(engine)
        await sched.stop()  # no error when not started


# =========================================================================
# TestIterationController
# =========================================================================


class TestIterationController:
    """Tests for IterationController."""

    def _mock_twin(
        self,
        constraints_pass: bool = True,
        constraint_errors: list[str] | None = None,
    ) -> AsyncMock:
        twin = AsyncMock()
        twin.create_branch = AsyncMock(return_value="iterate/test")
        twin.evaluate_constraints = AsyncMock(return_value=MagicMock(
            passed=constraints_pass,
            violations=[
                MagicMock(message=e)
                for e in (constraint_errors or [])
            ],
        ))
        twin.commit = AsyncMock()
        twin.merge = AsyncMock()
        return twin

    def _mock_agent(self, result: Any = None) -> _MockAgent:
        return _MockAgent(result=result or {"success": True})

    @pytest.mark.asyncio
    async def test_single_pass_approved(self) -> None:
        twin = self._mock_twin(constraints_pass=True)
        agent = self._mock_agent()
        ctrl = IterationController(twin, IterationConfig(auto_approve=True))

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={"key": "val"},
        )

        assert result.status == IterationStatus.APPROVED
        assert result.total_iterations == 1
        assert len(result.records) == 1
        assert result.records[0].constraints_passed is True
        twin.commit.assert_called_once()
        twin.merge.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_iteration_then_converge(self) -> None:
        twin = self._mock_twin()
        # Fail first 2 iterations, pass on 3rd
        call_count = 0

        async def evaluate_side_effect(branch: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return MagicMock(
                    passed=False,
                    violations=[MagicMock(message=f"error {call_count}")],
                )
            return MagicMock(passed=True, violations=[])

        twin.evaluate_constraints = AsyncMock(side_effect=evaluate_side_effect)

        agent = self._mock_agent()
        ctrl = IterationController(twin, IterationConfig(auto_approve=True))

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        assert result.status == IterationStatus.APPROVED
        assert result.total_iterations == 3
        assert len(result.records) == 3
        assert result.records[0].constraints_passed is False
        assert result.records[2].constraints_passed is True

    @pytest.mark.asyncio
    async def test_max_iterations_exhausted(self) -> None:
        twin = self._mock_twin(constraints_pass=False, constraint_errors=["bad"])
        agent = self._mock_agent()
        ctrl = IterationController(twin, IterationConfig(max_iterations=3))

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        assert result.status == IterationStatus.FAILED
        assert result.total_iterations == 3
        assert "exhausted" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_agent_failure(self) -> None:
        twin = self._mock_twin()
        agent = _MockAgent(error=RuntimeError("agent crash"))
        ctrl = IterationController(twin, IterationConfig())

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        assert result.status == IterationStatus.FAILED
        assert "agent crash" in (result.error or "")

    @pytest.mark.asyncio
    async def test_branch_creation_failure(self) -> None:
        twin = self._mock_twin()
        twin.create_branch = AsyncMock(side_effect=RuntimeError("neo4j down"))
        agent = self._mock_agent()
        ctrl = IterationController(twin)

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        assert result.status == IterationStatus.FAILED
        assert "Branch creation" in (result.error or "")

    @pytest.mark.asyncio
    async def test_converged_with_approval_workflow(self) -> None:
        twin = self._mock_twin(constraints_pass=True)
        agent = self._mock_agent()
        approval = MagicMock()  # non-None approval workflow

        ctrl = IterationController(
            twin,
            IterationConfig(auto_approve=False),
            approval_workflow=approval,
        )

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        # With approval workflow + auto_approve=False → CONVERGED (awaiting human)
        assert result.status == IterationStatus.CONVERGED
        twin.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_approve_without_workflow(self) -> None:
        twin = self._mock_twin(constraints_pass=True)
        agent = self._mock_agent()
        ctrl = IterationController(
            twin, IterationConfig(auto_approve=False)
        )

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
        )

        # No approval workflow → auto-approved even with auto_approve=False
        assert result.status == IterationStatus.APPROVED

    @pytest.mark.asyncio
    async def test_branch_isolation(self) -> None:
        twin = self._mock_twin(constraints_pass=True)
        agent = self._mock_agent()
        ctrl = IterationController(twin, IterationConfig(auto_approve=True))

        result = await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={},
            source_branch="feat/test",
        )

        assert result.source_branch == "feat/test"
        assert result.branch.startswith("iterate/")
        twin.create_branch.assert_called_once()
        call_args = twin.create_branch.call_args
        assert call_args[1].get("from_branch") == "feat/test" or call_args[0][1] == "feat/test"

    @pytest.mark.asyncio
    async def test_refine_adds_iteration_metadata(self) -> None:
        twin = self._mock_twin()
        # Fail first, pass second
        call_count = 0

        async def evaluate_side_effect(branch: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(
                    passed=False,
                    violations=[MagicMock(message="too hot")],
                )
            return MagicMock(passed=True, violations=[])

        twin.evaluate_constraints = AsyncMock(side_effect=evaluate_side_effect)

        captured_requests: list[Any] = []

        class _CapturingAgent:
            async def run_task(self, request: Any) -> dict[str, bool]:
                captured_requests.append(request)
                return {"success": True}

        agent = _CapturingAgent()
        ctrl = IterationController(twin, IterationConfig(auto_approve=True))

        await ctrl.run_iteration_loop(
            agent=agent,
            agent_code="MECH",
            task_type="validate",
            artifact_id=str(uuid4()),
            parameters={"original": True},
        )

        assert len(captured_requests) == 2
        # Second request should have _iteration and _previous_errors
        second_params = captured_requests[1].parameters
        assert second_params.get("_iteration") == 2
        assert second_params.get("_previous_errors") == ["too hot"]
        assert second_params.get("original") is True


# =========================================================================
# TestWorkflowEventSubscriber
# =========================================================================


class TestWorkflowEventSubscriber:
    """Tests for the WorkflowEventSubscriber integration."""

    @pytest.mark.asyncio
    async def test_forwards_completed_event(self) -> None:
        engine = InMemoryWorkflowEngine.create()
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        sub = WorkflowEventSubscriber(engine)
        event = _make_event(
            EventType.AGENT_TASK_COMPLETED,
            data={
                "run_id": run.id,
                "step_id": "x",
                "result": {"mesh": "ok"},
            },
        )
        await sub.on_event(event)

        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_ignores_missing_run_id(self) -> None:
        engine = InMemoryWorkflowEngine.create()
        sub = WorkflowEventSubscriber(engine)
        event = _make_event(
            EventType.AGENT_TASK_STARTED,
            data={"step_id": "x"},
        )
        # Should not raise
        await sub.on_event(event)

    @pytest.mark.asyncio
    async def test_forwards_failed_event(self) -> None:
        engine = InMemoryWorkflowEngine.create()
        defn = _parallel_workflow()
        await engine.register_workflow(defn)
        run = await engine.start_run(defn.id)

        sub = WorkflowEventSubscriber(engine)
        event = _make_event(
            EventType.AGENT_TASK_FAILED,
            data={
                "run_id": run.id,
                "step_id": "x",
                "error": "out of memory",
            },
        )
        await sub.on_event(event)

        updated = await engine.get_run(run.id)
        assert updated is not None
        assert updated.step_results["x"].status == StepStatus.FAILED
