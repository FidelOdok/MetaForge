# Runbook: FleetAnomalyPattern

## Alert Description

The **FleetAnomalyPattern** alert fires when more than 10% of the device fleet is simultaneously reporting telemetry errors. This indicates a systemic issue -- a bad firmware push, a backend change that breaks ingestion, or an environmental event affecting many devices at once.

**Alert rule**: `(sum(rate(metaforge_telemetry_ingestion_total{status="error"}[5m])) / sum(rate(metaforge_telemetry_ingestion_total[5m]))) > 0.10` for 5 minutes.

## Severity

**Critical** -- pages on-call engineer immediately via PagerDuty.

## Dashboard Links

- [Fleet Health](https://grafana.metaforge.dev/d/metaforge-fleet-health) -- Anomaly Indicators, Per-Device Drill-down
- [System Overview](https://grafana.metaforge.dev/d/metaforge-system-overview) -- overall platform health

## Diagnosis Checklist

1. **Determine if the issue is systemic or individual.** Use the Fleet Health dashboard Per-Device Drill-down table to see which devices are reporting errors. If errors span multiple device types and geographies, the issue is likely backend-side.
2. **Check for recent deployments.** Review CI/CD history and deployment logs. A bad firmware OTA push, a backend schema change, or a configuration update could cause fleet-wide errors.
   ```bash
   git log --oneline --since="2 hours ago"
   ```
3. **Examine error types.** Query Prometheus for the breakdown of error types:
   ```promql
   sum by (error_type) (rate(metaforge_telemetry_ingestion_errors_total[5m]))
   ```
   - `malformed` errors suggest a firmware or encoding issue.
   - `write_failure` errors suggest a TSDB or backend issue.
4. **Check the TSDB health.** If errors are `write_failure`, the time-series database may be overloaded or down. Check its container status and logs.
5. **Check the MQTT broker.** Verify the broker is healthy and not dropping or corrupting messages (see DeviceTelemetryStopped runbook for broker diagnostics).
6. **Check for environmental factors.** If devices are geographically clustered, consider network outages, power grid issues, or weather events. Cross-reference with device location metadata.
7. **Inspect sample error payloads.** Pull recent error messages from the Dead Letter Queue or error logs to understand the specific failure mode.

## Resolution Procedures

- **Roll back recent deployment**: If a firmware or backend deployment preceded the anomaly, roll it back immediately:
  ```bash
  # Backend rollback
  docker-compose up -d --force-recreate <service> --image <previous_tag>

  # Firmware OTA rollback (if supported)
  forge fleet rollback --firmware-version <previous_version>
  ```
- **Fix the ingestion pipeline**: If the error is `write_failure`, resolve the TSDB issue first (restart, scale, or clear disk space), then verify ingestion resumes.
- **Fix message schema**: If the error is `malformed`, identify the schema mismatch. Update the ingestion service to handle both old and new formats, then deploy the fix.
- **Throttle affected devices**: If the error flood is overwhelming the backend, apply rate limiting on the MQTT broker or ingestion service to reduce pressure while investigating.
- **Communicate with stakeholders**: For fleet-wide issues, send an initial incident notification to the product team and affected customers with estimated time to resolution.

## Escalation Path

1. **On-call platform engineer** -- first responder, follows this runbook.
2. **IoT / firmware team** -- escalate if the issue is firmware-related (bad OTA push, encoding change).
3. **Data infrastructure team** -- escalate if the issue is TSDB or pipeline throughput.
4. **Engineering manager + product manager** -- notify immediately for fleet-wide anomalies, as these may require customer communication.
5. **VP of Engineering** -- escalate if the anomaly affects more than 50% of the fleet or persists for more than 1 hour.
