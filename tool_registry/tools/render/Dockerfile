# MetaForge render adapter: model views of a GLB in headless Chromium (Playwright)
# Usage:  docker build -f tool_registry/tools/render/Dockerfile -t metaforge/render-adapter .
#
# The base is Microsoft's Playwright image for Python, pinned to an exact
# version and digest: it carries Chromium and every system library it needs.
# three.js (r169) is fetched from the npm registry at build time, checked
# against its SHA-256 and kept in the image, so a render needs no network.

FROM mcr.microsoft.com/playwright/python:v1.49.1-noble@sha256:42e251ec3238672c931be6c3565ff279690ae072c3e22058128d0412494f6866

LABEL description="Browser render adapter (three.js on Chromium) for the MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

ARG THREE_VERSION=0.169.0
ARG THREE_SHA256=c86d0937570fb425d981e156ddf919c43dbc45d4e91fd7c8dd0de56723b3ec71

WORKDIR /app
RUN pip install --no-cache-dir --break-system-packages \
    "playwright==1.49.1" \
    "pydantic>=2.0" \
    "structlog>=24.0" \
    "opentelemetry-api>=1.20" \
    "aiohttp>=3.9"

# three.js: only build/ and examples/jsm/ are served; the rest is dropped.
RUN mkdir -p /app/vendor \
    && python3 -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/three.tgz')" \
        "https://registry.npmjs.org/three/-/three-${THREE_VERSION}.tgz" \
    && echo "${THREE_SHA256}  /tmp/three.tgz" | sha256sum -c - \
    && tar -xzf /tmp/three.tgz -C /app/vendor \
    && mv /app/vendor/package /app/vendor/three \
    && rm -rf /app/vendor/three/src /app/vendor/three/examples/jsm/libs/basis \
        /app/vendor/three/examples/jsm/libs/draco /tmp/three.tgz \
    && test -f /app/vendor/three/build/three.module.js \
    && test -f /app/vendor/three/examples/jsm/loaders/GLTFLoader.js

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
COPY tool_registry/tools/render/ ./tool_registry/tools/render/
COPY tool_registry/tools/render/entrypoint.py ./entrypoint.py

RUN mkdir -p /workspace && chown -R pwuser:pwuser /workspace /app
USER pwuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    METAFORGE_ENV=docker \
    RENDER_WORK_DIR=/workspace \
    RENDER_THREE_DIR=/app/vendor/three \
    RENDER_HTTP_PORT=8107 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8107/health')" || exit 1
ENTRYPOINT ["python3", "/app/entrypoint.py"]
