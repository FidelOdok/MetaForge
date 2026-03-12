# Neo4jUnreachable Runbook

## Alert Description

- **Severity**: Critical
- **Expression**: `metaforge_neo4j_active_connections == 0`
- **Duration**: Fires after 1 minute
- **Dashboard**: [System Overview](../dashboards/system-overview.json), [Data Stores](../dashboards/data-stores.json)

Neo4j is the graph database backing the Digital Twin -- the single source of design truth. When there are zero active connections, all artifact graph operations (reads, writes, traversals, constraint checks) will fail. This blocks every agent and orchestrator workflow that interacts with design state.

## Symptoms

- All Digital Twin API calls return errors (connection refused / timeout)
- Agent workflows fail at the "read from Twin" or "propose change to Twin" steps
- The orchestrator cannot resolve inter-agent dependencies
- Constraint engine validation fails with database connection errors
- Grafana Data Stores dashboard shows zero active Neo4j connections

## Diagnosis Steps

1. **Check Neo4j process status**
   ```bash
   docker ps --filter name=neo4j
   docker logs neo4j --tail 100 --since 10m
   ```

2. **Check Neo4j connectivity from the gateway/twin_core host**
   ```bash
   # Bolt protocol (default port 7687)
   nc -zv <neo4j-host> 7687

   # HTTP API (default port 7474)
   curl -s http://<neo4j-host>:7474/
   ```

3. **Check Neo4j health endpoint**
   ```bash
   curl -s http://<neo4j-host>:7474/db/neo4j/cluster/available
   ```

4. **Check disk space on Neo4j host**
   ```bash
   # Neo4j will refuse writes and may crash if disk is full
   docker exec neo4j df -h /data
   ```

5. **Check Neo4j memory usage**
   ```bash
   docker stats neo4j --no-stream
   # Check for heap/page cache pressure in logs
   docker logs neo4j 2>&1 | grep -i "heap\|memory\|gc\|OutOfMemory" | tail -20
   ```

6. **Check for long-running queries or locks**
   ```bash
   # Connect via cypher-shell if possible
   docker exec neo4j cypher-shell -u neo4j -p <password> \
     "CALL dbms.listQueries() YIELD queryId, query, elapsedTimeMillis WHERE elapsedTimeMillis > 30000 RETURN *"
   ```

7. **Check connection pool exhaustion in twin_core**
   ```bash
   # Look for connection pool errors in the application
   docker logs metaforge-gateway 2>&1 | grep -i "neo4j\|connection\|pool\|refused" | tail -20
   ```

## Resolution Procedures

1. **Restart Neo4j**
   ```bash
   docker restart neo4j
   # Wait for it to become available (typically 15-30 seconds)
   sleep 30
   curl -s http://<neo4j-host>:7474/
   ```

2. **If disk full**, free space before restarting:
   ```bash
   # Clear transaction logs if safe
   docker exec neo4j ls -lh /data/transactions/
   # Remove old backups
   docker exec neo4j ls -lh /data/backups/
   # Restart after freeing space
   docker restart neo4j
   ```

3. **If OOM-killed**, increase memory allocation:
   ```bash
   # Check OOM events
   dmesg | grep -i oom | tail -10
   # Update Neo4j Docker memory limit and heap settings
   # In docker-compose.yml or neo4j.conf:
   #   NEO4J_server_memory_heap_max__size=2G
   #   NEO4J_server_memory_pagecache_size=1G
   docker-compose up -d neo4j
   ```

4. **If long-running queries are blocking**, kill them:
   ```bash
   docker exec neo4j cypher-shell -u neo4j -p <password> \
     "CALL dbms.listQueries() YIELD queryId, elapsedTimeMillis WHERE elapsedTimeMillis > 60000 RETURN queryId" \
     | while read qid; do
       docker exec neo4j cypher-shell -u neo4j -p <password> "CALL dbms.killQuery('$qid')"
     done
   ```

5. **If connection pool exhaustion**, restart the consuming application:
   ```bash
   docker restart metaforge-gateway
   ```

6. **Verify recovery**
   ```bash
   curl -s http://<neo4j-host>:7474/
   # Check that active connections metric returns to normal
   curl -s http://<gateway-host>:8000/health
   ```

## Escalation Path

| Timeframe | Action |
|-----------|--------|
| 0-5 min | On-call engineer checks Neo4j process and connectivity |
| 5-15 min | If restart does not resolve, check disk/memory and investigate root cause |
| 15-30 min | Escalate to platform engineering lead; assess data integrity |
| 30+ min | Page engineering manager; evaluate whether a database restore from backup is needed |

## Communication Template

**Internal (Slack / incident channel)**:

```
[INCIDENT] Neo4jUnreachable - Digital Twin Database Down

Status: Investigating / Mitigated / Resolved
Impact: All Digital Twin operations are blocked. Agent workflows, constraint validation, and design state reads/writes are failing.
Start time: <HH:MM UTC>
Current action: <what you are doing>
Next update: <HH:MM UTC or "in 15 minutes">
```
