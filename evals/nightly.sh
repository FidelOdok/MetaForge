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
exit $status
