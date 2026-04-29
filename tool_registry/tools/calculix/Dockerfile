# MetaForge CalculiX Adapter -- Docker container with ccx solver
# Usage:  docker build -f tool_registry/tools/calculix/Dockerfile -t metaforge/calculix-adapter .
#
# Provides CalculiX (ccx) FEA solver with a FastAPI-based MCP server
# for stress/displacement analysis and result parsing.

# -- Stage 1: dependencies ---------------------------------------------------
# NOTE: pinned to bookworm-slim (Debian 12) because trixie dropped the
# ``calculix-ccx`` binary package from main — only the docs subset
# (``calculix-ccx-test``) remained. Bookworm still ships the solver in
# the standard repo. See MET-380.
FROM python:3.12-slim-bookworm AS deps

WORKDIR /build

# Install system deps needed for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY tool_registry/tools/calculix/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

# -- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="CalculiX FEA solver adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app

# Install CalculiX solver and runtime dependencies. Verify via
# ``command -v ccx`` rather than ``ccx -v`` — the binary prints its
# version then returns exit 201 by design, which previously masked
# real install failures behind a ``|| true`` (root cause of MET-380).
RUN apt-get update && apt-get install -y --no-install-recommends \
        calculix-ccx \
    && rm -rf /var/lib/apt/lists/* \
    && command -v ccx

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy adapter source code
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

# Create working directory for solver operations
RUN mkdir -p /workspace /tmp/calculix

# Create non-root user for security
RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/calculix /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV CALCULIX_WORK_DIR=/workspace
ENV CCX_BINARY=ccx
ENV MCP_PORT=8200

WORKDIR /workspace

EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8200/health')" || exit 1

ENTRYPOINT ["python", "-m", "tool_registry.tools.calculix.entrypoint"]
