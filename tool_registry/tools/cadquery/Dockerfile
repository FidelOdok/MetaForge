# MetaForge CadQuery Adapter — multi-stage build
# Usage:  docker build -f tool_registry/tools/cadquery/Dockerfile -t metaforge/cadquery-adapter .
#
# Provides CadQuery (Pythonic CAD on OpenCascade) for parametric CAD generation,
# boolean operations, geometry analysis, export, and LLM-generated script execution.
# Much lighter than FreeCAD (~300-500MB vs ~1-2GB) with a fluent Python API.

# -- Stage 1: dependencies -------------------------------------------------
FROM python:3.11-slim AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/cadquery/requirements.txt /build/requirements.txt

RUN pip install --no-cache-dir -r /build/requirements.txt \
    && pip install --no-cache-dir \
        pydantic>=2.0 \
        structlog>=24.0 \
        opentelemetry-api>=1.20

# -- Stage 2: runtime ------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="CadQuery adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app

# Runtime dependencies for OpenCascade (pulled in by CadQuery wheels)
# libxrender1, libxmu6, libxi6 are needed by OCP even in headless mode
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libxrender1 \
        libxmu6 \
        libxi6 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy adapter source code
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

COPY tool_registry/tools/cadquery/entrypoint.py ./entrypoint.py

RUN mkdir -p /workspace /tmp/cadquery

# Non-root user for security
RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/cadquery /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV CADQUERY_WORK_DIR=/workspace

WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import cadquery; print(cadquery.__version__)" || exit 1

ENTRYPOINT ["python", "/app/entrypoint.py"]
