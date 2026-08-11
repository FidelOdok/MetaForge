# Gateway Service

The HTTP/WebSocket "front door" — `api_gateway/`.

- **Entry point**: `api_gateway/server.py` (~45KB) — FastAPI app factory. Run via `uvicorn api_gateway.server:app`.
- Wires roughly 14 routers: assistant, bom, cad, chat, compliance, constraint, convert, harness, knowledge, memory, projects, runs, sessions, twin.
- Each domain gets its own `routes.py` under `api_gateway/<domain>/` (e.g. `api_gateway/twin/routes.py`, `api_gateway/chat/routes.py`).
- OTel/observability bootstrap happens at startup here too — see [Observability Stack](observability-stack.md).
- Health check: `api_gateway/health.py`.
- **When you add or change a route or response schema**: regenerate `docs/reference/openapi.json` via `python scripts/gen_openapi.py` in the same PR. This is a hard CLAUDE.md rule — the published API reference at `docs/reference/gateway-api.md` renders from that generated file, so skipping the regen means the published docs silently drift from the real API.

See `docs/architecture.md` for the full request-flow diagram (Gateway → Orchestrator → Domain Agents → Skills → MCP → Tool Adapters → Twin).
