# FleetAnomalyPattern Runbook

## Alert Description

- **Severity**: Critical
- **Expression**: `(sum(rate(metaforge_telemetry_ingestion_total{status="error"}[5m])) / sum(rate(metaforge_telemetry_ingestion_total[5m]))) > 0.10`
- **Duration**: Fires after 5 minutes
- **Dashboard**: [Fleet Health](../dashboards/fleet-health.json)

More than 10% of fleet devices are reporting errors simultaneously. This pattern indicates a systemic issue -- such as a bad firmware update, infrastructure failure, or environmental event -- rather than isolated device failures.

## Symptoms

- Fleet Health dashboard shows a spike in device error rates across multiple devices
- Telemetry ingestion error rate exceeds 10% of total ingestion volume
- Multiple devices may report similar error codes or failure modes
- Anomaly detection flags correlated failures across the fleet
- Potential increase in DeviceOffline warnings for individual devices

## Diagnosis Steps

1. **Determine the scope and pattern**
   ```bash
   # Check which devices are reporting errors
   # Query Prometheus for error breakdown by device
   curl -s "http://<prometheus-host>:9090/api/v1/query?query=rate(metaforge_telemetry_ingestion_total{status='error'}[5m])" | jq '.data.result[] | {device: .metric.device_id, rate: .value[1]}'
   ```

2. **Check for recent firmware deployments**
   ```bash
   # Check deployment logs for recent OTA updates
   docker logs firmware-deployer --since 1h 2>&1 | grep -i "deploy\|update\|rollout"
   # Check if error onset correlates with a deployment timestamp
   ```

3. **Check telemetry ingestion service health**
   ```bash
   docker ps --filter name=telemetry-ingest
   docker logs telemetry-ingest --tail 200 --since 10m
   # Look for parsing errors, schema mismatches, or TSDB write failures
   ```

4. **Check TSDB write health**
   ```bash
   # If using TimescaleDB/InfluxDB, check for write errors
   docker logs <tsdb-container> --tail 100 --since 10m
   # Check disk space on TSDB host
   docker exec <tsdb-container> df -h /data
   ```

5. **Analyze error patterns in the telemetry data**
   ```bash
   # Check if errors are concentrated in specific device types, firmware versions, or regions
   docker logs telemetry-ingest 2>&1 | grep "error" | tail -50
   ```

6. **Check for infrastructure-level issues**
   ```bash
   # MQTT broker health
   docker ps --filter name=mqtt
   # Network connectivity
   ping <device-gateway-host>
   # DNS resolution
   dig <mqtt-host>
   ```

7. **Check for environmental or external factors**
   - Review any recent infrastructure changes (cloud provider maintenance, DNS changes)
   - Check if the pattern correlates with a geographic region or time zone
   - Review recent configuration changes pushed to devices

## Resolution Procedures

1. **If caused by a bad firmware update**, initiate rollback:
   ```bash
   # Halt the current rollout
   # Use your firmware deployment tool to rollback to the previous version
   # Example:
   forge fleet rollback --to-version <previous-version>
   ```

2. **If TSDB ingestion is failing** (schema mismatch or disk full):
   ```bash
   # Free disk space if needed
   docker exec <tsdb-container> df -h /data
   # Restart ingestion service after fixing the root cause
   docker restart telemetry-ingest
   ```

3. **If telemetry parsing errors** (devices sending malformed data):
   ```bash
   # Check for schema version mismatch
   docker logs telemetry-ingest 2>&1 | grep -i "schema\|parse\|invalid" | tail -20
   # Update the ingestion service to handle the new schema, or
   # Push a configuration update to affected devices
   ```

4. **If network/infrastructure issue**, restore connectivity:
   - Work with the infrastructure team to resolve network partitions
   - Restore DNS if resolution is failing
   - Check and restore cloud provider services if applicable

5. **If the issue is environmental** (power outage, weather event):
   - Document the affected region and expected recovery time
   - Monitor for devices coming back online as conditions improve
   - No platform-side fix needed; focus on monitoring recovery

6. **Monitor recovery**
   ```bash
   # Watch error rate drop back below threshold
   watch -n 10 'curl -s "http://<prometheus-host>:9090/api/v1/query?query=(sum(rate(metaforge_telemetry_ingestion_total{status=%22error%22}[5m]))/sum(rate(metaforge_telemetry_ingestion_total[5m])))*100" | jq ".data.result[0].value[1]"'
   ```

## Escalation Path

| Timeframe | Action |
|-----------|--------|
| 0-5 min | On-call engineer determines scope (how many devices, which types, which regions) |
| 5-15 min | If firmware-related, initiate rollback. If infrastructure-related, escalate to DevOps |
| 15-30 min | Escalate to platform engineering lead and IoT/device team lead |
| 30+ min | Page engineering manager; if customer-facing, notify customer success team |

## Communication Template

**Internal (Slack / incident channel)**:

```
[INCIDENT] FleetAnomalyPattern - Fleet-Wide Device Error Spike

Status: Investigating / Mitigated / Resolved
Impact: >10% of fleet devices are reporting errors. Affected device count: <N>. Potential causes under investigation: <firmware update / infrastructure / environmental>.
Error rate: <X>%
Affected regions/types: <details if known>
Start time: <HH:MM UTC>
Current action: <what you are doing>
Next update: <HH:MM UTC or "in 15 minutes">
```
