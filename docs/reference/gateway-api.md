# Gateway API Reference

The complete HTTP API for the **MetaForge Gateway** — the front door consumed by
the CLI, dashboard, and IDE assistants. This reference is generated from the
gateway's OpenAPI schema, so it always matches the running code.

!!! note "Source of truth"
    This page renders [`openapi.json`](openapi.json), produced by
    `python scripts/gen_openapi.py` from the live FastAPI app. Regenerate and
    commit it whenever gateway routes or schemas change (see the *update docs
    before merge* rule in `CLAUDE.md`). When the gateway is running you can also
    hit the interactive docs directly at `/docs` (Swagger UI) and `/redoc`.

<swagger-ui src="openapi.json"/>
