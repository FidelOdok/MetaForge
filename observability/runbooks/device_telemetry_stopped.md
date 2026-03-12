# DeviceTelemetryStopped Runbook

## Alert Description

- **Severity**: Critical
- **Expression**: `absent_over_time(metaforge_mqtt_messages_received_total{device_id=~".+"}[5m])`
- **Duration**: Fires after 5 minutes
- **Dashboard**: [Fleet Health](../dashboards/fleet-health.json)

No MQTT telemetry messages have been received from any device in the last 5 minutes. This indicates a fleet-wide communication failure rather than a single device issue (for individual devices, see the DeviceOffline warning alert).

## Symptoms

- Fleet Health dashboard shows zero incoming MQTT messages across all devices
- Time-series database (TSDB) ingestion rate drops to zero
- No new telemetry data points appear for any device
- Device status indicators in dashboards go stale
- Anomaly detection has no fresh data to evaluate

## Diagnosis Steps

1. **Check the MQTT broker status**
   ```bash
   docker ps --filter name=mosquitto
   # or for EMQX/HiveMQ:
   docker ps --filter name=mqtt
   docker logs <mqtt-broker> --tail 100 --since 10m
   ```

2. **Check MQTT broker connectivity**
   ```bash
   # Test MQTT connection (requires mosquitto-clients)
   mosquitto_sub -h <mqtt-host> -p 1883 -t "metaforge/telemetry/#" -C 1 -W 10
   # If TLS:
   mosquitto_sub -h <mqtt-host> -p 8883 --cafile /path/to/ca.crt -t "metaforge/telemetry/#" -C 1 -W 10
   ```

3. **Check the MetaForge telemetry ingestion service**
   ```bash
   docker ps --filter name=telemetry-ingest
   docker logs telemetry-ingest --tail 100 --since 10m
   ```

4. **Check MQTT broker metrics**
   ```bash
   # Check connected client count
   mosquitto_sub -h <mqtt-host> -t "\$SYS/broker/clients/connected" -C 1 -W 5
   # Check message rate
   mosquitto_sub -h <mqtt-host> -t "\$SYS/broker/messages/received" -C 1 -W 5
   ```

5. **Check network connectivity to device fleet**
   ```bash
   # If devices connect via a VPN or gateway
   ping <device-gateway-host>
   # Check for firewall/security group changes
   iptables -L -n | grep 1883
   ```

6. **Check TLS certificate expiry** (if using encrypted MQTT)
   ```bash
   openssl s_client -connect <mqtt-host>:8883 2>/dev/null | openssl x509 -noout -dates
   ```

7. **Distinguish between broker-side and device-side issues**
   ```bash
   # Publish a test message and check if the ingestion service receives it
   mosquitto_pub -h <mqtt-host> -t "metaforge/telemetry/test-device" -m '{"test": true}'
   # Check ingestion service logs for the test message
   docker logs telemetry-ingest --tail 10
   ```

## Resolution Procedures

1. **If MQTT broker is down**, restart it:
   ```bash
   docker restart <mqtt-broker>
   # Verify broker accepts connections
   mosquitto_sub -h <mqtt-host> -t "\$SYS/broker/uptime" -C 1 -W 10
   ```

2. **If the telemetry ingestion service is down**, restart it:
   ```bash
   docker restart telemetry-ingest
   # Monitor for incoming messages
   docker logs -f telemetry-ingest
   ```

3. **If TLS certificates have expired**, renew them:
   ```bash
   # Renew certificates using your certificate management process
   # Restart the broker to pick up new certificates
   docker restart <mqtt-broker>
   ```

4. **If devices cannot reach the broker** (network/firewall issue):
   - Check and restore firewall rules for port 1883 (or 8883 for TLS)
   - Verify VPN tunnels or network gateways are operational
   - Contact the network/infrastructure team if changes were made

5. **If the broker is overloaded**, check resource limits:
   ```bash
   docker stats <mqtt-broker> --no-stream
   # Increase memory/CPU if needed, then restart
   ```

6. **Verify recovery**
   ```bash
   # Confirm messages are flowing again
   mosquitto_sub -h <mqtt-host> -t "metaforge/telemetry/#" -C 3 -W 30
   # Check Fleet Health dashboard for resumed ingestion
   ```

## Escalation Path

| Timeframe | Action |
|-----------|--------|
| 0-5 min | On-call engineer checks MQTT broker and telemetry ingestion service |
| 5-15 min | If broker is healthy, investigate network/firewall and device-side connectivity |
| 15-30 min | Escalate to platform engineering lead and IoT/device team |
| 30+ min | Page engineering manager; coordinate with field engineering if physical device access is needed |

## Communication Template

**Internal (Slack / incident channel)**:

```
[INCIDENT] DeviceTelemetryStopped - Fleet Telemetry Blackout

Status: Investigating / Mitigated / Resolved
Impact: No telemetry data is being received from any device. Fleet monitoring, anomaly detection, and device health tracking are blind.
Affected devices: All (fleet-wide)
Start time: <HH:MM UTC>
Current action: <what you are doing>
Next update: <HH:MM UTC or "in 15 minutes">
```
