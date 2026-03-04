# Runbook: KafkaConsumerStopped

## Alert Description

The **KafkaConsumerStopped** alert fires when a Kafka consumer group has zero consumption rate while its consumer lag is non-zero. This means messages are accumulating in the topic but no consumer is processing them, which will cause event processing delays across the platform.

**Alert rule**: `rate(metaforge_kafka_messages_consumed_total[5m]) == 0 and metaforge_kafka_consumer_lag > 0` for 5 minutes.

## Severity

**Critical** -- pages on-call engineer immediately via PagerDuty.

## Dashboard Links

- [System Overview](https://grafana.metaforge.dev/d/metaforge-system-overview) -- Kafka Consumer Lag panel
- [Data Stores](https://grafana.metaforge.dev/d/metaforge-data-stores) -- Kafka metrics section

## Diagnosis Checklist

1. **Identify the affected consumer group.** Check the alert labels for `consumer_group` and `topic` to determine which service has stopped consuming.
2. **Check consumer group status.** Run `docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group <consumer_group> --describe`. Look for members with no assigned partitions or groups in a `Dead` or `Empty` state.
3. **Inspect the consumer application.** Check if the consuming service container is running: `docker ps --filter name=<service>`. If crashed, inspect logs with `docker logs --tail 200 <service>`.
4. **Check the Dead Letter Queue (DLQ).** If a poison message caused the consumer to fail, check the DLQ topic: `docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic <topic>.dlq --from-beginning --max-messages 5`.
5. **Check for rebalance storms.** Examine `metaforge_kafka_rebalance_total` in Prometheus. Frequent rebalances indicate consumers are crashing and rejoining repeatedly.
6. **Verify broker health.** Run `docker exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092` and check that all brokers are available.
7. **Check for consumer offset issues.** If the consumer committed an invalid offset, the group may be stuck. Verify with: `docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group <consumer_group> --describe`.

## Resolution Procedures

- **Restart the consumer service**: `docker restart <consumer_service>`. Monitor consumption rate in Grafana to confirm it resumes.
- **Reset consumer offset** (if stuck on a bad offset): `docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group <consumer_group> --topic <topic> --reset-offsets --to-latest --execute`. Use `--to-earliest` if messages must not be skipped.
- **Skip poison messages**: If a specific message is causing repeated crashes, consume and discard it manually, or move the consumer offset past it.
- **Trigger manual rebalance**: Restart one consumer instance to trigger a partition reassignment.
- **Scale consumers**: If lag is growing because of throughput, add more consumer instances to the group.
- **Fix application bug**: If logs show a repeating error (deserialization failure, schema mismatch), fix the code and redeploy.

## Escalation Path

1. **On-call platform engineer** -- first responder, follows this runbook.
2. **Platform team lead** -- escalate after 15 minutes if consumption has not resumed.
3. **Data infrastructure team** -- escalate if the issue is broker-level (broker down, disk full, replication issues).
4. **Engineering manager** -- notify if event processing is stalled for more than 30 minutes.
