# GatewayDown Runbook

## Alert Description

- **Severity**: Critical
- **Expression**: `up{job="metaforge-gateway"} == 0`
- **Duration**: Fires after 1 minute
- **Dashboard**: [System Overview](../dashboards/system-overview.json)

The MetaForge Gateway is the HTTP/WebSocket "front door" for all API traffic. When this alert fires, the gateway process is unreachable by Prometheus, meaning all external API requests will fail.

## Symptoms

- All API requests from CLI (`forge` commands) return connection errors
- WebSocket connections from IDE assistants drop
- Grafana dashboard panels sourcing gateway metrics show "No Data"
- Downstream agents and orchestrator lose their entry point (though internal Kafka/Neo4j communication may still function)

## Diagnosis Steps

1. **Check gateway process status**
   ```bash
   systemctl status metaforge-gateway
   # or if running in Docker:
   docker ps --filter name=metaforge-gateway
   docker logs metaforge-gateway --tail 100
   ```

2. **Check if the host is reachable**
   ```bash
   ping <gateway-host>
   curl -s http://<gateway-host>:8000/health
   ```

3. **Check port availability**
   ```bash
   ss -tlnp | grep 8000
   # Verify nothing else has bound to the gateway port
   ```

4. **Check system resources on the gateway host**
   ```bash
   free -h          # Memory
   df -h            # Disk
   top -bn1         # CPU / process list
   ```

5. **Check application logs for crash reason**
   ```bash
   journalctl -u metaforge-gateway --since "10 minutes ago" --no-pager
   # or Docker:
   docker logs metaforge-gateway --since 10m
   ```

6. **Check Prometheus scrape targets**
   - Open Prometheus UI at `/targets`
   - Verify the `metaforge-gateway` job shows the endpoint and last scrape error

7. **Check network / firewall rules**
   ```bash
   iptables -L -n | grep 8000
   # Verify no recent firewall changes blocking Prometheus scraping or client access
   ```

## Resolution Procedures

1. **Restart the gateway process**
   ```bash
   systemctl restart metaforge-gateway
   # or Docker:
   docker restart metaforge-gateway
   ```

2. **If OOM-killed**, increase memory limits:
   ```bash
   # Check OOM events
   dmesg | grep -i oom | tail -20
   # Adjust Docker memory limit or systemd MemoryMax
   ```

3. **If port conflict**, identify and stop the conflicting process:
   ```bash
   lsof -i :8000
   kill <conflicting-pid>
   systemctl restart metaforge-gateway
   ```

4. **If disk full**, free space:
   ```bash
   df -h /
   # Remove old logs, temp files, or Docker images
   docker system prune -f
   journalctl --vacuum-size=500M
   ```

5. **If configuration error**, check recent config changes:
   ```bash
   git -C /opt/metaforge log --oneline -5
   # Revert if a recent change caused the failure
   ```

6. **Verify recovery**
   ```bash
   curl -s http://<gateway-host>:8000/health
   # Confirm Prometheus target is UP again
   ```

## Escalation Path

| Timeframe | Action |
|-----------|--------|
| 0-5 min | On-call engineer investigates using diagnosis steps above |
| 5-15 min | If not resolved, escalate to platform engineering lead |
| 15-30 min | If infrastructure-related (host down, network issue), escalate to infrastructure/DevOps team |
| 30+ min | Page engineering manager; consider failover if available |

## Communication Template

**Internal (Slack / incident channel)**:

```
[INCIDENT] GatewayDown - MetaForge Gateway Unreachable

Status: Investigating / Mitigated / Resolved
Impact: All external API traffic to MetaForge is blocked. CLI commands, IDE integrations, and WebSocket connections are failing.
Start time: <HH:MM UTC>
Current action: <what you are doing>
Next update: <HH:MM UTC or "in 15 minutes">
```
