# MetaForge Gazebo Adapter -- Docker container with Gazebo Sim (Harmonic)
# Usage:  docker build -f tool_registry/tools/gazebo/Dockerfile -t metaforge/gazebo-adapter .
#
# Provides Gazebo Sim (gz-sim, "Harmonic") headless for ROS-native
# dynamics/physics simulation, with a JSON-RPC MCP adapter.
#
# MET-740: build-verified against a real Docker daemon (previously untested,
# per the note this replaced). Two real bugs found and fixed here:
#
# 1. The base image was Debian (python:3.12-slim-bookworm), but OSRF's
#    "ubuntu-stable" apt repo has no Debian codenames at all -- `apt-get
#    update` 404'd immediately.
# 2. OSRF DOES also publish a "debian-stable" repo (right codename,
#    "bookworm" resolves), but its package index is EMPTY for every Debian
#    codename tried (bullseye/bookworm both 0-byte Packages files, confirmed
#    via the raw InRelease MD5Sum listing) -- gz-harmonic (or anything else)
#    is simply never published there. OSRF's only real, populated repo is
#    Ubuntu ("ubuntu-stable"/jammy|noble, confirmed non-empty + gz-harmonic
#    present in both). Base image switched to ubuntu:24.04 (noble) below --
#    it also ships Python 3.12 directly via apt (3.12.3), matching the rest
#    of this repo's Python version without needing a separate python:3.12
#    base at all.

FROM ubuntu:24.04 AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        python3.12 \
        python3.12-venv \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/gazebo/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /build/requirements.txt

# -- Stage 2: runtime --------------------------------------------------------
FROM ubuntu:24.04 AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="Gazebo Sim (Harmonic) dynamics adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/FidelOdok/MetaForge"

WORKDIR /app

# Install Python 3.12 + Gazebo Sim (Harmonic) from the OSRF apt repo.
# Headless server-only use (-s flag at runtime) still needs the
# simulation/physics packages, not the GUI ones -- gz-sim is the
# meta-package that pulls those in.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        ca-certificates \
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
COPY --from=deps /usr/lib/python3.12 /usr/lib/python3.12
COPY --from=deps /usr/local/lib/python3.12 /usr/local/lib/python3.12
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
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://localhost:8201/health')" || exit 1

ENTRYPOINT ["python3.12", "-m", "tool_registry.tools.gazebo.entrypoint"]
