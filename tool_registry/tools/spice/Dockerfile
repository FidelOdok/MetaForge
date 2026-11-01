# MetaForge SPICE adapter: ngspice behind MCP over HTTP (FORGE-542)
# Usage:  docker build -f tool_registry/tools/spice/Dockerfile -t metaforge/spice-adapter .

FROM python:3.11-slim AS deps
RUN pip install --no-cache-dir \
    "pydantic>=2.0" \
    "structlog>=24.0" \
    "opentelemetry-api>=1.20" \
    "aiohttp>=3.9"

FROM python:3.11-slim AS runtime
LABEL description="ngspice adapter for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ngspice \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/
COPY tool_registry/tools/spice/entrypoint.py ./entrypoint.py

RUN mkdir -p /workspace \
    && groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /app
USER metaforge

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    METAFORGE_ENV=docker \
    SPICE_WORK_DIR=/workspace \
    SPICE_HTTP_PORT=8104

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8104/health')" || exit 1
ENTRYPOINT ["python", "/app/entrypoint.py"]
