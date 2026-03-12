# Incident Runbooks

On-call runbook documentation for MetaForge critical alerts. Each runbook provides diagnosis steps, resolution procedures, escalation paths, and communication templates.

Alert rules are defined in [`observability/alerting/rules.yaml`](../alerting/rules.yaml). Grafana dashboards are in [`observability/dashboards/`](../dashboards/).

## Critical Alert Runbooks

| Alert | Runbook | Trigger |
|-------|---------|---------|
| GatewayDown | [gateway_down.md](gateway_down.md) | Gateway unreachable for >1 min |
| KafkaConsumerStopped | [kafka_consumer_stopped.md](kafka_consumer_stopped.md) | Consumer group has zero consumption with non-zero lag for >5 min |
| Neo4jUnreachable | [neo4j_unreachable.md](neo4j_unreachable.md) | Zero active Neo4j connections for >1 min |
| DeviceTelemetryStopped | [device_telemetry_stopped.md](device_telemetry_stopped.md) | No MQTT messages from any device for >5 min |
| FleetAnomalyPattern | [fleet_anomaly_pattern.md](fleet_anomaly_pattern.md) | >10% of fleet devices reporting errors for >5 min |

## Runbook Structure

Each runbook follows a standard template:

1. **Alert Description** -- severity, PromQL expression, firing duration, relevant dashboard
2. **Symptoms** -- what the on-call operator will observe
3. **Diagnosis Steps** -- step-by-step investigation checklist
4. **Resolution Procedures** -- concrete actions to fix the issue
5. **Escalation Path** -- when and who to escalate to, by timeframe
6. **Communication Template** -- copy-paste incident notification for internal channels
