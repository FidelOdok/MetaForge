# Orchestrator

The coordination engine ("the brain") — `orchestrator/`.

- **Reality check (verified 2026-08-11)**: everything actually wired into the gateway today is the **in-memory** variant. `orchestrator/temporal_worker.py` has real Temporal scaffolding, but there's no evidence of a live Temporal backend actually running in this codebase — don't assume ADR-001's PydanticAI + Temporal design is deployed just because the scaffolding exists in the tree.
- `scheduler.py` — `Scheduler` ABC + `InMemoryScheduler`.
- `workflow_dag.py` — `WorkflowEngine` ABC + `InMemoryWorkflowEngine`.
- `dependency_engine.py`, `iteration_controller.py` — inter-agent dependency resolution, propose-validate-refine loop.
- Subpackages: `design_flow/`, `event_bus/`, `activities/`, `workflows/`.
- Start reading at `orchestrator/scheduler.py`, then `orchestrator/workflow_dag.py`.

See `docs/architecture/robust-harness-design.md` for the ReAct-loop-level harness design (a related but distinct layer from this orchestrator).
