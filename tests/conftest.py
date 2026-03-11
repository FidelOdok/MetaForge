"""Root-level test fixtures shared across unit and integration tests.

Provides reusable in-memory implementations of all MetaForge core components:
TwinAPI, McpBridge, EventBus, WorkflowEngine, Scheduler, and test artifacts.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus, EventSubscriber
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    WorkflowDefinition,
    WorkflowStep,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.artifact import Artifact
from twin_core.models.enums import ArtifactType

# ---------------------------------------------------------------------------
# Spy subscriber for capturing events
# ---------------------------------------------------------------------------


class SpySubscriber(EventSubscriber):
    """Records all received events for test assertions."""

    def __init__(self, sub_id: str = "spy", types: set[EventType] | None = None) -> None:
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

    def events_of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.received if e.type == event_type]

    def clear(self) -> None:
        self.received.clear()


# ---------------------------------------------------------------------------
# Core component fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    """Fresh in-memory Digital Twin API."""
    return InMemoryTwinAPI.create()


@pytest.fixture
def mcp_bridge() -> InMemoryMcpBridge:
    """Fresh in-memory MCP bridge with no pre-registered tools."""
    return InMemoryMcpBridge()


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh event bus with no subscribers."""
    return EventBus()


@pytest.fixture
def spy(event_bus: EventBus) -> SpySubscriber:
    """Spy subscriber attached to the event bus, capturing all events."""
    subscriber = SpySubscriber()
    event_bus.subscribe(subscriber)
    return subscriber


@pytest.fixture
def workflow_engine() -> InMemoryWorkflowEngine:
    """Fresh in-memory workflow engine."""
    return InMemoryWorkflowEngine.create()


# ---------------------------------------------------------------------------
# Artifact factories
# ---------------------------------------------------------------------------

MECH_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000001")
EE_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000002")


def make_artifact(
    *,
    artifact_id: UUID | None = None,
    name: str = "test-part",
    artifact_type: ArtifactType = ArtifactType.CAD_MODEL,
    domain: str = "mechanical",
    file_path: str = "cad/bracket.step",
    content_hash: str = "abc123",
    fmt: str = "step",
    created_by: str = "test",
) -> Artifact:
    """Create a test artifact with sensible defaults."""
    return Artifact(
        id=artifact_id or uuid4(),
        name=name,
        type=artifact_type,
        domain=domain,
        file_path=file_path,
        content_hash=content_hash,
        format=fmt,
        created_by=created_by,
    )


@pytest.fixture
async def mech_artifact(twin: InMemoryTwinAPI) -> Artifact:
    """A mechanical CAD model artifact stored in the Twin."""
    artifact = make_artifact(
        artifact_id=MECH_ARTIFACT_ID,
        name="bracket-v1",
        artifact_type=ArtifactType.CAD_MODEL,
        domain="mechanical",
        file_path="cad/bracket.step",
    )
    await twin.create_artifact(artifact)
    return artifact


@pytest.fixture
async def ee_artifact(twin: InMemoryTwinAPI) -> Artifact:
    """An electronics schematic artifact stored in the Twin."""
    artifact = make_artifact(
        artifact_id=EE_ARTIFACT_ID,
        name="main-schematic",
        artifact_type=ArtifactType.SCHEMATIC,
        domain="electronics",
        file_path="eda/kicad/main.kicad_sch",
        fmt="kicad_sch",
    )
    await twin.create_artifact(artifact)
    return artifact


# ---------------------------------------------------------------------------
# Workflow definition helpers
# ---------------------------------------------------------------------------


def make_linear_workflow(
    steps: list[WorkflowStep] | None = None,
    name: str = "test-linear",
) -> WorkflowDefinition:
    """Create a linear A->B->C workflow definition."""
    if steps is None:
        steps = [
            WorkflowStep(step_id="a", agent_code="MECH", task_type="validate_stress"),
            WorkflowStep(
                step_id="b",
                agent_code="MECH",
                task_type="check_tolerances",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="c",
                agent_code="MECH",
                task_type="generate_mesh",
                depends_on=["b"],
            ),
        ]
    return WorkflowDefinition(name=name, steps=steps)


def make_diamond_workflow(name: str = "test-diamond") -> WorkflowDefinition:
    """Create a diamond A->(B,C)->D workflow."""
    return WorkflowDefinition(
        name=name,
        steps=[
            WorkflowStep(step_id="a", agent_code="MECH", task_type="validate_stress"),
            WorkflowStep(
                step_id="b",
                agent_code="MECH",
                task_type="check_tolerances",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="c",
                agent_code="EE",
                task_type="run_erc",
                depends_on=["a"],
            ),
            WorkflowStep(
                step_id="d",
                agent_code="MECH",
                task_type="generate_mesh",
                depends_on=["b", "c"],
            ),
        ],
    )
