# MetaForge FreeCAD Adapter — single-interpreter build
# Usage:  docker build -f tool_registry/tools/freecad/Dockerfile -t metaforge/freecad-adapter .
#
# Provides FreeCAD headless (freecadcmd) for CAD export, mesh generation,
# boolean operations, parametric/PartDesign authoring, assemblies, and geometry
# property extraction.
# Note: FreeCAD images are larger (~2-3GB) due to OpenCASCADE dependencies.
#
# CRITICAL — interpreter alignment: the FreeCAD module (``FreeCAD.so``) is a
# compiled extension built for Debian's *system* python3 and installed under
# ``/usr/lib/freecad-python3/lib``. It can only be imported by that interpreter,
# with that directory on ``PYTHONPATH``. So the adapter MUST run under
# ``/usr/bin/python3`` (not a python.org base image), and its pip deps must be
# installed for that same interpreter. An earlier multi-stage build ran the
# adapter under python.org python3.11 while FreeCAD targeted Debian python3.13 —
# ``import FreeCAD`` failed at runtime (every tool → FreecadNotAvailableError)
# even though ``freecadcmd`` worked. This single-interpreter build fixes that.

FROM debian:trixie-slim AS runtime

LABEL maintainer="MetaForge <team@metaforge.dev>"
LABEL version="0.2.0"
LABEL description="FreeCAD headless adapter for MetaForge MCP tool registry"
LABEL org.opencontainers.image.source="https://github.com/MetaForge-HA/MetaForge"

WORKDIR /app

# FreeCAD headless + OpenCASCADE + the system python3 it targets, plus pip so we
# can install the adapter's Python deps for that *same* interpreter.
RUN apt-get update && apt-get install -y --no-install-recommends \
        # FreeCAD headless (provides freecadcmd + /usr/lib/freecad-python3/lib/FreeCAD.so)
        freecad \
        # Meshing libraries used by FreeCAD FEM workbench
        gmsh \
        netgen \
        # System python3 (the interpreter FreeCAD.so is built for) + pip
        python3 \
        python3-pip \
        # Required runtime libraries for OpenCASCADE
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install the adapter's Python deps for the SYSTEM python3 (the one that can
# import FreeCAD). --break-system-packages is safe + required inside the
# container (PEP-668 externally-managed marker).
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    "pydantic>=2.0" \
    "structlog>=24.0" \
    "opentelemetry-api>=1.20" \
    "aiohttp>=3.9"

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
# /app for the adapter package; the freecad lib dir so `import FreeCAD` resolves;
# the Mod dirs so the workbench modules (Sketcher, PartDesign, ...) import and
# their object types (`PartDesign::Body`, `Sketcher::SketchObject`) register —
# freecadcmd auto-loads these, but a plain `python3 entrypoint.py` does not.
ENV PYTHONPATH=/app:/usr/lib/freecad-python3/lib:/usr/lib/freecad/Mod:/usr/share/freecad/Mod
ENV METAFORGE_ENV=docker
ENV FREECAD_WORK_DIR=/workspace
# Disable FreeCAD GUI initialization in headless mode
ENV QT_QPA_PLATFORM=offscreen

WORKDIR /workspace

# Health = the HTTP MCP server is up AND the interpreter can import FreeCAD (the
# real failure mode that MET-527 fixed) — both, so a regression on either fails.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD /usr/bin/python3 -c "import FreeCAD, urllib.request; \
        urllib.request.urlopen('http://localhost:${FREECAD_HTTP_PORT:-8102}/health')" || exit 1

# Run under the system python3 (FreeCAD-capable), NOT a python.org interpreter.
ENTRYPOINT ["/usr/bin/python3", "/app/entrypoint.py"]
