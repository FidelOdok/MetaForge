# Hosting the dashboard on Vercel

MetaForge is local-first: the gateway, the agents and the tool containers run on
your own machine. The dashboard is only a client, so it can be hosted anywhere —
including Vercel — and pointed back at whatever gateway you are running.

This page covers one Vercel project, built from `dashboard/` and suited to a
domain such as `app.example.com`.

## Why the dashboard needs configuring at all

The dashboard's API client used to hardcode a *relative* base of `/api/v1` and
rely on something in front of it rewriting that to a gateway:

- in development, the Vite proxy in `dashboard/vite.config.ts`
- in the Docker image, `location /api/` in `dashboard/nginx.conf`

A static host has no such proxy. The browser has to call the gateway directly, at
an address only you know — so the base is resolved at runtime instead, in this
order:

1. the gateway you saved in **Settings → Gateway** (kept in `localStorage`)
2. `VITE_GATEWAY_URL`, baked in at build time
3. nothing configured — the original relative `/api/v1`

Step 3 is what keeps `npm run dev` and the Docker image working unchanged.

:::warning[The `/api` prefix is a proxy artefact, not a gateway path]

The gateway mounts its versioned routers at `/v1` (`APIRouter(prefix="/v1/…")`
throughout `api_gateway/`). The extra `/api` segment exists only so the dev
proxy and nginx have something to match and strip. Requests that go *through*
a proxy must say `/api/v1/x`; requests sent *straight to* the gateway must say
`/v1/x`. The dashboard handles this for you — `apiBase()` in
`dashboard/src/lib/gatewayConfig.ts` picks the right one — but it matters if
you are testing routes by hand with `curl`.

:::

## Deploying

### 1. Create the project

Create a Vercel project from this repository and set **Root Directory** to
`dashboard`. Its `vercel.json` supplies the build command, output directory and
headers, and carries the SPA rewrite that keeps deep links such as `/twin` and
`/settings` working on reload.

### 2. Set environment variables

| Variable | Value |
|----------|-------|
| `VITE_GATEWAY_URL` | *Optional.* A default gateway for first-time visitors. Leave unset to have them configure their own. |

This is a build-time variable — Vite inlines it — so changing it needs a
redeploy.

### 3. Point the dashboard at your gateway

Open the deployed dashboard, go to **Settings → Gateway**, enter the address and
port, and use **Test connection** to confirm it before saving. The value is stored
in that browser only; nothing is sent to Vercel, and each person using the
dashboard configures their own.

## Reaching your gateway from an HTTPS page

This is the part that bites. A page served over HTTPS cannot freely call a plain
HTTP endpoint — browsers block it as mixed content. The exception is loopback:

| Gateway address | Chrome | Firefox / Safari |
|-----------------|--------|------------------|
| `https://…` | works | works |
| `http://localhost:8000` | works — loopback is treated as potentially trustworthy | **blocked** |
| `http://192.168.1.50:8000` | **blocked** | **blocked** |

So a hosted dashboard needs the gateway reachable over HTTPS for anything but
Chrome-against-localhost. Two practical options:

- **Tailscale** — put the gateway machine on your tailnet and use its
  `*.ts.net` name, which comes with a real certificate.
- **Cloudflare Tunnel** — expose the gateway through a tunnel. There is an
  existing runbook for the MCP sidecar —
  [`docs/runbooks/cloudflare-mcp-tunnel.md`](https://github.com/FidelOdok/MetaForge/blob/main/docs/runbooks/cloudflare-mcp-tunnel.md)
  — that the same approach follows.

The Settings page detects the mixed-content case and says so, rather than leaving
you with an unexplained network error.

CORS is not an obstacle for a default local gateway: `create_app` leaves
`allow_origins` at `["*"]`, so a browser on any origin may call it. A gateway
with authentication switched on refuses that wildcard — see
[Gateway authentication](authentication.md).

## Security: read this before exposing a gateway

:::danger[A default gateway has no authentication on its data routes]

With `METAFORGE_AUTH_MODE=off`, which is the default, only the harness
credential routes are guarded — by `METAFORGE_HARNESS_ADMIN_TOKEN`. Everything
else (the digital twin, projects, chat, file download) is open to anything that
can reach the port. Combined with `allow_origins=["*"]`, a gateway on a public
address is readable and writable by anyone who finds it.

Do not put one on the open internet. Use a private network (Tailscale) or an
authenticating proxy (Cloudflare Access, an OAuth2 proxy) in front of it. Both
also give you the HTTPS endpoint the section above requires.

:::

The gateway *can* authenticate callers itself, by installing an auth provider
and naming it in `METAFORGE_AUTH_MODE`. That is a plug-in seam rather than a
built-in: see [Gateway authentication](authentication.md) for the contract, and for the
dashboard side of it.

## What is *not* deployed to Vercel

- **The gateway, orchestrator, agents and tool containers.** They stay on your
  machine. Vercel serves static assets only.
- **RUM telemetry.** Faro and OpenTelemetry ship to `/faro` and `/otlp`, paths
  that exist only behind the Docker image's nginx. RUM is therefore off unless
  `VITE_RUM_ENABLED=true` is set at build time, which `dashboard/Dockerfile` does
  for that image. Leave it unset on Vercel — switching it on there produces two
  404s per page load and no telemetry.

## Local preview

```bash
cd dashboard && npm install && npm run dev    # http://localhost:5173
```

To check a production build the way Vercel serves it:

```bash
cd dashboard && npm run build && npm run preview
```
