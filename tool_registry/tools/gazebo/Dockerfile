# MetaForge Gazebo Adapter -- Docker container with Gazebo Sim (Harmonic)
# Usage:  docker build -f tool_registry/tools/gazebo/Dockerfile -t metaforge/gazebo-adapter .
#
# Provides Gazebo Sim (gz-sim, "Harmonic") headless for ROS-native
# dynamics/physics simulation, with a JSON-RPC MCP adapter.
#
# NOTE: unlike calculix's Dockerfile (verified against a real ``ccx``
# install), this file has not been build-tested against a real ``gz``
# binary in this environment -- no Docker daemon / package-index network
# access was available while authoring it. Treat the OSRF repo setup and
# package name below as a first cut to verify before relying on it, the
# same way MET-380 caught a Debian packaging gotcha for CalculiX.

FROM python:3.12-slim-bookworm AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/gazebo/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

# -- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="Gazebo Sim (Harmonic) dynamics adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/FidelOdok/MetaForge"

WORKDIR /app

# Install Gazebo Sim (Harmonic) from the OSRF apt repo. Headless server-only
# use (-s flag at runtime) still needs the simulation/physics packages, not
# the GUI ones -- gz-sim is the meta-package that pulls those in.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        lsb-release \
    && curl -fsSL https://packages.osrfoundation.org/gazebo.gpg -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        gz-harmonic \
    && rm -rf /var/lib/apt/lists/* \
    && command -v gz

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy adapter source code
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

RUN mkdir -p /workspace /tmp/gazebo

RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/gazebo /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV GAZEBO_WORK_DIR=/workspace
ENV GZ_BINARY=gz
ENV MCP_PORT=8201

WORKDIR /workspace

EXPOSE 8201

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8201/health')" || exit 1

ENTRYPOINT ["python", "-m", "tool_registry.tools.gazebo.entrypoint"]
