# MCP HTTP Sidecar

The dev MetaForge MCP server runs as a sidecar service (`mcp-http`) in `docker-compose.override.yml`, not embedded in the gateway process. It listens on `0.0.0.0:8765`, reuses the gateway image, mounts the same source tree for hot-reload, and shares the `metaforge` docker network so it can reach `postgres` + `neo4j`.

An MCP client (Claude Code, Cursor, LibreChat, Claude Desktop) connects over HTTP transport, e.g. in `.mcp.json`:

```json
{"mcpServers": {"metaforge": {"transport": "http", "url": "http://<host>:8765/mcp"}}}
```

`POST /mcp` is a stateless, spec-compliant streamable-HTTP MCP endpoint — `initialize`, `tools/list`, `tools/call` (standard MCP method names, implemented in `metaforge/mcp/server.py` alongside legacy `tool/list`/`tool/call`). No `Mcp-Session-Id` needed. Third-party clients should use `type: streamable-http` against `/mcp` — NOT the bespoke `/mcp/sse` endpoint, which is a custom query-param stream (`?request=<urlencoded JSON>`), not a standard MCP transport handshake.

**Why it's a sidecar and not embedded**: Anthropic's MCP guidance is HTTP for remote, stdio for local-only. An earlier SSH-piped stdio bridge worked but had no reconnect and no observability.

**How to apply:**
- If MCP reconnection fails, first check the sidecar is up (`docker compose ps mcp-http`).
- Restart after compose-file edits with `--force-recreate` — plain `restart` won't pick up compose changes.
- Adapter env (Postgres/Neo4j credentials) should come from the host's `.env`, never literal fallbacks committed to the override file.
- `auth_enforced=False` is fine for an internal dev network; production deploys need `METAFORGE_MCP_API_KEY`.
- digikey/mouser/nexar tools self-skip registration when their credentials aren't present in env — a missing tool there is expected, not a bug.

Related: [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md) — the sidecar itself doesn't ship native CAD/FEA runtimes.
