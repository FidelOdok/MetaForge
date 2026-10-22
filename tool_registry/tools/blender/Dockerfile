# MetaForge Blender adapter (FORGE-628): Blender 5.2.2 LTS, headless renders.
# Usage:  docker build -f tool_registry/tools/blender/Dockerfile -t metaforge/blender-adapter .
#
# Blender is GPL; see tool_registry/tools/blender/BLENDER_NOTICE.md. The
# official blender.org build is installed unmodified and checked against its
# published SHA-256; its own licence files stay in /opt/blender. MetaForge's
# adapter runs it as a separate program and never imports bpy itself.

FROM ubuntu:24.04 AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY tool_registry/tools/blender/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /build/requirements.txt

# -- Stage 2: Blender from blender.org, verified ------------------------------
FROM ubuntu:24.04 AS blender

ARG BLENDER_VERSION=5.2.2
ARG BLENDER_SERIES=5.2
ARG BLENDER_SHA256=84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/blender.tar.xz \
        "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz

# -- Stage 3: runtime ----------------------------------------------------------
FROM ubuntu:24.04 AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="Blender 5.2 LTS headless render adapter for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/FidelOdok/MetaForge"
LABEL org.metaforge.blender.version="5.2.2"
LABEL org.metaforge.blender.license="GPL-2.0-or-later (distributed as GPL-3.0)"
LABEL org.metaforge.blender.source="https://download.blender.org/source/blender-5.2.2.tar.xz"

WORKDIR /app

# Libraries the official Linux build loads even in --background mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        ca-certificates \
        libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxext6 \
        libxkbcommon0 libsm6 libice6 libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=blender /opt/blender /opt/blender
RUN ln -s /opt/blender/blender /usr/local/bin/blender && blender --version

COPY --from=deps /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=deps /usr/local/bin /usr/local/bin

COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/
COPY tool_registry/tools/blender/BLENDER_NOTICE.md /opt/blender/METAFORGE_NOTICE.md

# ubuntu:24.04 ships a "ubuntu" user and group at uid/gid 1000; remove them
# so metaforge can take 1000 like the other adapter images.
RUN mkdir -p /workspace \
    && (userdel -r ubuntu 2>/dev/null || true) \
    && groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV BLENDER_WORK_DIR=/workspace
ENV BLENDER_BINARY=blender
ENV MCP_PORT=8202

WORKDIR /workspace

EXPOSE 8202

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://localhost:8202/health')" || exit 1

ENTRYPOINT ["python3.12", "-m", "tool_registry.tools.blender.entrypoint"]
