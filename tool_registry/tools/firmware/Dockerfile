# MetaForge firmware adapter: PlatformIO builds, Renode and Espressif QEMU
# emulation, behind MCP over HTTP (FORGE-674, FORGE-677).
# Usage:  docker build -f tool_registry/tools/firmware/Dockerfile -t metaforge/firmware-adapter .
#
# Every toolchain is fetched here, at image build, so a firmware.build or a
# firmware.run_emulated needs no network: the seed projects make PlatformIO
# install the STM32 and ESP32 platforms, their compilers and frameworks, and
# each Renode board is loaded once so the SVD file Renode downloads for it is
# in Renode's cache. Renode (MIT) and Espressif's QEMU fork (GPL-2.0) run as
# separate programs; nothing here links them.
#
# The toolchains are installed by the runtime user into directories it
# already owns, so no layer re-copies them to change ownership.

FROM python:3.11-slim AS deps
RUN pip install --no-cache-dir \
    "pydantic>=2.0" \
    "structlog>=24.0" \
    "opentelemetry-api>=1.20" \
    "aiohttp>=3.9"

FROM python:3.11-slim AS runtime
LABEL description="Firmware build (PlatformIO) and emulation (Renode, Espressif QEMU) adapter for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

ARG PLATFORMIO_VERSION=6.2.0
ARG STSTM32_VERSION=20.0.0
ARG ESPRESSIF32_VERSION=7.1.3
ARG RENODE_VERSION=1.17.0
ARG ESP_QEMU_TAG=esp-develop-9.2.2-20260417
ARG ESP_QEMU_BUILD=esp_develop_9.2.2_20260417
ARG FREERTOS_KERNEL_VERSION=V11.3.1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git xz-utils \
        libglib2.0-0 libpixman-1-0 libslirp0 libsdl2-2.0-0 libgcrypt20 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && mkdir -p /opt/platformio /workspace /app \
    && chown metaforge:metaforge /opt/platformio /workspace /app

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
RUN pip install --no-cache-dir "platformio==${PLATFORMIO_VERSION}"

# Renode (the portable build carries its own .NET runtime). Owned by the
# runtime user in the same layer it is unpacked in.
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
RUN curl -fsSL "https://github.com/renode/renode/releases/download/v${RENODE_VERSION}/renode-${RENODE_VERSION}.linux-portable.tar.gz" \
        | tar -xz -C /opt \
    && mv /opt/renode_${RENODE_VERSION}* /opt/renode \
    && chown -R metaforge:metaforge /opt/renode \
    && ln -s /opt/renode/renode /usr/local/bin/renode

# Espressif's QEMU fork, xtensa build (the ESP32).
RUN mkdir -p /opt/esp-qemu \
    && curl -fsSL "https://github.com/espressif/qemu/releases/download/${ESP_QEMU_TAG}/qemu-xtensa-softmmu-${ESP_QEMU_BUILD}-x86_64-linux-gnu.tar.xz" \
        | tar -xJ -C /opt/esp-qemu \
    && ln -s /opt/esp-qemu/qemu/bin/qemu-system-xtensa /usr/local/bin/qemu-system-xtensa

# FreeRTOS kernel sources, compiled into a build whose project carries a
# FreeRTOSConfig.h (the STM32Cube framework does not build FreeRTOS itself).
RUN git clone --depth 1 --branch "${FREERTOS_KERNEL_VERSION}" \
        https://github.com/FreeRTOS/FreeRTOS-Kernel.git /opt/freertos-kernel \
    && rm -rf /opt/freertos-kernel/.git

USER metaforge

# PlatformIO's platforms, installed by building the seed projects. Telemetry
# off, and the upgrade check pushed out ten years: at its default PlatformIO
# checks for an upgrade after a build, and with no network that waits about a
# minute on every call.
ENV PLATFORMIO_CORE_DIR=/opt/platformio \
    PLATFORMIO_SETTING_ENABLE_TELEMETRY=No \
    PLATFORMIO_SETTING_CHECK_PLATFORMIO_INTERVAL=3650 \
    PLATFORMIO_SETTING_CHECK_PRUNE_SYSTEM_THRESHOLD=0 \
    STSTM32_VERSION=${STSTM32_VERSION} \
    ESPRESSIF32_VERSION=${ESPRESSIF32_VERSION}
COPY --chown=metaforge:metaforge tool_registry/tools/firmware/seed/ /tmp/seed/
RUN sed -i "s/^platform = ststm32$/platform = platformio\/ststm32@${STSTM32_VERSION}/" /tmp/seed/stm32/platformio.ini \
    && sed -i "s/^platform = espressif32$/platform = platformio\/espressif32@${ESPRESSIF32_VERSION}/" /tmp/seed/esp32/platformio.ini \
    && pio run -d /tmp/seed/stm32 \
    && pio run -d /tmp/seed/esp32 \
    && rm -rf /tmp/seed "${PLATFORMIO_CORE_DIR}/.cache"

# Load every board firmware.run_emulated maps (adapter.RENODE_BOARDS) once, so
# the SVD files Renode downloads for them are cached in the image.
RUN printf '%s\n' \
        'mach create "a"' 'machine LoadPlatformDescription @platforms/boards/stm32f4_discovery-kit.repl' \
        'mach create "b"' 'machine LoadPlatformDescription @platforms/boards/nucleo_f767zi.repl' \
        'mach create "c"' 'machine LoadPlatformDescription @platforms/boards/nucleo_h753zi.repl' \
        'quit' > /tmp/warm.resc \
    && renode --disable-gui --plain --console /tmp/warm.resc \
    && rm /tmp/warm.resc

WORKDIR /app
COPY --chown=metaforge:metaforge mcp_core/ ./mcp_core/
COPY --chown=metaforge:metaforge tool_registry/ ./tool_registry/
COPY --chown=metaforge:metaforge observability/ ./observability/
COPY --chown=metaforge:metaforge tool_registry/tools/firmware/entrypoint.py ./entrypoint.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    METAFORGE_ENV=docker \
    FIRMWARE_WORK_DIR=/workspace \
    FIRMWARE_HTTP_PORT=8105 \
    FREERTOS_KERNEL_DIR=/opt/freertos-kernel

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8105/health')" || exit 1
ENTRYPOINT ["python", "/app/entrypoint.py"]
