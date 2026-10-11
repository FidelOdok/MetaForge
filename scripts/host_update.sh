#!/usr/bin/env bash
# Update a MetaForge host that runs from published images only (FORGE-593).
#
# A host keeps no source checkout. Its deploy folder holds the compose file,
# the handful of config files the stack mounts, and its own .env (secrets and
# COMPOSE_FILE / COMPOSE_PROFILES); everything that runs comes from GHCR.
#
# Each run:
#   1. fetches the deploy bundle (compose + mounted configs) for a ref from
#      GitHub, replacing the bundle files and nothing else (.env and any
#      host-only override in the folder are left alone);
#   2. pulls every image and verifies the signature of each MetaForge image by
#      digest with cosign, against this repository's CI identity -- any
#      failure stops before anything is restarted;
#   3. starts the stack without building, recreates every container whose
#      image changed, and waits for health.
#
# Usage: scripts/host_update.sh [--dir DIR] [--ref REF] [--no-verify]
#   DIR defaults to $METAFORGE_HOST_DIR or ~/metaforge, REF to main.
set -euo pipefail

DIR="${METAFORGE_HOST_DIR:-$HOME/metaforge}"
REF="main"
VERIFY=1
REPO="${METAFORGE_REPO:-FidelOdok/MetaForge}"
COSIGN="${COSIGN:-$(command -v cosign || echo "$HOME/.local/bin/cosign")}"
IDENTITY="^https://github\\.com/${REPO}/\\.github/workflows/"
ISSUER="https://token.actions.githubusercontent.com"

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help) sed -n 2,22p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# The files the stack bind-mounts from the repository. Kept in step with
# docker-compose.yml by tests/unit/test_host_update.py.
BUNDLE=(
  "docker-compose.yml"
  "infra/temporal/dynamicconfig"
  "observability/alerting/rules.yaml"
  "observability/alertmanager.yaml"
  "observability/dashboards"
  "observability/grafana-dashboards.yml"
  "observability/grafana-datasources.yml"
  "observability/loki.yaml"
  "observability/otel-collector-config.yaml"
  "observability/prometheus.yml"
  "observability/tempo.yaml"
)
# Output directories the stack writes into.
OUTPUT_DIRS=("evals/reports")

log() { printf '\n== %s\n' "$*"; }

mkdir -p "$DIR"
cd "$DIR"
if [ ! -f .env ]; then
  echo "no .env in $DIR: create it first (secrets, COMPOSE_PROFILES)" >&2
  exit 1
fi

# The data lives in named volumes owned by the compose project, and the project
# is named after the folder unless something says otherwise. Pin it, so a
# deploy folder with another name still finds the same databases.
if [ -z "${COMPOSE_PROJECT_NAME:-}" ] && ! grep -q '^COMPOSE_PROJECT_NAME=' .env; then
  export COMPOSE_PROJECT_NAME=metaforge
fi

log "bundle $REPO@$REF"
sha=$(curl -fsSL -H "Accept: application/vnd.github.sha" \
  "https://api.github.com/repos/${REPO}/commits/${REF}")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${sha}" \
  | tar -xz -C "$tmp" --strip-components=1
for path in "${BUNDLE[@]}"; do
  [ -e "$tmp/$path" ] || { echo "bundle is missing $path" >&2; exit 1; }
  rm -rf "./$path"
  mkdir -p "$(dirname "./$path")"
  cp -a "$tmp/$path" "./$path"
done
for out in "${OUTPUT_DIRS[@]}"; do mkdir -p "./$out"; done
before=$(cat .metaforge-bundle 2>/dev/null || echo none)
echo "$sha" > .metaforge-bundle
echo "bundle ${before:0:12} -> ${sha:0:12}"

log "pull"
docker compose pull -q

log "verify signatures"
owner="${REPO%%/*}"
for img in $(docker compose config --images 2>/dev/null | sort -u | grep "^ghcr.io/${owner,,}/metaforge-" || true); do
  digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$img" \
    | grep -m1 "^${img%:*}@" || true)
  [ -n "$digest" ] || { echo "no registry digest for $img" >&2; exit 1; }
  if [ "$VERIFY" = 0 ]; then
    echo "NOT verified (--no-verify): $digest"
    continue
  fi
  [ -x "$COSIGN" ] || { echo "cosign not found; install it or pass --no-verify" >&2; exit 1; }
  "$COSIGN" verify --certificate-identity-regexp "$IDENTITY" \
    --certificate-oidc-issuer "$ISSUER" "$digest" >/dev/null 2>"$tmp/cosign.err" \
    || { echo "SIGNATURE CHECK FAILED for $digest; nothing restarted" >&2;
         tail -3 "$tmp/cosign.err" >&2; exit 1; }
  echo "verified  $digest"
done

log "start"
docker compose up -d --no-build --remove-orphans
# A container keeps the image it was created from; a re-pulled tag does not
# move it. Recreate every service whose container runs an older image.
stale=()
for svc in $(docker compose config --services 2>/dev/null); do
  cid=$(docker compose ps -q "$svc" 2>/dev/null | head -1)
  [ -n "$cid" ] || continue
  want=$(docker image inspect --format '{{.Id}}' \
    "$(docker inspect --format '{{.Config.Image}}' "$cid")" 2>/dev/null || true)
  have=$(docker inspect --format '{{.Image}}' "$cid")
  if [ -n "$want" ] && [ "$want" != "$have" ]; then stale+=("$svc"); fi
done
if [ ${#stale[@]} -gt 0 ]; then
  echo "recreating: ${stale[*]}"
  docker compose up -d --no-build --force-recreate "${stale[@]}"
fi

log "health"
for _ in $(seq 1 60); do
  [ -z "$(docker compose ps --format '{{.Name}} {{.Health}}' \
    | awk '$2!="" && $2!="healthy"')" ] && break
  sleep 5
done
if docker compose ps --format '{{.Name}} {{.Health}}' \
    | awk '$2!="" && $2!="healthy"{print "not healthy: "$1" ("$2")"; bad=1} END{exit bad}'; then
  log "done: bundle ${sha:0:12}"
else
  echo "some services are not healthy" >&2
  exit 1
fi
