# Runbook: DeviceTelemetryStopped

## Alert Description

The **DeviceTelemetryStopped** alert fires when no MQTT messages have been received from any device in the fleet for 5 minutes. This indicates a systemic telemetry pipeline failure -- either the MQTT broker is down, all devices have lost connectivity, or the ingestion pipeline is broken.

**Alert rule**: `absent_over_time(metaforge_mqtt_messages_received_total{device_id=~".+"}[5m])` for 5 minutes.

## Severity

**Critical** -- pages on-call engineer immediately via PagerDuty.

## Dashboard Links

- [Fleet Health](https://grafana.metaforge.dev/d/metaforge-fleet-health) -- Device Count by Status, MQTT Connection Health
- [System Overview](https://grafana.metaforge.dev/d/metaforge-system-overview) -- overall platform health

## Diagnosis Checklist

1. **Check the MQTT broker.** Verify the MQTT broker container is running: `docker ps --filter name=mosquitto` (or `emqx`/`hivemq` depending on deployment). Check its logs: `docker logs --tail 200 mosquitto`.
2. **Test MQTT connectivity.** Subscribe to the wildcard topic from within the Docker network to confirm the broker is accepting connections:
   ```bash
   docker exec mosquitto mosquitto_sub -h localhost -t '#' -C 1 -W 5
   ```
   If this times out, the broker is not delivering messages.
3. **Check the telemetry ingestion service.** Verify the service that subscribes to MQTT and writes to the TSDB is running: `docker ps --filter name=telemetry-ingest`. Inspect its logs for errors.
4. **Verify device connectivity.** If only specific devices are affected, check the device-side logs or use the MQTT broker's client status endpoint. For Mosquitto: check `/var/log/mosquitto/mosquitto.log` inside the container.
5. **Check network connectivity.** Verify that the network path between devices and the MQTT broker is open. Check firewalls, VPN tunnels, and DNS resolution.
6. **Inspect Prometheus scraping.** If the metric itself is absent (not zero), verify that Prometheus is successfully scraping the telemetry exporter: check Prometheus targets page at `http://prometheus:9090/targets`.
7. **Check for recent configuration changes.** Review recent changes to MQTT ACLs, topic filters, or TLS certificates that may have blocked device connections.

## Resolution Procedures

- **Restart the MQTT broker**: `docker restart mosquitto`. Monitor the Fleet Health dashboard for devices reconnecting.
- **Restart the telemetry ingestion service**: `docker restart telemetry-ingest`. Verify consumption resumes in the dashboard.
- **Fix MQTT ACL / authentication**: If devices are being rejected, check the MQTT broker's ACL configuration and password file. Update and reload: `docker exec mosquitto mosquitto_passwd -U /mosquitto/config/passwd`.
- **Renew TLS certificates**: If TLS certificate expiry is blocking connections, renew the certificates and restart the broker.
- **Restore device connectivity**: If the issue is on the device side (firmware crash, network outage), coordinate with the field engineering team to power-cycle or re-provision affected devices.
- **Scale the ingestion pipeline**: If the pipeline was overwhelmed, add more consumer instances or increase resource limits.

## Escalation Path

1. **On-call platform engineer** -- first responder, follows this runbook.
2. **IoT / device engineering team** -- escalate if the issue is device-side (firmware, connectivity).
3. **Network / infrastructure team** -- escalate if the issue is network-level (firewall, DNS, VPN).
4. **Engineering manager** -- notify if fleet telemetry is down for more than 15 minutes for stakeholder communication.
