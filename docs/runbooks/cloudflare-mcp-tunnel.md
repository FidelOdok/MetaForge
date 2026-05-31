# Runbook: Cloudflare Tunnel for the MCP HTTP Sidecar

Exposes the dev MCP HTTP sidecar (`mcp-http`, port `8765`) to **Claude cloud**
— the claude.ai web connector and remote/cloud Claude Code — over a stable,
public HTTPS hostname. No inbound ports are opened on the host; `cloudflared`
makes an outbound-only connection to Cloudflare's edge.

Tracking: **MET-482**. Builds on MET-479 (sidecar) and MET-338 (API-key auth).
OAuth for the claude.ai connector is separate (MET-480) — this runbook covers
the token-based static-key path.

## Architecture

```
Claude cloud ──HTTPS──► Cloudflare edge ──tunnel──► cloudflared ──http──► mcp-http:8765
                       (mcp.yourdomain.com)        (container)           (unified MCP)
```

`cloudflared` and `mcp-http` share the `metaforge` Docker network, so the
tunnel routes to `http://mcp-http:8765` by service name. Ingress (the
public-hostname → service mapping) lives in the Cloudflare dashboard against
the tunnel token — there is no local config file.

## One-time setup

### 1. Create the named tunnel (Cloudflare Zero Trust dashboard)

1. **Networks → Tunnels → Create a tunnel**, connector type **Cloudflared**.
2. Name it (e.g. `metaforge-mcp`) and save. Copy the **tunnel token** shown
   on the install step (the long `eyJ...` string after `--token`).
3. Under the tunnel's **Public Hostname** tab, add a route:
   - **Subdomain / Domain** → e.g. `mcp.yourdomain.com` (the domain must be
     on your Cloudflare account).
   - **Service** → Type `HTTP`, URL `mcp-http:8765`.
4. Save. Cloudflare provisions the DNS record automatically.

### 2. Configure secrets in `.env`

```bash
# A strong key — clients send it as `Authorization: Bearer <key>`.
METAFORGE_MCP_API_KEY=<generate a long random string>
# The tunnel token from step 1.2.
CLOUDFLARE_TUNNEL_TOKEN=eyJ...
```

> Set `METAFORGE_MCP_API_KEY` **before** the tunnel goes live. Empty means the
> sidecar runs in open mode — fine on the LAN, not fine on the public internet.

### 3. Bring it up

```bash
docker compose up -d mcp-http cloudflared
docker compose logs -f cloudflared      # expect "Registered tunnel connection" x4
```

A healthy connector logs four `Registered tunnel connection` lines (one per
Cloudflare edge colo). `curl https://mcp.yourdomain.com/mcp` should now reach
the sidecar (a `401` means auth is enforced and working — see below).

## Connecting Claude

### Claude Code (remote)

Point an MCP entry at the public URL with the bearer header:

```json
{
  "mcpServers": {
    "metaforge": {
      "type": "http",
      "url": "https://mcp.yourdomain.com/mcp",
      "headers": { "Authorization": "Bearer <METAFORGE_MCP_API_KEY>" }
    }
  }
}
```

(The committed `.mcp.json` keeps the LAN URL `http://fidel-dev:8765/mcp` for
local dev — use the public URL only in environments that need it.)

### claude.ai web connector

The web connector UI does not accept static bearer tokens — it requires
OAuth 2.1 + PKCE, tracked in **MET-480**. Until that lands, use the public
hostname from remote Claude Code (above), not the claude.ai connector.

## Verification

```bash
# Unauthenticated request is rejected when the key is set:
curl -s -o /dev/null -w '%{http_code}\n' https://mcp.yourdomain.com/mcp
# => 401

# Authenticated MCP initialize handshake succeeds:
curl -s https://mcp.yourdomain.com/mcp \
  -H "Authorization: Bearer $METAFORGE_MCP_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
# => a JSON-RPC result with serverInfo
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `cloudflared` exits immediately | Missing/invalid `CLOUDFLARE_TUNNEL_TOKEN` | Re-copy the token; ensure `.env` is loaded (`docker compose config`). |
| Public URL returns 502 | `mcp-http` not up, or ingress points to the wrong service | Confirm `docker compose ps mcp-http`; ingress URL must be `mcp-http:8765`. |
| Public URL returns 401 | API key required and not sent / mismatched | Send `Authorization: Bearer <METAFORGE_MCP_API_KEY>`. |
| No `Registered tunnel connection` logs | Egress blocked to Cloudflare edge (7844/tcp+udp) | Allow outbound to `*.cftunnel.com`; tunnel falls back to HTTP/2 if QUIC is blocked. |
| DNS doesn't resolve | Public Hostname route not saved | Re-add the route; Cloudflare creates the CNAME on save. |

## Teardown

```bash
docker compose stop cloudflared       # stop exposing; mcp-http stays up
```

To retire the tunnel entirely, delete it in the Cloudflare dashboard and clear
`CLOUDFLARE_TUNNEL_TOKEN` from `.env`.
