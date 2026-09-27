# Gateway API Reference

The complete HTTP API for the **MetaForge Gateway** — the front door consumed by
the CLI, dashboard, and IDE assistants. This reference is generated from the
gateway's OpenAPI schema, so it always matches the running code.

**[Open the OpenAPI explorer →](/MetaForge/reference/openapi/)** — every route,
parameter, schema and example, rendered from the spec below.

:::note[Source of truth]

The explorer renders [`openapi.json`](https://fidelodok.github.io/MetaForge/reference/openapi.json),
produced by `python scripts/gen_openapi.py` from the live FastAPI app.
Regenerate and commit it whenever gateway routes or schemas change (see the
*update docs before merge* rule in `CLAUDE.md`). When the gateway is running
you can also hit the interactive docs directly at `/docs` (Swagger UI) and
`/redoc`.

:::

## Base URLs and the `/api` prefix

The gateway mounts its versioned routers at `/v1`. A request sent straight to
the gateway uses `/v1/...`; a request that goes through the dashboard's dev
proxy or an nginx in front of it uses `/api/v1/...`, because the extra segment
is what the proxy matches and strips. Health is unversioned, at `/health`.

```bash
export METAFORGE_GATEWAY="http://localhost:8000"

# Is the gateway up, and which auth mode is it in?
curl "$METAFORGE_GATEWAY/health"

# The project library
curl "$METAFORGE_GATEWAY/v1/projects"
```

See [Vercel deployment](../deployment/vercel.md) for the proxy rules and
[Gateway authentication](../deployment/cloud.md) for what `auth_mode` in the
health response means.

## Authentication

With `METAFORGE_AUTH_MODE=off` — the default, and the only mode a local
install needs — the data routes take no credential. With
`METAFORGE_AUTH_MODE=supabase` every route outside the public set requires a
bearer token and returns `401` without one. `GET /health` reports which mode is
active, so it is checkable at runtime rather than by reading config.

The harness credential routes are separate, and are guarded by
`METAFORGE_HARNESS_ADMIN_TOKEN` in both modes.
