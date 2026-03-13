# MetaForge Gateway — multi-stage build
# Usage:  docker build -t metaforge-gateway .
#         docker run -p 8000:8000 metaforge-gateway

# ── Stage 1: dependencies ────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /app

# Install system deps for potential C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install runtime dependencies only (no dev extras)
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir uvicorn[standard]

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source
COPY api_gateway/ ./api_gateway/
COPY orchestrator/ ./orchestrator/
COPY twin_core/ ./twin_core/
COPY digital_twin/ ./digital_twin/
COPY shared/ ./shared/
COPY skill_registry/ ./skill_registry/
COPY mcp_core/ ./mcp_core/
COPY tool_registry/ ./tool_registry/
COPY domain_agents/ ./domain_agents/
COPY observability/ ./observability/
COPY pyproject.toml ./

# Install the package itself (editable not needed in container)
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1
ENV METAFORGE_ENV=docker

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "api_gateway.server:app", "--host", "0.0.0.0", "--port", "8000"]
