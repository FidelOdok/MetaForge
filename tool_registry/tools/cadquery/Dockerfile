# MetaForge CadQuery Adapter — multi-stage build
# Usage:  docker build -f tool_registry/tools/cadquery/Dockerfile -t metaforge/cadquery-adapter .
#
# Provides CadQuery (Pythonic CAD on OpenCascade) for parametric CAD generation,
# boolean operations, geometry analysis, export, and LLM-generated script execution
# (each script in its own OS-isolated child process, see script_runner.py).
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
        opentelemetry-api>=1.20 \
        opentelemetry-sdk>=1.20 \
        opentelemetry-exporter-otlp-proto-grpc>=1.20

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

# Shared code: only the modules this adapter imports, so a change elsewhere
# in these packages neither rebuilds the image nor busts its layer cache.
# Keep in step with this adapter's list in .github/adapter-image-filters.yml;
# CI imports every module of the adapter inside the built image.
COPY mcp_core/*.py ./mcp_core/
COPY observability/*.py ./observability/
COPY observability/audit/ ./observability/audit/
COPY observability/slo/ ./observability/slo/
COPY tool_registry/*.py ./tool_registry/
COPY tool_registry/mcp_server/ ./tool_registry/mcp_server/
COPY tool_registry/tools/__init__.py ./tool_registry/tools/
COPY tool_registry/tools/cadquery/ ./tool_registry/tools/cadquery/

COPY tool_registry/tools/cadquery/entrypoint.py ./entrypoint.py
COPY --chmod=0755 tool_registry/tools/cadquery/start.sh /usr/local/bin/cadquery-start

RUN mkdir -p /workspace /tmp/cadquery /run/cadquery-runner

# The adapter runs as uid 1000 (cadquery-start drops to it). /app stays owned
# by root, so neither the adapter nor a script child can change the code.
# /run/cadquery-runner holds each script child's private folder: a child of a
# runner uid may create its folder there (sticky, 1733) but not list it.
RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/cadquery \
    && chown metaforge:metaforge /run/cadquery-runner \
    && chmod 1733 /run/cadquery-runner

# Started as root only so cadquery-start can keep CAP_SETUID and CAP_SETGID for
# the script runner while it drops to uid 1000 (see start.sh).
USER root

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV CADQUERY_WORK_DIR=/workspace
# The isolated script runner (script_runner.py): a private folder per run under
# this path, one uid per concurrent run, and every isolation layer required.
ENV CADQUERY_RUNNER_ROOT=/run/cadquery-runner
ENV CADQUERY_RUNNER_UIDS=61000-61003
ENV CADQUERY_RUNNER_REQUIRE=uid,landlock,seccomp
# The root file system is read-only under compose; caches go to /tmp.
ENV XDG_CACHE_HOME=/tmp/.cache

WORKDIR /workspace

EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8101/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/cadquery-start"]
CMD ["python", "/app/entrypoint.py"]
