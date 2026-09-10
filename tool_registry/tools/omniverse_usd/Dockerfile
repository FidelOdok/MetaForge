# MetaForge OpenUSD Conversion Adapter -- Docker container with usd-core/trimesh
# Usage:  docker build -f tool_registry/tools/omniverse_usd/Dockerfile -t metaforge/omniverse-usd-adapter .
#
# Converts GLB (already produced by cadquery/freecad/occt-converter's STEP
# tessellation) into OpenUSD stages. Unlike gazebo's Dockerfile, every
# dependency here is a plain PyPI wheel -- no apt repo, no conda, no GPU --
# verified installable in a plain venv 2026-08-25.

FROM python:3.12-slim-bookworm AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/omniverse_usd/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

# -- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="OpenUSD (GLB -> USD) conversion adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/FidelOdok/MetaForge"

WORKDIR /app

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

RUN mkdir -p /workspace /tmp/omniverse_usd

RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/omniverse_usd /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV OMNIVERSE_USD_WORK_DIR=/workspace
ENV MCP_PORT=8202

WORKDIR /workspace

EXPOSE 8202

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8202/health')" || exit 1

ENTRYPOINT ["python", "-m", "tool_registry.tools.omniverse_usd.entrypoint"]
