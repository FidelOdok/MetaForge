# MetaForge OpenFOAM adapter: OpenFOAM 11 behind MCP over HTTP (FORGE-557)
# Usage:  docker build -f tool_registry/tools/openfoam/Dockerfile -t metaforge/openfoam-adapter .
#
# Built by CI (adapter-images.yml), not on a shared host: the base image is about
# 4 GB. The base is the OpenFOAM Foundation's own release image, pinned by digest
# so a moved tag cannot change what is shipped. It is Ubuntu 20.04, whose system
# Python is 3.8; the adapter needs 3.11, so a standalone 3.11 is installed with uv
# rather than from a PPA, and OpenFOAM's own environment is left untouched.

FROM ghcr.io/astral-sh/uv:0.5.14 AS uv

FROM openfoam/openfoam11-paraview510:11@sha256:fd10956e0b1eb70f9808baf2857e4baf846a0f6f272f73b6d00546eae96be181 AS runtime
LABEL description="OpenFOAM 11 adapter for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

USER root
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy
RUN uv python install 3.11 \
    && uv venv /opt/venv --python 3.11 \
    && uv pip install --python /opt/venv/bin/python --no-cache \
        "pydantic>=2.0" \
        "structlog>=24.0" \
        "opentelemetry-api>=1.20" \
        "aiohttp>=3.9" \
    && rm -rf /root/.cache

WORKDIR /app
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/
COPY tool_registry/tools/openfoam/entrypoint.py ./entrypoint.py

# Same uid as the other adapters, so the shared adapter-workspace volume is
# writable by all of them.
RUN mkdir -p /workspace \
    && groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /app
USER metaforge

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    METAFORGE_ENV=docker \
    OPENFOAM_BASHRC=/opt/openfoam11/etc/bashrc \
    OPENFOAM_WORK_DIR=/workspace \
    OPENFOAM_HTTP_PORT=8300

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8300/health')" || exit 1
# The base image's own entrypoint starts a shell; replace it.
ENTRYPOINT ["python", "/app/entrypoint.py"]
