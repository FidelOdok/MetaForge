# MetaForge KiCad Adapter — multi-stage build
# Usage:  docker build -f tool_registry/tools/kicad/Dockerfile -t metaforge/kicad-adapter .
#
# Provides KiCad 9 CLI for ERC, DRC, BOM, Gerber, drill, position, netlist and
# pin-mapping (Phase 1, read-only), and the Phase 2 generation tools: the KiCad
# libraries, pcbnew for board generation, and Freerouting for routing (FORGE-683).

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
    opentelemetry-api>=1.20 \
    aiohttp>=3.9

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
        # KiCad CLI package (provides kicad-cli binary, and pcbnew for /usr/bin/python3)
        kicad \
        # FORGE-683: the libraries Phase 2 generation draws symbols and footprints
        # from; nothing is drawn freehand.
        kicad-symbols \
        kicad-footprints \
        # FORGE-683: Freerouting 2.5 runs on Java 25.
        openjdk-25-jre-headless \
        ca-certificates \
        curl \
        # Required runtime libraries
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# FORGE-683: Freerouting (GPL-3.0), the autorouter, run as a separate program
# through KiCad's Specctra export/import. Pinned and checksummed. Its native
# freerouting-cli build crashes mid-route (a GraalVM serialization error), so
# the jar runs on the JRE above.
ARG FREEROUTING_VERSION=2.5.0
ARG FREEROUTING_SHA256=f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7
RUN mkdir -p /opt/freerouting \
    && curl -fsSL -o /opt/freerouting/freerouting.jar \
        "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
    && echo "${FREEROUTING_SHA256}  /opt/freerouting/freerouting.jar" | sha256sum -c -

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
ENV KICAD_HTTP_PORT=8103

WORKDIR /workspace

# MET-478 follow-up: HTTP by default (mirrors cadquery/freecad/calculix) so
# the gateway/sidecar can reach this container as a remote adapter instead
# of falling back to running kicad.* in-process where kicad-cli isn't
# installed. ``kicad-cli version`` alone doesn't prove the HTTP server
# accepted the port.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8103/health')" || exit 1

ENTRYPOINT ["python", "/app/entrypoint.py"]
