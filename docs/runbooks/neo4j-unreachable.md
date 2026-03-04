# Runbook: Neo4jUnreachable

## Alert Description

The **Neo4jUnreachable** alert fires when the MetaForge platform has zero active connections to the Neo4j graph database. Neo4j is the Digital Twin's backing store -- all artifact nodes, relationships, and constraints live here. Without Neo4j, agents cannot read or propose changes to the design graph.

**Alert rule**: `metaforge_neo4j_active_connections == 0` for 1 minute.

## Severity

**Critical** -- pages on-call engineer immediately via PagerDuty.

## Dashboard Links

- [Data Stores](https://grafana.metaforge.dev/d/metaforge-data-stores) -- Neo4j section
- [System Overview](https://grafana.metaforge.dev/d/metaforge-system-overview) -- Gateway Status (may show degraded)

## Diagnosis Checklist

1. **Check Neo4j container status.** Run `docker ps --filter name=neo4j` and verify the container is running. If it exited, check the exit code: `docker inspect --format='{{.State.ExitCode}}' neo4j`.
2. **Inspect Neo4j logs.** Run `docker logs --tail 200 neo4j` and look for `OutOfMemoryError`, disk space errors, or lock contention.
3. **Test direct connectivity.** Try reaching Neo4j from the gateway host:
   - HTTP API: `curl -s http://neo4j:7474/db/neo4j/cluster/available`
   - Bolt protocol: `docker exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "RETURN 1"`
4. **Check memory and disk usage.** Neo4j is memory-intensive.
   - Container memory: `docker stats neo4j --no-stream`
   - Disk: `docker exec neo4j df -h /data`
   - JVM heap: check `NEO4J_dbms_memory_heap_max__size` in the Neo4j configuration.
5. **Check connection pool exhaustion.** If the gateway opened too many connections and did not return them, the pool may be exhausted. Check the gateway logs for connection pool timeout errors.
6. **Verify cluster health (if clustered).** For a causal cluster: `docker exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "CALL dbms.cluster.overview()"`.
7. **Check network connectivity.** Verify the Docker network bridge is intact and the neo4j service is resolvable from other containers.

## Resolution Procedures

- **Restart Neo4j**: `docker restart neo4j`. Wait 30-60 seconds for the database to become available, then verify with `cypher-shell`.
- **Increase heap memory**: If logs show `OutOfMemoryError`, increase `NEO4J_dbms_memory_heap_max__size` in `docker-compose.yml` (e.g., from `512m` to `1g`) and recreate the container.
- **Reclaim disk space**: If the data volume is full, prune old transaction logs: `docker exec neo4j rm -f /data/transactions/neo4j/neostore.transaction.db.*` (only for non-clustered setups; check backup status first).
- **Restart gateway to reset connection pool**: If pool exhaustion is suspected, restart the gateway after Neo4j is confirmed healthy: `docker restart metaforge-gateway`.
- **Failover to replica**: In a clustered setup, verify a read replica can be promoted. Update the connection URI in the gateway configuration to point to the new leader.
- **Restore from backup**: If data corruption is suspected, restore from the latest backup: `neo4j-admin load --from=/backups/latest.dump --database=neo4j --force`.

## Escalation Path

1. **On-call platform engineer** -- first responder, follows this runbook.
2. **Platform team lead** -- escalate after 10 minutes if Neo4j cannot be restarted.
3. **Database / infrastructure team** -- escalate if the issue involves data corruption, cluster split-brain, or hardware failure.
4. **Engineering manager** -- notify if the Digital Twin is unavailable for more than 20 minutes.
