# MetaForge FreeCAD Adapter — multi-stage build
# Usage:  docker build -f tool_registry/tools/freecad/Dockerfile -t metaforge/freecad-adapter .
#
# Provides FreeCAD headless (freecadcmd) for CAD export, mesh generation,
# boolean operations, and geometry property extraction.
# Note: FreeCAD images are larger (~1-2GB) due to OpenCASCADE dependencies.

# ── Stage 1: dependencies ────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# Install system deps needed for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install minimal Python dependencies for the adapter
RUN pip install --no-cache-dir \
    pydantic>=2.0 \
    structlog>=24.0 \
    opentelemetry-api>=1.20

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.1.0"
LABEL description="FreeCAD headless adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app

# Install FreeCAD headless and OpenCASCADE dependencies
# FreeCAD pulls in libocct (OpenCASCADE) automatically as a dependency.
# We use --no-install-recommends to skip GUI packages where possible.
RUN apt-get update && apt-get install -y --no-install-recommends \
        # FreeCAD headless (provides freecadcmd binary)
        freecad \
        # Meshing libraries used by FreeCAD FEM workbench
        gmsh \
        netgen \
        # Required runtime libraries for OpenCASCADE
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy adapter source code (only what the adapter needs)
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY observability/ ./observability/

# Copy entrypoint
COPY tool_registry/tools/freecad/entrypoint.py ./entrypoint.py

# Create working directory for FreeCAD operations
RUN mkdir -p /workspace /tmp/freecad

# Create non-root user for security
RUN groupadd --gid 1000 metaforge \
    && useradd --uid 1000 --gid metaforge --shell /bin/bash --create-home metaforge \
    && chown -R metaforge:metaforge /workspace /tmp/freecad /app

USER metaforge

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV METAFORGE_ENV=docker
ENV FREECAD_WORK_DIR=/workspace
# Disable FreeCAD GUI initialization in headless mode
ENV QT_QPA_PLATFORM=offscreen

WORKDIR /workspace

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD freecadcmd --version || python3 -c "import FreeCAD; print(FreeCAD.Version())" || exit 1

ENTRYPOINT ["python", "/app/entrypoint.py"]
