#!/usr/bin/env bash
# Nightly eval driver (MET-574) — run on a box that can reach the gateway
# (GitHub-hosted runners can't reach fidel-dev, so this is the on-box half
# of scheduled evals; wire it to cron or a systemd timer, e.g.:
#
#   0 2 * * *  cd /home/claude/MetaForge && evals/nightly.sh >> ~/eval-nightly.log 2>&1
#
# Behavior: runs both suites into a timestamped evals/reports/<ts>/ dir,
# judges work products (best-effort; set JUDGE_BACKEND=claude-cli to judge
# via the Claude Code CLI login instead of ANTHROPIC_API_KEY), then diffs
# against the previous nightly with trend.py --strict. Exit code is non-zero
# on suite failure or regression — the alerting hook.
set -uo pipefail

GATEWAY="${FORGE_QA_GATEWAY:-http://fidel-dev:8000}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/reports/$TS"
mkdir -p "$OUT"
status=0

# MET-574: route the nightly outcome into the observability stack. When
# LOKI_PUSH_URL is set (e.g. http://<loki-host>:3100/loki/api/v1/push), push
# one structured log line per run under service_name="metaforge-evals";
# level="error" on regression/failure feeds the version-controlled
# NightlyEvalRegression rule in observability/alerting/loki-rules.yaml.
# Best-effort by design — alerting must never change the nightly's exit code.
notify_loki() { # notify_loki <level> <message>
  [ -z "${LOKI_PUSH_URL:-}" ] && return 0
  local payload
  payload="$(python3 - "$1" "$2" <<'PY'
import json, sys, time
level, message = sys.argv[1], sys.argv[2]
print(json.dumps({"streams": [{
    "stream": {"service_name": "metaforge-evals", "job": "nightly", "level": level},
    "values": [[str(time.time_ns()), message]],
}]}))
PY
)" || return 0
  curl -s -m 10 -H 'Content-Type: application/json' \
    -X POST "$LOKI_PUSH_URL" -d "$payload" >/dev/null || true
}

echo "=== nightly evals $TS → $OUT (gateway: $GATEWAY) ==="

python3 "$ROOT/run_scenarios.py" --gateway "$GATEWAY" \
  --out "$OUT/report_runs.json" || status=1

python3 "$ROOT/run_chat_scenarios.py" --gateway "$GATEWAY" --strict \
  --out "$OUT/report_chat.json" || status=1

# Judge is advisory and best-effort — never fails the nightly.
for report in "$OUT/report_runs.json" "$OUT/report_chat.json"; do
  [ -f "$report" ] && python3 "$ROOT/judge.py" --report "$report" --gateway "$GATEWAY" \
    ${JUDGE_BACKEND:+--backend "$JUDGE_BACKEND"} || true
done

# Diff against the previous nightly, if one exists.
PREV="$(ls -d "$ROOT"/reports/*/ 2>/dev/null | sort | tail -n 2 | head -n 1)"
if [ -n "$PREV" ] && [ "${PREV%/}" != "$OUT" ]; then
  echo "=== trend vs $(basename "$PREV") ==="
  for f in report_runs.json report_chat.json; do
    if [ -f "$PREV/$f" ] && [ -f "$OUT/$f" ]; then
      python3 "$ROOT/trend.py" diff --before "$PREV/$f" --after "$OUT/$f" --strict || status=1
    fi
  done
fi

echo "=== nightly evals done (status=$status) ==="
if [ "$status" -ne 0 ]; then
  notify_loki error "nightly evals FAILED (regression or suite failure) ts=$TS reports=$OUT gateway=$GATEWAY"
else
  notify_loki info "nightly evals passed ts=$TS reports=$OUT gateway=$GATEWAY"
fi
exit $status
