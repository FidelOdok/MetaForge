#!/usr/bin/env bash
#
# Build a standalone `forge` CLI binary with PyInstaller (MET-555).
#
# The forge CLI is a thin HTTP client (only httpx + structlog beyond stdlib), so
# the bundle stays lean — the heavy gateway/server stack is excluded explicitly.
# Produces a single self-contained executable at dist/forge that runs without a
# Python install.
#
# Usage:
#   pip install "pyinstaller>=6.0"      # or: pip install -e ".[build]"
#   scripts/build_forge_binary.sh
#
# Note: PyInstaller builds for the CURRENT platform only. For macOS/Windows/Linux
# artifacts, run this on each target (e.g. a CI matrix), not cross-compiled.

set -euo pipefail

cd "$(dirname "$0")/.."

# Server-side deps the CLI never imports — excluded to keep the binary small and
# avoid dragging in native libraries the CLI doesn't need.
EXCLUDES=(
  fastapi starlette uvicorn pydantic pydantic_core
  opentelemetry kafka neo4j
  api_gateway orchestrator twin_core domain_agents tool_registry
  numpy pandas matplotlib
)

exclude_args=()
for mod in "${EXCLUDES[@]}"; do
  exclude_args+=(--exclude-module "$mod")
done

pyinstaller \
  --onefile \
  --name forge \
  --collect-submodules cli.forge_cli \
  "${exclude_args[@]}" \
  --distpath dist \
  --workpath build/pyinstaller \
  --specpath build/pyinstaller \
  --noconfirm \
  cli/forge_cli/__main__.py

echo
echo "Built: dist/forge"
echo "Try:   ./dist/forge chat --help"
