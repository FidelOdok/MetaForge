# MetaForge KiCad Adapter — multi-stage build
# Usage:  docker build -f tool_registry/tools/kicad/Dockerfile -t metaforge/kicad-adapter .
#
# Provides KiCad 8 CLI for ERC, DRC, BOM export, Gerber export, netlist
# export, and pin mapping extraction (Phase 1 — read-only operations).

# ── Stage 1: dependencies ────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# Install system deps needed for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install minimal Python dependencies for the adapter
RUN pip install --no-cache-dir \
    pydantic>=2.0 \
    structlog>=24.0 \
    opentelemetry-api>=1.20

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="KiCad 8 CLI adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app

# Install KiCad CLI and runtime dependencies
# KiCad 8 is available in Debian testing/unstable repos; for slim images
# we use the Debian package with minimal GUI deps via --no-install-recommends
RUN apt-get update && apt-get install -y --no-install-recommends \
        # KiCad CLI package (provides kicad-cli binary)
        kicad \
        # Required runtime libraries
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy adapter source code (only what the adapter needs)
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

# Copy entrypoint
COPY tool_registry/tools/kicad/entrypoint.py ./entrypoint.py

# Create working directory for KiCad operations
RUN mkdir -p /workspace /tmp/kicad

# Create non-root user for security
RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/kicad /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV KICAD_WORK_DIR=/workspace

WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD kicad-cli version || exit 1

ENTRYPOINT ["python", "/app/entrypoint.py"]
