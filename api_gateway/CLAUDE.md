# api_gateway

HTTP/WebSocket API server -- the "front door" for MetaForge. Built with FastAPI + Uvicorn. Exposes REST endpoints for the CLI, dashboard, and IDE extensions.

## Layer & Dependencies

- **Layer**: 4
- **May import from**: `orchestrator`, `domain_agents`, `twin_core`, `digital_twin`, `skill_registry`, `observability`, `pydantic`, standard library
- **Do NOT import from**: `cli`, `dashboard` (those are consumers, not dependencies)

## Key Files

- `server.py` -- `create_app()` FastAPI application factory
- `health.py` -- Health check endpoint
- `auth/` -- Authentication and authorization
- `middleware/` -- Request/response middleware (CORS, logging, etc.)
- `routes/` -- General API routes
- `assistant/` -- Assistant-mode endpoints (approval workflow)
  - `routes.py`, `schemas.py`, `approval.py`
- `chat/` -- Chat/conversation endpoints
  - `routes.py`, `schemas.py`, `models.py`, `activity.py`
- `sessions/` -- Session management endpoints
- `projects/` -- Project management endpoints
- `knowledge/` -- Knowledge retrieval endpoints
- `compliance/` -- Compliance check endpoints
- `convert/` -- Format conversion endpoints (AAS, SysML export)

## Testing

```bash
ruff check api_gateway/
mypy --strict api_gateway/
pytest tests/unit/test_api*.py tests/unit/test_gateway*.py -v
pytest tests/integration/test_api*.py -v --timeout=120
```

## Conventions

- All routes use Pydantic v2 request/response schemas
- Use dependency injection for services (Twin API, orchestrator, etc.)
- Never put business logic in route handlers -- delegate to service layers
- Add observability instrumentation (Logs, Metrics, Traces) to all endpoints — see [Observability Requirements in root CLAUDE.md](../CLAUDE.md#observability-requirements) for all 7 levels
- Manual verification: `uvicorn api_gateway.server:app` and test endpoints respond
