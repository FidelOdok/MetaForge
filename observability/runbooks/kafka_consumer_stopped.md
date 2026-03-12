# KafkaConsumerStopped Runbook

## Alert Description

- **Severity**: Critical
- **Expression**: `rate(metaforge_kafka_messages_consumed_total[5m]) == 0 and metaforge_kafka_consumer_lag > 0`
- **Duration**: Fires after 5 minutes
- **Dashboard**: [System Overview](../dashboards/system-overview.json), [Data Stores](../dashboards/data-stores.json)

A Kafka consumer group has stopped consuming messages while lag continues to grow. This means design change events, agent task dispatches, or telemetry data are not being processed, causing a growing backlog.

## Symptoms

- Design change events are not propagated to agents
- Agent tasks queue up but are never executed
- The orchestrator stops receiving event-driven triggers
- Kafka consumer lag metric (`metaforge_kafka_consumer_lag`) is increasing
- Consumption rate (`metaforge_kafka_messages_consumed_total`) is flat at zero

## Diagnosis Steps

1. **Identify the affected consumer group**
   - Check the alert labels for `consumer_group` to identify which group has stopped
   - Cross-reference with the Data Stores dashboard for lag trends

2. **Check the consumer application process**
   ```bash
   # Identify which service owns this consumer group
   # Common groups: orchestrator-events, agent-dispatcher, telemetry-ingest
   docker ps --filter name=<service-name>
   docker logs <service-name> --tail 200 --since 10m
   ```

3. **Check Kafka broker health**
   ```bash
   # List consumer groups and their status
   kafka-consumer-groups.sh --bootstrap-server <kafka-host>:9092 --describe --group <consumer_group>

   # Check broker status
   kafka-broker-api-versions.sh --bootstrap-server <kafka-host>:9092
   ```

4. **Check for consumer rebalancing issues**
   ```bash
   # Look for rebalance events in consumer logs
   docker logs <service-name> 2>&1 | grep -i "rebalance\|partition\|revoked\|assigned" | tail -20
   ```

5. **Check topic health**
   ```bash
   # Describe the topic partitions
   kafka-topics.sh --bootstrap-server <kafka-host>:9092 --describe --topic <topic-name>

   # Check for under-replicated partitions
   kafka-topics.sh --bootstrap-server <kafka-host>:9092 --describe --under-replicated-partitions
   ```

6. **Check for serialization/deserialization errors**
   ```bash
   # Consumer may be crashing on malformed messages
   docker logs <service-name> 2>&1 | grep -i "deseriali\|serde\|parse\|schema" | tail -20
   ```

7. **Check system resources on the consumer host**
   ```bash
   free -h
   df -h
   top -bn1 | head -20
   ```

## Resolution Procedures

1. **Restart the consumer service**
   ```bash
   docker restart <service-name>
   # Monitor logs for successful startup and consumption resumption
   docker logs -f <service-name>
   ```

2. **If consumer crashes repeatedly on a poison message**, skip the problematic offset:
   ```bash
   # Identify current offset
   kafka-consumer-groups.sh --bootstrap-server <kafka-host>:9092 \
     --describe --group <consumer_group>

   # Reset to skip past the bad message (use with caution)
   kafka-consumer-groups.sh --bootstrap-server <kafka-host>:9092 \
     --group <consumer_group> --topic <topic>:<partition> \
     --reset-offsets --shift-by 1 --execute
   ```

3. **If Kafka broker is unhealthy**, restart the broker:
   ```bash
   docker restart kafka
   # Wait for broker to rejoin the cluster
   ```

4. **If consumer group is stuck in rebalance**, force a clean rejoin:
   ```bash
   # Stop all consumers in the group
   docker stop <service-name>
   # Wait for session timeout (default 10s)
   sleep 15
   # Restart
   docker start <service-name>
   ```

5. **Monitor lag recovery**
   ```bash
   watch -n 5 "kafka-consumer-groups.sh --bootstrap-server <kafka-host>:9092 \
     --describe --group <consumer_group>"
   ```

## Escalation Path

| Timeframe | Action |
|-----------|--------|
| 0-5 min | On-call engineer identifies affected consumer group and checks process health |
| 5-15 min | If consumer restart does not resolve, investigate Kafka broker health |
| 15-30 min | Escalate to platform engineering lead if broker-level issues found |
| 30+ min | Page engineering manager; assess data loss risk from growing lag |

## Communication Template

**Internal (Slack / incident channel)**:

```
[INCIDENT] KafkaConsumerStopped - Consumer Group "<consumer_group>" Not Processing

Status: Investigating / Mitigated / Resolved
Impact: Event processing is stalled for <consumer_group>. Affected functionality: <agent dispatch / design events / telemetry ingestion>.
Current lag: <N> messages
Start time: <HH:MM UTC>
Current action: <what you are doing>
Next update: <HH:MM UTC or "in 15 minutes">
```
