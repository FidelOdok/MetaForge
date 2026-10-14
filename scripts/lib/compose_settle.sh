#!/usr/bin/env bash
# Bring a compose stack the rest of the way up, and say so honestly (FORGE-622).
#
# `docker compose up` returns non-zero when a service it was told to start has
# a `depends_on: service_healthy` dependency that does not report healthy
# inside compose's own wait. The dependency is usually fine moments later --
# the gateway takes a while to register its workflows -- but compose has
# already given up, and anything downstream of it is left in `Created`,
# never started.
#
# Under `set -e` that non-zero exit also ends the calling script on the spot,
# before any health loop it may have. The deploy stops half-done: images
# pulled and verified, some containers running the new build, the dashboard
# dark. Nothing retries, and a tail-piped invocation swallows the exit code,
# so it reads as success.
#
# So: waiting for health is not enough on its own, and neither is asking
# whether the healthy ones are healthy. A container with no healthcheck of its
# own -- the dashboard has none -- has an empty Health field, and a filter that
# skips empty Health skips a container that never started. This waits on both
# facts, gives whatever was left behind a second chance once its dependency is
# actually healthy, and fails loudly naming every service that is still wrong.

# Services that exist but are not running, one "name state" per line.
_compose_not_running() {
  docker compose ps -a --format '{{.Name}} {{.State}}' 2>/dev/null \
    | awk '$2 != "running" { print $1 " " $2 }'
}

# Services with a healthcheck that are not yet passing it.
_compose_not_healthy() {
  docker compose ps -a --format '{{.Name}} {{.Health}}' 2>/dev/null \
    | awk '$2 != "" && $2 != "healthy" { print $1 " " $2 }'
}

_compose_settled() {
  [ -z "$(_compose_not_running)" ] && [ -z "$(_compose_not_healthy)" ]
}

_compose_wait() {
  local attempts="$1" sleep_s="$2" i
  for ((i = 0; i < attempts; i++)); do
    _compose_settled && return 0
    sleep "$sleep_s"
  done
  _compose_settled
}

# compose_settle [attempts] [sleep_seconds]
#
# Returns 0 only when every container is running and every healthcheck passes.
compose_settle() {
  local attempts="${1:-${COMPOSE_SETTLE_ATTEMPTS:-60}}"
  local sleep_s="${2:-${COMPOSE_SETTLE_SLEEP:-5}}"

  if _compose_wait "$attempts" "$sleep_s"; then
    return 0
  fi

  # Still not settled. If the problem is only that something never started,
  # its dependency has probably reported healthy by now -- so ask compose to
  # start what is missing, rather than leaving the stack half-up.
  local stranded
  stranded="$(_compose_not_running)"
  if [ -n "$stranded" ]; then
    echo "not started, retrying: $(echo "$stranded" | awk '{print $1}' | tr '\n' ' ')" >&2
    docker compose up -d --no-build || true
    # A shorter second wait: the slow dependency has already had the first one.
    _compose_wait "$(( attempts / 4 + 1 ))" "$sleep_s" && return 0
  fi

  {
    echo "stack did not settle:"
    _compose_not_running | while read -r name state; do
      echo "  not running: $name ($state)"
    done
    _compose_not_healthy | while read -r name health; do
      echo "  not healthy: $name ($health)"
    done
  } >&2
  return 1
}
