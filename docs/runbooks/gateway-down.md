# Runbook: GatewayDown

## Alert Description

The **GatewayDown** alert fires when the MetaForge API Gateway becomes unreachable. The gateway is the single HTTP/WebSocket entry point for all platform traffic, so this alert represents a full service outage for external consumers.

**Alert rule**: `up{job="metaforge-gateway"} == 0` for 1 minute.

## Severity

**Critical** -- pages on-call engineer immediately via PagerDuty.

## Dashboard Links

- [System Overview](https://grafana.metaforge.dev/d/metaforge-system-overview) -- Gateway Status panel
- [SLO Overview](https://grafana.metaforge.dev/d/metaforge-slo-overview) -- Availability SLI

## Diagnosis Checklist

1. **Check container health.** Run `docker ps --filter name=metaforge-gateway` and verify the container is running. If it exited, inspect exit code with `docker inspect --format='{{.State.ExitCode}}' metaforge-gateway`.
2. **Inspect container logs.** Run `docker logs --tail 200 metaforge-gateway` and look for fatal errors, out-of-memory kills, or unhandled exceptions.
3. **Verify port binding.** Confirm the gateway port (default 8000) is listening: `ss -tlnp | grep 8000`. If nothing is bound, the process may have crashed before startup completed.
4. **Check host resources.** Run `free -h` and `df -h` to verify the host has sufficient memory and disk. OOM-killed containers will show in `dmesg | grep -i oom`.
5. **Test upstream dependencies.** The gateway requires Neo4j and Kafka at startup. Verify both are healthy:
   - Neo4j: `curl -s http://neo4j:7474/db/neo4j/cluster/available`
   - Kafka: `docker exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092`
6. **Check DNS / service discovery.** If running behind a load balancer or service mesh, verify DNS resolution and health-check endpoints.
7. **Review recent deployments.** Check if a deployment occurred shortly before the alert: `git log --oneline -5` and CI/CD pipeline history.

## Resolution Procedures

- **Restart the container**: `docker restart metaforge-gateway`. Monitor logs for successful startup.
- **Rollback if recently deployed**: If a deployment preceded the failure, roll back to the previous image tag: `docker-compose up -d --force-recreate metaforge-gateway` with the prior image.
- **Scale horizontally**: If the crash is due to traffic overload, scale to additional replicas and place behind a load balancer.
- **Fix configuration**: If logs show a configuration error (missing env vars, bad secrets), correct the `.env` or secrets mount and restart.
- **Address dependency failure**: If Neo4j or Kafka is down, resolve those first (see their respective runbooks). The gateway will reconnect automatically once dependencies recover.

## Escalation Path

1. **On-call platform engineer** -- first responder, follows this runbook.
2. **Platform team lead** -- escalate after 15 minutes if not resolved.
3. **Infrastructure / SRE team** -- escalate if the issue is host-level (OOM, disk, network).
4. **Engineering manager** -- notify if outage exceeds 30 minutes for stakeholder communication.
