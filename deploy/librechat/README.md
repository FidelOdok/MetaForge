# LibreChat on MetaForge (MET-552)

LibreChat is MetaForge's flagship chat/agent surface. Its agents drive the
Digital Twin and tools over MCP; LibreChat renders tool calls, code, and
artifacts natively. MetaForge's embedded, entity-scoped chats (twin
node/part, approvals, BOM) stay as-is — this does not replace them.

## Why LibreChat (vs building it in-house)

MetaForge's bespoke chat (~2,930 LOC) is markdown-only with no tool-call /
thinking / inline-proposal rendering, and the harness discards its ReAct
trace. Reaching parity in-house is 5–10× the code. LibreChat (MIT) already
ships agents, tool-call rendering, a code interpreter, Code Artifacts, RAG,
and **native MCP** — so we point it at MetaForge's tools instead.

## How it connects

LibreChat → `mcp-http` sidecar at `http://mcp-http:8765/mcp` (a stateless,
spec-compliant **streamable-HTTP** MCP endpoint — `initialize` / `tools/list`
/ `tools/call`, 76 tools). Configured in [`librechat.yaml`](./librechat.yaml)
under `mcpServers.metaforge`. LibreChat calls LLM providers directly with its
own keys (MetaForge has no OpenAI-compatible endpoint).

## Deploy (fidel-dev)

Prereq: the core stack is up (so the `metaforge_metaforge` network exists and
`mcp-http` is reachable).

```bash
cd ~/MetaForge
cp deploy/librechat/.env.example deploy/librechat/.env
#  … fill in ANTHROPIC_API_KEY and the four LibreChat secrets …
docker compose -f docker-compose.librechat.yml up -d
# → http://<host>:3080  (self-register the first account)
```

## Verify the MCP seam (the crux)

1. Register / log in at `:3080`.
2. Agents → Agent Builder → **Add MCP Server Tools** → the `metaforge` server
   lists `twin.*` / `cadquery.*` / `freecad.*` / … (proves the handshake).
3. Ask an agent "get twin node `<id>`" → it calls `twin.get_node`; the tool
   call + result render inline.
4. Ask it to "propose …" → `twin.propose_change` files a `DesignChangeProposal`
   visible in the MetaForge dashboard `/approvals` and the in-twin card;
   Approve → applies (MET-548, PRs #429–#431).

## Notes

- **Two apps, two stores**: LibreChat uses its own MongoDB; MetaForge uses
  Postgres/Neo4j. SSO / deep-linking from a twin entity into a context-seeded
  LibreChat conversation is later work.
- The sidecar runs open on the internal network today. For any public
  exposure, set `METAFORGE_MCP_API_KEY` on the sidecar and the matching value
  in `.env`, then uncomment the `headers` block in `librechat.yaml`.
