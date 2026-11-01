# MetaForge slicer adapter: PrusaSlicer's command line behind MCP over HTTP
# Usage:  docker build -f tool_registry/tools/slicer/Dockerfile -t metaforge/slicer-adapter .
#
# Debian 13 (trixie) ships PrusaSlicer 2.9.2, with STEP import. The base is
# pinned to trixie so a Debian release cannot change the slicer underneath.
# PrusaSlicer is AGPL-3.0: it runs here as a separate program and nothing in
# MetaForge links it.

FROM python:3.11-slim-trixie AS deps
RUN pip install --no-cache-dir \
    "pydantic>=2.0" \
    "structlog>=24.0" \
    "opentelemetry-api>=1.20" \
    "aiohttp>=3.9"

FROM python:3.11-slim-trixie AS runtime
LABEL description="PrusaSlicer adapter for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends prusa-slicer \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/
COPY tool_registry/tools/slicer/entrypoint.py ./entrypoint.py

RUN mkdir -p /workspace \
    && groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /app
USER metaforge

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    METAFORGE_ENV=docker \
    SLICER_WORK_DIR=/workspace \
    SLICER_HTTP_PORT=8106

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8106/health')" || exit 1
ENTRYPOINT ["python", "/app/entrypoint.py"]
