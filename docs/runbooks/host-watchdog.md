# Runbook: host watchdog

The compose stack runs a `watchdog` service (`python -m observability.watchdog`,
from the gateway image). Docker restarts a container that exits, but never one
that keeps running in a bad state; the watchdog covers that gap.

## What it does

Every 30 seconds it looks at the containers of its own compose project and:

| Condition | Action |
|---|---|
| A container Docker reports `unhealthy` for 3 checks in a row | Restarts it |
| The gateway's `/health` says the twin runs in memory although Neo4j is configured, for 2 checks | Restarts the gateway |
| Host `MemAvailable` below 512 MB | Logs `watchdog_memory_low`. Kills nothing |

Restarts are rate limited per service: at most one every 10 minutes and 3 an
hour. A container that keeps failing is reported (`watchdog_restart_suppressed`
and the `WatchdogRestartBlocked` alert) rather than restarted in a loop.

Containers that are still starting, or have no health check, are left alone.
The watchdog never restarts itself.

## Alerts

All four are in the `metaforge_host` group of `observability/alerting/rules.yaml`.

| Alert | Fires when | What to do |
|---|---|---|
| `TwinRunningInMemory` (critical) | The gateway has served an in-memory twin for 2 minutes | Check Neo4j: `docker compose ps neo4j`, `docker compose logs --tail 50 neo4j`. Once it is healthy, `docker compose restart gateway`. Then confirm `curl -s localhost:8000/health` shows `twin_backend` as `neo4j (persistent)` |
| `HostMemoryLow` (warning) | Under 512 MB available for 2 minutes | `free -m`, then `docker stats --no-stream` to find the largest containers. Stop a heavy job or a service you do not need. Do not restart a large dev server while memory is low: rebuilding its bundles can take the host offline |
| `WatchdogRestartBlocked` (warning) | A restart was suppressed or failed in the last 30 minutes | Read the service's logs: `docker compose logs --tail 100 <service>`. It is failing after restarts, so a restart will not fix it |
| `WatchdogSilent` (warning) | No watchdog check recorded for 15 minutes | `docker compose ps watchdog` and `docker compose logs --tail 50 watchdog` |

## Logs

The watchdog logs as `metaforge-watchdog`. Its events are
`watchdog_started`, `watchdog_restarted`, `watchdog_restart_suppressed`,
`watchdog_restart_failed`, `watchdog_memory_low`, `watchdog_memory_recovered`
and `watchdog_check_failed`:

```bash
docker compose logs --tail 100 watchdog
```

## Settings

Set these in `.env`; the compose file passes the first three through.

| Variable | Default | Meaning |
|---|---|---|
| `METAFORGE_WATCHDOG_INTERVAL` | `30` | Seconds between checks (minimum 5) |
| `METAFORGE_WATCHDOG_MEMORY_WARN_MB` | `512` | Warn below this much available memory |
| `METAFORGE_WATCHDOG_MAX_RESTARTS_PER_HOUR` | `3` | Restarts of one service allowed per hour |
| `METAFORGE_WATCHDOG_UNHEALTHY_CHECKS` | `3` | Unhealthy checks in a row before a restart |
| `METAFORGE_WATCHDOG_TWIN_CHECKS` | `2` | In-memory twin checks in a row before a gateway restart |
| `METAFORGE_WATCHDOG_COOLDOWN` | `600` | Seconds between restarts of one service |

To turn it off, `docker compose stop watchdog`.

## Security

The watchdog mounts `/var/run/docker.sock`, which is root-equivalent on the
host. It only lists, inspects and restarts containers labelled with its own
compose project, but anything able to run code inside its container could do
more. Leave it stopped on a host where that is not acceptable.

## Limits

- It does not act on memory. Choosing what to stop is a person's decision.
- A gateway run with `--reload` (the dev override) stays up after a failed
  start and reports unhealthy instead of exiting. The watchdog restarts it
  after 3 unhealthy checks, which is the case Docker's restart policy misses.
- It watches one host. A host that is down entirely shows as `WatchdogSilent`
  only if Prometheus runs somewhere else.
