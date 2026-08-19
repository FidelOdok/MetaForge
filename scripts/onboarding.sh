#!/usr/bin/env bash
#
# MetaForge onboarding — the one script that gets you from nothing to a
# running instance with your first project created.
#
# Handles both audiences:
#   - Usage  : a stable, hardened instance to actually use day-to-day
#              (built images, no source mounted, generated secrets)
#   - Develop: an editable checkout to contribute to MetaForge itself
#              (Python venv, hot-reload containers, dev secrets)
#
# Either way it: checks requirements, gets your LLM API key, builds and
# starts the stack, installs the `forge` CLI, proves it all actually works,
# and walks you through creating your first project.
#
#   curl -fsSL https://raw.githubusercontent.com/FidelOdok/MetaForge/main/scripts/onboarding.sh | bash
#
#   ./scripts/onboarding.sh                       # asks usage vs develop
#   ./scripts/onboarding.sh --usage --yes
#   ./scripts/onboarding.sh --develop --extras dev,knowledge,cadquery
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/onboarding.sh --usage --yes
#
# Idempotent: safe to re-run. Never overwrites a secret already set in
# .env, never touches a checkout that's already there.

set -euo pipefail

# ── Options ──────────────────────────────────────────────────────────
MODE=""                 # "usage" | "develop"
INSTALL_DIR=""
GIT_REF="main"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_PROVIDER=""
EXTRAS="dev,knowledge,cadquery"
SKIP_CLI=0
NON_INTERACTIVE=0
FULL_STACK=0
HEALTH_TIMEOUT=240
REPO_URL="https://github.com/FidelOdok/MetaForge.git"

usage() {
  cat <<'EOF'
Usage: onboarding.sh [options]

  --usage                run in "usage" mode: hardened, built images,
                         generated secrets — for people who just want
                         MetaForge running
  --develop              run in "develop" mode: editable Python install,
                         hot-reload containers, source bind-mounted —
                         for contributing to MetaForge itself
                         (omit both flags to be asked interactively)

  --dir <path>           checkout/install directory if not already in one
                         (default: ./MetaForge)
  --ref <git-ref>        branch or tag to install (default: main)
  --extras <list>        develop mode only: pip extras (default:
                         dev,knowledge,cadquery)
  --llm-api-key <key>    skip the interactive prompt, use this key
  --skip-cli             don't install the `forge` CLI binary
  --full                 also start Kafka/Temporal (not required for
                         Phase 1 core features)
  --yes                  non-interactive: never prompt (defaults to
                         usage mode, no project name/idea prompts)
  -h, --help             show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --usage) MODE="usage"; shift ;;
    --develop|--dev) MODE="develop"; shift ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --dir=*) INSTALL_DIR="${1#*=}"; shift ;;
    --ref) GIT_REF="$2"; shift 2 ;;
    --ref=*) GIT_REF="${1#*=}"; shift ;;
    --extras) EXTRAS="$2"; shift 2 ;;
    --extras=*) EXTRAS="${1#*=}"; shift ;;
    --llm-api-key) LLM_API_KEY="$2"; shift 2 ;;
    --llm-api-key=*) LLM_API_KEY="${1#*=}"; shift ;;
    --skip-cli) SKIP_CLI=1; shift ;;
    --full) FULL_STACK=1; shift ;;
    --yes) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

bold()  { printf '\033[1m%s\033[0m\n' "$1"; }
log()   { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
ok()    { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
bad()   { printf '  \033[1;31m✗\033[0m %s\n' "$1"; }
warn()  { printf '  \033[1;33m!\033[0m %s\n' "$1" >&2; }
die()   { printf '\033[1;31mError:\033[0m %s\n' "$1" >&2; exit 1; }
rand_secret() { openssl rand -hex 24 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# `curl -fsSL ... | bash` feeds the script itself over stdin, so plain `read`
# can't prompt (nothing left to read, or it reads script bytes). Read from
# the controlling terminal directly instead — the same trick rustup/Homebrew
# use — so prompts still work when piped. Falls back to "not interactive"
# only when no terminal is attached at all (true headless/CI).
tty_available() { { : < /dev/tty; } 2>/dev/null; }
interactive() { [ "$NON_INTERACTIVE" -eq 0 ] && tty_available; }

# ask VAR "prompt" — visible prompt, reads from /dev/tty. Returns 1 (leaves
# VAR unset) if no terminal is attached.
ask() {
  local __var="$1" __prompt="$2" __val=""
  tty_available || return 1
  read -r -p "$__prompt" __val < /dev/tty
  printf -v "$__var" '%s' "$__val"
}

# ask_hidden VAR "prompt" — same, but doesn't echo input (for API keys).
ask_hidden() {
  local __var="$1" __prompt="$2" __val=""
  tty_available || return 1
  read -r -s -p "$__prompt" __val < /dev/tty
  echo
  printf -v "$__var" '%s' "$__val"
}

# ── 0. Welcome ───────────────────────────────────────────────────────
cat <<'BANNER'

  MetaForge — local-first control plane for hardware design.
  This turns human intent (PRDs, constraints) into reviewable,
  manufacturable deliverables — schematics, BOMs, CAD/FEA, test plans —
  by driving real engineering tools (KiCad, FreeCAD, CalculiX, SPICE)
  through specialist AI agents.

BANNER

# ── 1. Mode ──────────────────────────────────────────────────────────
if [ -z "$MODE" ]; then
  if [ "$NON_INTERACTIVE" -eq 1 ] || ! interactive; then
    MODE="usage"
  else
    bold "How do you want to run MetaForge?"
    echo "  1) Use it            — stable instance, built images, hardened secrets [recommended]"
    echo "  2) Develop/contribute — editable source, hot-reload, Python venv"
    ask choice "Choose [1]: " || true
    case "$choice" in
      2) MODE="develop" ;;
      *) MODE="usage" ;;
    esac
  fi
fi
ok "Mode: $MODE"

bold "What you'll need:"
cat <<'REQS'
  1. Docker Desktop (or Docker Engine + the Compose plugin)
       https://docs.docker.com/get-docker/
  2. ~5 GB free disk space, 4 GB+ RAM free (image builds + Postgres/Neo4j/MinIO)
  3. These ports free: 8000 (gateway), 3000 (dashboard), 7474 + 7687 (neo4j),
     9000 + 9001 (minio)
  4. An API key from ONE model provider, so the chat/assistant layer can
     actually talk to an LLM:
       - Anthropic (recommended): https://console.anthropic.com/settings/keys
       - OpenAI:                  https://platform.openai.com/api-keys
       - OpenRouter (one key, many models): https://openrouter.ai/keys
     The harness actually supports ~30 providers (see `forge auth list` once
     it's running) — these three are just the fastest paths to a working key.
     Everything else (Digital Twin, BOM, CAD/FEA adapters, dashboard) works
     without a key — only the conversational assistant needs one.
  5. git (to clone the repo, if you're not already inside a checkout)
REQS
if [ "$MODE" = "develop" ]; then
  echo "  6. Python 3.11+ on PATH (for the editable install)"
fi

# ── 2. Preflight ─────────────────────────────────────────────────────
log "Checking your machine"

fail=0
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
  ok "Docker Compose: $(docker compose version --short 2>/dev/null)"
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
  ok "Docker Compose (standalone)"
else
  bad "Docker Compose not found — install it: https://docs.docker.com/get-docker/"
  fail=1
fi

command -v curl >/dev/null 2>&1 && ok "curl available" || { bad "curl not found"; fail=1; }
command -v openssl >/dev/null 2>&1 && ok "openssl available" || warn "openssl not found — falling back to /dev/urandom for secrets"

PYTHON=""
if [ "$MODE" = "develop" ]; then
  for candidate in python3.11 python3.12 python3.13 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")"
      major="${ver%%.*}"; minor="${ver#*.}"
      if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then PYTHON="$candidate"; break; fi
    fi
  done
  if [ -n "$PYTHON" ]; then
    ok "Python: $($PYTHON --version) ($PYTHON)"
  else
    bad "Python 3.11+ not found — install it, or re-run with --usage instead (no host Python needed)"
    fail=1
  fi
fi

if command -v free >/dev/null 2>&1; then
  mem_avail_mb="$(free -m | awk '/^Mem:/{print $7}')"
  [ -n "${mem_avail_mb:-}" ] && [ "$mem_avail_mb" -lt 3500 ] 2>/dev/null && \
    warn "Only ~${mem_avail_mb}MB RAM available — the build may be slow or get OOM-killed. 4GB+ recommended."
fi
avail_kb="$(df -Pk . 2>/dev/null | awk 'NR==2{print $4}')"
if [ -n "${avail_kb:-}" ] && [ "$avail_kb" -lt 5000000 ] 2>/dev/null; then
  warn "Less than ~5GB free disk here — image builds may fail partway through."
fi

[ "$fail" -eq 1 ] && die "Install the missing prerequisite(s) above, then re-run this script."

# ── 3. LLM API key ───────────────────────────────────────────────────
log "Setting up your LLM provider"

if [ -z "$LLM_API_KEY" ]; then
  LLM_API_KEY="${ANTHROPIC_API_KEY:-${OPENAI_API_KEY:-${OPENROUTER_API_KEY:-}}}"
  [ -n "$LLM_API_KEY" ] && ok "Found an API key already in your environment"
fi
if [ -z "$LLM_API_KEY" ]; then
  if interactive; then
    echo "  Paste an API key to enable chat now, or press Enter to configure it later."
    echo "  (Anthropic keys start with sk-ant-, OpenRouter keys with sk-or-, OpenAI keys with sk-)"
    ask_hidden LLM_API_KEY "  API key: " || true
    [ -z "$LLM_API_KEY" ] && warn "Skipping — the assistant/chat layer will be unavailable until you set METAFORGE_LLM_API_KEY later."
  else
    warn "No API key provided — continuing without one. The assistant/chat layer will be unavailable until you set METAFORGE_LLM_API_KEY later."
  fi
fi
if [ -n "$LLM_API_KEY" ]; then
  # Check the more specific prefixes first — OpenRouter keys (sk-or-...) would
  # otherwise match a naive "starts with sk-" catch-all for OpenAI and get
  # pointed at the wrong base URL entirely.
  case "$LLM_API_KEY" in
    sk-ant-*) LLM_PROVIDER="anthropic" ;;
    sk-or-*) LLM_PROVIDER="openrouter" ;;
    *) LLM_PROVIDER="openai" ;;
  esac
fi

# ── 4. Resolve the checkout (clone only if we're not already in one) ─
log "Preparing the MetaForge checkout"

is_metaforge_checkout() {
  [ -f "$1/docker-compose.yml" ] && [ -f "$1/pyproject.toml" ] && grep -q '^name = "metaforge"' "$1/pyproject.toml" 2>/dev/null
}

# BASH_SOURCE is empty (not just unset) when the script is fed to bash over
# stdin (`curl ... | bash` — there's no script file at all), and under `set
# -u` even *indexing* an empty array is a fatal "unbound variable" error. So
# check emptiness before touching the index, rather than expanding it and
# hoping a fallback default masks the error.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
fi
if [ -n "$SCRIPT_DIR" ] && is_metaforge_checkout "$SCRIPT_DIR/.."; then
  ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  ok "Running from an existing checkout: $ROOT_DIR"
elif [ -n "$INSTALL_DIR" ] && is_metaforge_checkout "$INSTALL_DIR"; then
  ROOT_DIR="$(cd "$INSTALL_DIR" && pwd)"
  ok "Using existing checkout: $ROOT_DIR"
else
  TARGET="${INSTALL_DIR:-./MetaForge}"
  # Loop instead of failing outright: a stray non-checkout directory at the
  # default path (e.g. a partial clone left over from an earlier attempt) is
  # common enough that the user shouldn't have to re-invoke the whole
  # curl-pipe command with a new --dir by hand.
  while [ -d "$TARGET" ] && [ "$(ls -A "$TARGET" 2>/dev/null)" ] && ! is_metaforge_checkout "$TARGET"; do
    warn "$TARGET exists but isn't a complete MetaForge checkout (maybe a partial clone from an earlier attempt?)."
    if ! interactive; then
      die "Remove it (rm -rf '$TARGET') or re-run with --dir <empty-path>."
    fi
    new_target=""
    ask new_target "  Install into a different directory instead (blank to abort): " || true
    if [ -z "${new_target:-}" ]; then
      die "Aborted. Remove '$TARGET' yourself first, or re-run with --dir <empty-path>."
    fi
    TARGET="$new_target"
  done
  if [ -d "$TARGET" ] && [ "$(ls -A "$TARGET" 2>/dev/null)" ]; then
    ROOT_DIR="$(cd "$TARGET" && pwd)"
    ok "Using existing checkout: $ROOT_DIR"
  else
    command -v git >/dev/null 2>&1 || die "git is required to clone MetaForge (or run this from inside an existing checkout)."
    log "Cloning MetaForge ($GIT_REF) into $TARGET"
    git clone --depth 1 --branch "$GIT_REF" "$REPO_URL" "$TARGET"
    ROOT_DIR="$(cd "$TARGET" && pwd)"
    ok "Cloned to $ROOT_DIR"
  fi
fi
cd "$ROOT_DIR"

# ── 5. .env ──────────────────────────────────────────────────────────
log "Configuring .env"

if [ -f .env ]; then
  ok ".env already exists — filling in only missing values"
else
  cp .env.example .env
  ok "Created .env from .env.example"
fi

set_if_blank() {
  local key="$1" value="$2"
  if grep -qE "^${key}=.+" .env; then return 0
  elif grep -qE "^${key}=$" .env; then
    local tmp; tmp="$(mktemp)"
    sed "s|^${key}=$|${key}=${value}|" .env > "$tmp" && mv "$tmp" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

if [ "$MODE" = "usage" ]; then
  # Postgres creds aren't in .env.example at all (compose defaults them all
  # to "metaforge") — pin real ones so this doesn't run on the dev default.
  set_if_blank POSTGRES_USER "metaforge"
  set_if_blank POSTGRES_DB "metaforge"
  set_if_blank POSTGRES_PASSWORD "$(rand_secret)"
  set_if_blank NEO4J_PASSWORD "$(rand_secret)"
  set_if_blank GRAFANA_PASSWORD "$(rand_secret)"
  set_if_blank METAFORGE_MCP_API_KEY "$(rand_secret)"
  # Gates POST/DELETE /v1/harness/credentials and PUT /v1/harness/selection.
  # Left unset, those endpoints are open with a logged warning.
  set_if_blank METAFORGE_HARNESS_ADMIN_TOKEN "$(rand_secret)"
  ok "Generated strong secrets for any credential left at its default"
  chmod 600 .env
  ok ".env permissions set to 600 (owner read/write only)"
else
  ok "Dev-mode defaults kept as-is (local-only, matches docker-compose.override.yml)"
fi

if [ -n "$LLM_API_KEY" ]; then
  set_if_blank METAFORGE_LLM_PROVIDER "$LLM_PROVIDER"
  set_if_blank METAFORGE_LLM_API_KEY "$LLM_API_KEY"
  ok "Configured LLM provider: $LLM_PROVIDER"
fi

# ── 6. Python environment (develop mode only) ────────────────────────
if [ "$MODE" = "develop" ]; then
  log "Setting up Python virtualenv (.venv, extras: $EXTRAS)"
  if [ ! -d .venv ]; then
    "$PYTHON" -m venv .venv
    ok "Created .venv"
  else
    ok ".venv already exists"
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip -q
  pip install -e ".[$EXTRAS]" -q
  ok "Installed metaforge (editable) with extras: $EXTRAS"
  deactivate
fi

# ── 7. Build + start the stack ───────────────────────────────────────
if [ "$MODE" = "usage" ]; then
  log "Building images (production mode — this can take a few minutes on first run)"
  SERVICES=(postgres neo4j minio gateway dashboard)
  [ "$FULL_STACK" -eq 1 ] && SERVICES+=(zookeeper kafka temporal-db temporal)
  # -f pins to docker-compose.yml only, skipping the auto-merged dev override
  # (hot-reload bind mounts + Vite dashboard).
  COMPOSE_BASE=("${DC[@]}" -f docker-compose.yml --profile prod)
  if ! "${COMPOSE_BASE[@]}" up -d --build "${SERVICES[@]}"; then
    warn "docker compose up reported an error — often just service_healthy ordering timing out on a slow first boot. Continuing to health checks."
  fi
else
  log "Starting dev containers (hot-reload override auto-applied)"
  SERVICES=(postgres neo4j minio gateway dashboard-dev)
  [ "$FULL_STACK" -eq 1 ] && SERVICES+=(zookeeper kafka temporal-db temporal)
  COMPOSE_BASE=("${DC[@]}")
  if ! "${COMPOSE_BASE[@]}" up -d "${SERVICES[@]}"; then
    warn "docker compose up reported an error — often just service_healthy ordering timing out on a slow first boot. Continuing to health checks."
  fi
fi

wait_for_container_health() {
  local service="$1" timeout="$2" waited=0 cid status
  cid="$("${COMPOSE_BASE[@]}" ps -q "$service" 2>/dev/null || true)"
  [ -z "$cid" ] && { warn "$service: no container found, skipping health wait"; return 0; }
  if ! docker inspect --format '{{.State.Health}}' "$cid" 2>/dev/null | grep -qv '<nil>'; then
    ok "$service: no healthcheck defined, assuming started"
    return 0
  fi
  printf '  waiting for %s' "$service"
  while [ "$waited" -lt "$timeout" ]; do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
    [ "$status" = "healthy" ] && { printf ' — healthy\n'; return 0; }
    printf '.'; sleep 3; waited=$((waited + 3))
  done
  printf ' — timed out (last status: %s)\n' "$status"
  warn "$service did not report healthy within ${timeout}s — check: ${COMPOSE_BASE[*]} logs $service"
}

log "Waiting for services to become healthy (timeout ${HEALTH_TIMEOUT}s each)"
for svc in postgres neo4j minio; do
  wait_for_container_health "$svc" "$HEALTH_TIMEOUT" || true
done

GATEWAY_PORT="$(grep -E '^GATEWAY_PORT=' .env | tail -1 | cut -d= -f2)"; GATEWAY_PORT="${GATEWAY_PORT:-8000}"
DASHBOARD_PORT="$(grep -E '^DASHBOARD_PORT=' .env | tail -1 | cut -d= -f2)"; DASHBOARD_PORT="${DASHBOARD_PORT:-3000}"

printf '  waiting for gateway http://localhost:%s/health' "$GATEWAY_PORT"
waited=0; gateway_ok=0
while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
  if curl -fsS "http://localhost:${GATEWAY_PORT}/health" >/dev/null 2>&1; then
    printf ' — up\n'; gateway_ok=1; break
  fi
  printf '.'; sleep 3; waited=$((waited + 3))
done
if [ "$gateway_ok" -eq 1 ]; then
  ok "Gateway healthy at http://localhost:${GATEWAY_PORT}/health"
else
  printf ' — timed out\n'
  warn "Gateway not responding yet — check: ${COMPOSE_BASE[*]} logs gateway"
fi

# ── 8. Install the forge CLI ─────────────────────────────────────────
if [ "$SKIP_CLI" -eq 0 ]; then
  log "Installing the forge CLI"
  if command -v forge >/dev/null 2>&1; then
    ok "forge already installed: $(command -v forge)"
  else
    bash scripts/install.sh || warn "forge CLI install failed — retry manually with scripts/install.sh"
  fi
else
  log "Skipping forge CLI install (--skip-cli)"
fi

# ── 9. Verify it actually works ──────────────────────────────────────
log "Verifying your instance"

HAVE_FORGE=0
if command -v forge >/dev/null 2>&1; then
  HAVE_FORGE=1
  ok "forge CLI: $(command -v forge)"
  forge auth list 2>/dev/null | sed 's/^/  /' || true
else
  warn "forge CLI not found on PATH — open a new shell (it was just added to your profile) or re-run scripts/install.sh"
fi

# ── 10. First project walkthrough ────────────────────────────────────
if [ "$gateway_ok" -eq 1 ]; then
  log "Let's create your first project"
  echo "  A project groups your work products (CAD, BOM, decisions) and scopes"
  echo "  the assistant to your hardware idea."

  PROJECT_NAME="My First Project"
  PROJECT_IDEA=""
  if interactive; then
    ask input_name "  Project name [$PROJECT_NAME]: " || true
    [ -n "${input_name:-}" ] && PROJECT_NAME="$input_name"
    ask PROJECT_IDEA "  One line about what you're building (optional): " || true
  fi
  DESCRIPTION="${PROJECT_IDEA:-Created during MetaForge onboarding.}"

  create_resp="$(curl -fsS -X POST "http://localhost:${GATEWAY_PORT}/v1/projects" \
    -H 'Content-Type: application/json' \
    -d "$(printf '{"name":"%s","description":"%s"}' \
          "$(printf '%s' "$PROJECT_NAME" | sed 's/"/\\"/g')" \
          "$(printf '%s' "$DESCRIPTION" | sed 's/"/\\"/g')")" 2>&1)" || create_resp=""

  PROJECT_ID="$(printf '%s' "$create_resp" | grep -o '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"

  if [ -n "$PROJECT_ID" ]; then
    ok "Created project '$PROJECT_NAME' (id: $PROJECT_ID)"
    if [ "$HAVE_FORGE" -eq 1 ] && [ -n "$LLM_API_KEY" ]; then
      log "Asking the assistant about it (project-scoped chat)"
      msg="Introduce yourself in one sentence and say what you can help me build for this project."
      [ -n "$PROJECT_IDEA" ] && msg="I'm building: $PROJECT_IDEA. $msg"
      reply="$(forge chat --project "$PROJECT_ID" -m "$msg" 2>&1)" || true
      if [ -n "$reply" ]; then
        ok "Assistant replied:"
        echo "$reply" | sed 's/^/  /'
      else
        warn "No reply from the assistant — check: forge auth list, and gateway logs."
      fi
    elif [ -z "$LLM_API_KEY" ]; then
      warn "No LLM key configured — skipping the chat demo. Set one, then run: forge chat --project $PROJECT_ID -m \"...\""
    fi
    echo "  Dashboard: http://localhost:${DASHBOARD_PORT}/projects/${PROJECT_ID}"
  else
    warn "Couldn't create the project automatically — response: ${create_resp:-<no response>}"
    warn "Create one manually at http://localhost:${DASHBOARD_PORT}/projects or: curl -X POST http://localhost:${GATEWAY_PORT}/v1/projects -H 'Content-Type: application/json' -d '{\"name\":\"My First Project\",\"description\":\"...\"}'"
  fi
else
  warn "Skipping the first-project walkthrough — the gateway isn't healthy yet. Once it is: open http://localhost:${DASHBOARD_PORT}/projects"
fi

# ── 11. What's next ──────────────────────────────────────────────────
log "You're up and running ($MODE mode)"
echo "  Install   : $ROOT_DIR"
echo "  Gateway   : http://localhost:${GATEWAY_PORT}"
echo "  Dashboard : http://localhost:${DASHBOARD_PORT}"
[ "$MODE" = "usage" ] && echo "  Secrets   : generated into $ROOT_DIR/.env (mode 600, gitignored) — back it up somewhere safe"
echo
echo "Try next:"
echo "  forge                                   # interactive chat"
echo "  forge projects list                     # see your projects"
[ -n "${PROJECT_ID:-}" ] && echo "  forge chat --project $PROJECT_ID -m \"...\"   # keep talking about this project"
echo "  open http://localhost:${DASHBOARD_PORT}        # dashboard tour"
if [ "$MODE" = "develop" ]; then
  echo
  echo "Dev workflow:"
  echo "  source .venv/bin/activate"
  echo "  make check   # lint + format + typecheck"
  echo "  make test    # unit + contract tests"
  echo "  See CLAUDE.md for the full git/PR workflow."
else
  echo
  echo "This instance only listens on localhost by default. To reach it from"
  echo "another machine, don't publish ports directly — see"
  echo "docs/runbooks/cloudflare-mcp-tunnel.md for the tunnel this repo already uses."
  echo
  echo "To update later:"
  echo "  cd '$ROOT_DIR' && git pull && ${COMPOSE_BASE[*]} up -d --build ${SERVICES[*]}"
fi
echo
echo "Docs: $ROOT_DIR/docs/getting-started.md · docs/cli-reference.md · docs/troubleshooting.md"
