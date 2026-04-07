# orchestrator

Coordination engine -- the "brain" of MetaForge. Manages workflow DAGs, agent scheduling, dependency resolution, the propose-validate-refine iteration loop, event bus, and Temporal workflow/activity definitions.

## Layer & Dependencies

- **Layer**: 3
- **May import from**: `twin_core`, `digital_twin`, `skill_registry`, `observability`, `pydantic`, standard library
- **Do NOT import from**: `domain_agents`, `api_gateway`, `tool_registry`, `mcp_core`

## Key Files

- `workflow_dag.py` -- `WorkflowDefinition`, `WorkflowStep`, `WorkflowEngine`, `InMemoryWorkflowEngine`
- `dependency_engine.py` -- `DependencyGraph` with cycle detection (`CyclicDependencyError`)
- `scheduler.py` -- `Scheduler`, `InMemoryScheduler`, `RetryPolicy`, priority-based agent queuing
- `iteration_controller.py` -- `IterationController` for propose-validate-refine loops
- `event_bus/` -- Pub/sub system: `EventBus`, `EventSubscriber`, `AuditEventSubscriber`, `WorkflowEventSubscriber`
- `temporal_worker.py` -- Temporal worker bootstrap
- `activities/` -- Temporal activity definitions (agent runners, approval waits)
- `workflows/` -- Temporal workflow definitions (`SingleAgentWorkflow`, `HardwareDesignWorkflow`)

## Testing

```bash
ruff check orchestrator/
mypy --strict orchestrator/
pytest tests/unit/test_orchestrator*.py tests/unit/test_workflow*.py tests/unit/test_scheduler*.py -v
```

## Conventions

- Use `InMemoryWorkflowEngine` and `InMemoryScheduler` in unit tests -- never require Temporal
- Temporal activities must be idempotent and have explicit retry policies
- Event subscribers must not raise exceptions -- log and continue
- All workflow steps must be traceable via OpenTelemetry spans
- Never call domain agent code directly -- invoke through Temporal activities
- Add observability instrumentation (Logs, Metrics, Traces) to all public methods — see [Observability Requirements in root CLAUDE.md](../CLAUDE.md#observability-requirements) for all 7 levels
