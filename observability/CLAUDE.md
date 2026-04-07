# observability

Cross-cutting observability layer. Canonical implementation of all 7 observability levels: Logs, Metrics, Traces, Profiling, Alerting, Synthetic Monitoring, and RUM (in progress). Can be imported by any module at any layer.

## Layer & Dependencies

- **Layer**: Cross-cutting (importable by all modules)
- **May import from**: `pydantic`, standard library, OpenTelemetry SDK (optional)
- **Do NOT import from**: any MetaForge module (`twin_core`, `orchestrator`, `domain_agents`, etc.)

## Key Files

- `bootstrap.py` -- `init_observability()` / `shutdown_observability()`, `ObservabilityState`
- `config.py` -- `ObservabilityConfig`, `OtlpExporterConfig`, `PrometheusConfig`, `GrafanaConfig`
- `logging.py` -- `configure_logging()`, `add_trace_context()` (structlog integration)
- `tracing.py` -- `get_tracer()`, `traced` decorator, `NoOpTracer`/`NoOpSpan` fallbacks, `SPAN_CATALOG`
- `metrics.py` -- `MetricDefinition`, `MetricsRegistry`, `MetricsCollector`
- `middleware.py` -- ASGI middleware for request tracing
- `propagation.py` -- Trace context propagation (`extract_trace_context`, `inject_trace_context`)
- `trace_enrichment.py` -- Trace entry enrichment utilities
- `cost_attribution.py` -- LLM cost tracking per agent/skill
- `simulation_metrics.py` -- Simulation-specific metric helpers
- `tenant_isolation.py` -- Multi-tenant metric isolation
- `audit/` -- Audit logging: `logger.py`, `models.py`, `integrity.py`
- `alerting/` -- Alert rule definitions (`rules.yaml`, `routes.yaml`) — Level 5 (Alerting)
- `slo/` -- SLO definitions and calculator
- `dashboards/` -- Grafana dashboard JSON definitions
- `profiling/` -- Pyroscope profiling configuration and label conventions — Level 4 (Profiling); process-level, no per-module instrumentation required
- `synthetic/` -- Synthetic monitoring probe definitions — Level 6 (Synthetic Monitoring); probe logic currently lives in `.claude/agents/dashboard-tester.agent.md`
- `rum/` -- Real User Monitoring configuration — Level 7 (RUM); in progress (MET-288), front-end instrumentation only

## Testing

```bash
ruff check observability/
mypy --strict observability/
pytest tests/unit/test_observability*.py tests/unit/test_metrics*.py tests/unit/test_tracing*.py -v
```

## Conventions

- The system degrades gracefully without the OTel SDK installed -- `NoOpTracer` and `NoOpSpan` are used as fallbacks
- Every module should use `logger = structlog.get_logger(__name__)` and `tracer = get_tracer("module.name")`
- Metrics are registered declaratively via `MetricDefinition` in `MetricsRegistry`
- Never import from other MetaForge modules -- this package must remain dependency-free within the project
- Audit logs must include integrity hashes for tamper detection
- Dashboard JSON files are auto-provisioned via `grafana-dashboards.yml`
- Alert rules for new metrics must be added to `alerting/rules.yaml` — do not create ad-hoc Grafana alerts (Level 5)
- Profiling is process-level and captured automatically via Pyroscope — module authors do not need to add profiling instrumentation (Level 4)
