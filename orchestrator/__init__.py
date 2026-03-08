"""Orchestrator — coordination engine (the 'brain').

Exports all public types for the orchestrator layer:
event bus, workflow engine, dependency engine, scheduler,
iteration controller, Temporal activities, and workflows.
"""

from orchestrator.dependency_engine import CyclicDependencyError, DependencyGraph
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
    IterationRecord,
    IterationResult,
    IterationStatus,
)
from orchestrator.scheduler import (
    AgentProtocol,
    InMemoryScheduler,
    RetryPolicy,
    ScheduledStep,
    Scheduler,
    SchedulerPriority,
)
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepResult,
    StepStatus,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from orchestrator.activities import (
    AgentActivityInput,
    AgentActivityOutput,
    ApprovalRequest,
    ApprovalResult,
    get_default_retry_policy,
    run_mechanical_agent,
    run_electronics_agent,
    run_firmware_agent,
    run_simulation_agent,
    wait_for_approval,
)
from orchestrator.workflows import (
    SingleAgentWorkflow,
    SingleAgentWorkflowInput,
    SingleAgentWorkflowOutput,
    HardwareDesignWorkflow,
    HardwareDesignWorkflowInput,
    HardwareDesignWorkflowOutput,
)

__all__ = [
    # Event bus
    "AuditEventSubscriber",
    "EventBus",
    "EventSubscriber",
    "WorkflowEventSubscriber",
    "create_default_bus",
    # Workflow DAG
    "InMemoryWorkflowEngine",
    "StepResult",
    "StepStatus",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowRun",
    "WorkflowStatus",
    "WorkflowStep",
    # Dependency engine
    "CyclicDependencyError",
    "DependencyGraph",
    # Scheduler
    "AgentProtocol",
    "InMemoryScheduler",
    "RetryPolicy",
    "Scheduler",
    "ScheduledStep",
    "SchedulerPriority",
    # Iteration controller
    "IterationConfig",
    "IterationController",
    "IterationRecord",
    "IterationResult",
    "IterationStatus",
    # Temporal activities
    "AgentActivityInput",
    "AgentActivityOutput",
    "ApprovalRequest",
    "ApprovalResult",
    "get_default_retry_policy",
    "run_mechanical_agent",
    "run_electronics_agent",
    "run_firmware_agent",
    "run_simulation_agent",
    "wait_for_approval",
    # Temporal workflows
    "SingleAgentWorkflow",
    "SingleAgentWorkflowInput",
    "SingleAgentWorkflowOutput",
    "HardwareDesignWorkflow",
    "HardwareDesignWorkflowInput",
    "HardwareDesignWorkflowOutput",
]
