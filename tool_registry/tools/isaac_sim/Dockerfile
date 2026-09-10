# MetaForge Isaac Sim Adapter -- thin coordinator, no GPU needed here
# Usage:  docker build -f tool_registry/tools/isaac_sim/Dockerfile -t metaforge/isaac-sim-adapter .
#
# This container does NOT run Isaac Sim itself. It's a lightweight MCP
# coordinator that dispatches jobs to a *separate* nvcr.io/nvidia/isaac-sim
# container via tool_registry.compute_providers.resolve_runtime() -- same
# shape as compute_providers.py's own RunPod/Vast.ai runtimes, one level up.
# No GPU, no NVIDIA Container Toolkit, no ACCEPT_EULA needed for THIS image.

FROM python:3.12-slim-bookworm AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/isaac_sim/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

# -- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="Isaac Sim (PhysX + RTX) dispatch coordinator for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/FidelOdok/MetaForge"

WORKDIR /app

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

RUN mkdir -p /workspace /tmp/isaac_sim

RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/isaac_sim /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV ISAAC_SIM_WORK_DIR=/workspace
ENV MCP_PORT=8203

WORKDIR /workspace

EXPOSE 8203

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8203/health')" || exit 1

ENTRYPOINT ["python", "-m", "tool_registry.tools.isaac_sim.entrypoint"]
