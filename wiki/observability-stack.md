---
updated: 2026-08-11
---

# Observability Stack

`observability/` — real and fairly complete, not aspirational (verified 2026-08-11):

- `logging.py` (114 lines) — structlog config.
- `tracing.py` (149 lines) — OTel tracer setup, with a `NoOpTracer` fallback when the SDK isn't installed.
- `metrics.py` (909 lines, the largest file in the package) — Prometheus-style `MetricsCollector` / `MetricsRegistry`.
- `bootstrap.py` (237 lines) — wires OTel + logging + metrics together; imported directly by `api_gateway/server.py`.
- `middleware.py`, `propagation.py`, `cost_attribution.py`, `tenant_isolation.py` — cross-cutting concerns.
- Grafana/Loki/Tempo/Alertmanager/Prometheus config files also live under here.
- Grafana MCP tools give live access to Prometheus/Loki/Tempo — see CLAUDE.md's "Observability Stack (Grafana)" table for datasource UIDs, and `/bug-hunt` for the scan-triage-file workflow built on top of it.
