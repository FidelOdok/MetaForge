---
title: Connect a hosted harness
---

# Connect a hosted harness to your own gateway

claude.ai and ChatGPT run in someone else's datacentre. They cannot reach
`localhost`, so a gateway on your machine is invisible to them — which is the
only reason any of this is more involved than pointing a client at a port.

**Your design data does not move.** The twin, your files and the tool adapters
stay on your machine. What this page sets up is a way for a hosted harness to
*reach* them, under a credential you control and can withdraw.

If you use a local harness instead — Claude Code or the Codex CLI on the same
machine — none of this applies. Install the `metaforge-local` plugin and it
talks to a local process over stdio, with no network, no account and no
hostname. That is the simpler path and most people should take it.

## What you need

- A gateway running on your machine, with the MCP sidecar on `:8765`
- A tunnel client — [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
  or [`ngrok`](https://ngrok.com/download). Deliberately not bundled: a binary
  that opens a public hostname is something you should choose to have

## 1. Close the sidecar before you open it

This is the step worth not skipping. An unauthenticated sidecar is fine on a
laptop and is an **unauthenticated write endpoint** once it has a public
hostname.

```bash
# In .env, next to your gateway
METAFORGE_MCP_API_KEY=$(openssl rand -base64 36 | tr -d '\n/+=' | cut -c1-40)
```

Restart the sidecar — *recreate* it rather than `docker compose restart`, which
does not re-read the environment:

```bash
docker compose up -d --no-deps --force-recreate mcp-http
```

Confirm it is actually enforcing, rather than assuming:

```console
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8765/mcp \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}'
401
```

A `200` there means it is still open. Do not continue.

## 2. Open the tunnel

```bash
forge tunnel up
```

`forge tunnel` refuses rather than warns when the gateway is not fit to be
public: nothing answering, something answering that is not a MetaForge gateway
("tunnelling it would publish somebody else's service"), or a gateway that
accepts unauthenticated connections. It then hands over to whichever tunnel
client you have installed and prints the public hostname.

You can also run the tunnel yourself — `forge tunnel` adds the pre-flight, not
the tunnel.

## 3. Add it to the harness

### Claude Code (remote)

Claude Code can send a static bearer token, so it needs nothing else:

```json
{
  "mcpServers": {
    "metaforge": {
      "type": "http",
      "url": "https://<your-hostname>/mcp",
      "headers": { "Authorization": "Bearer <METAFORGE_MCP_API_KEY>" }
    }
  }
}
```

Or install the plugin, which brings the tools, the slash commands, the
agents, the hooks and the engineering skills together, and asks for the gateway
URL on first use:

```
/plugin marketplace add FidelOdok/MetaForge
/plugin install metaforge
```

The plugin has no token field (FORGE-659). When the gateway needs a login it
answers 401, Claude Code flags `metaforge` in `/mcp`, and you sign in there with
OAuth: run `/mcp`, choose `metaforge`, and follow the browser prompt. This needs
`METAFORGE_OAUTH_LOGIN_SECRET` set on the gateway, as for claude.ai below.

The reason there is no header: Claude Code does not fall back to OAuth once a
configured `Authorization` header is rejected, it reports the connection as
failed. A plugin that always sent a bearer header would therefore never offer
the sign-in. A gateway with only `METAFORGE_MCP_API_KEY` has no OAuth to offer,
so for that case skip the plugin's connector and add the server yourself with
the JSON above, or:

```bash
claude mcp add --transport http metaforge https://<your-hostname>/mcp \
  --header "Authorization: Bearer $METAFORGE_MCP_API_KEY"
```

### claude.ai

The web connector **cannot send a static bearer token** — it runs OAuth 2.1 +
PKCE. Set the shared secret its sign-in page will ask for, and restart:

```bash
METAFORGE_OAUTH_LOGIN_SECRET=$(openssl rand -base64 18 | tr -d '\n/+=' | cut -c1-20)
```

Then **Settings → Connectors → Add custom connector**, and enter
`https://<your-hostname>/mcp`. Nothing else: claude.ai reads the `401`'s
`WWW-Authenticate` header, follows it to the metadata, registers itself, and
sends you to the sign-in page. Enter the secret and it completes the exchange.

With `DATABASE_URL` set (the standard compose file sets it), registered
clients and issued tokens are kept in Postgres (`mcp_oauth_clients`,
`mcp_oauth_tokens`, tokens by their SHA-256 only), so redeploying or
restarting the sidecar does not sign anyone out (FORGE-697). Without a
database they are held in memory, and after a restart a client must sign in
again; a client that keeps reusing its old registration, as Claude Code does,
then needs that registration cleared before it can.

You do **not** need to set `METAFORGE_OAUTH_ISSUER`. The server works out its
own public URL from the headers the tunnel sets. Pinning it to a quick tunnel's
hostname is actively worse, because that hostname changes on the next restart.

## What you get, and what you do not

A **connector** carries the MCP server: **tools**, and the slash commands, which
are exposed as MCP **prompts**. It does not carry skills, agents or hooks —
there is no MCP method that would deliver them.

A **plugin bundle** is a different mechanism and does carry more. ChatGPT
installs one from an uploaded `.zip` or `.tar.gz` holding a `plugin.json`, an
`mcp.json` and a `skills/` directory — so all 43 engineering skills travel with
it. Build one with:

Every release attaches the bundle, so the normal route is to download
`metaforge-plugin-<version>.zip` (or `.tar.gz`) from the
[latest release](https://github.com/FidelOdok/MetaForge/releases/latest) rather
than to build one.

Build it yourself only when you need a different server address:

```bash
python scripts/package_plugin_bundle.py --gateway-url https://your-hostname/mcp
```

:::warning[A released bundle points at `localhost`]

Each user runs their own gateway, so there is no address a published bundle
could correctly bake in — it ships the local default. ChatGPT cloud runs in
someone else's datacentre and cannot reach `localhost`, so a bundle uploaded
unmodified installs and then connects to nothing, which presents as the plugin
being broken rather than as a configuration gap. Rebuild with `--gateway-url`,
or edit `mcp.json` inside the archive before uploading.

:::

So the honest summary is per-mechanism, not per-product: a connector is tools
and prompts; a bundle is tools, prompts and skills; only Claude Code's own
plugin format carries agents and hooks as well.

### The hosted-gateway package, `metaforge-cloud` (FORGE-658, FORGE-661)

`metaforge-cloud` is `metaforge` with the hosted gateway's address written in as
a literal, so there is nothing to enter. It carries the same tools, skills,
commands, agents and hooks. The connector is the bare address with no `?profile=`
query and no `${user_config.*}` reference, and no bearer header.

It is not in the `FidelOdok/MetaForge` marketplace, which catalogs the two
open-core packages. This one belongs to the hosted service, since the only
thing it adds is that service's address — <https://www.metaforge.uk> says how
to get it.

Once it is installed, run `/mcp` in Claude Code, choose `metaforge`, and sign in
with the access secret. The gateway needs `METAFORGE_OAUTH_LOGIN_SECRET` set, as
above.

Install one MetaForge package, not several: the manifest has no way to switch a
connector off, so two would offer every tool twice.

**This is an experiment (FORGE-661).** FORGE-660 had removed the address so that
none of ours was in the public repository. But claude.ai chat ignores a connector
whose URL has a `${user_config.*}` reference, and a custom connector at the same
address showed **Connected** while the plugin's own row did not recognise it. The
literal is back to find out whether claude.ai matches a plugin's connector to one
on the account by address. If it does not help, the FORGE-660 form (address from
user configuration) is the one to restore.

### Connecting on claude.ai

Anthropic's plugin support table says claude.ai chat **ignores** an MCP server
whose URL contains a `${user_config.*}` reference. `metaforge` writes its URL that
way, so it has no connector there; there is no template to edit and nothing to
connect. (An earlier version of this page said the template was shown for you to
replace. That was wrong.) `metaforge-cloud` has a literal address, so its
connector is listed on the plugin's **Connectors** tab.

Adding a plugin does not add or sign in its connector, so the row starts at
**Not added**: choose **Add**, then **Connect**. If that cannot be done, add a
custom connector under **Customize → Connectors** with the same address. Type the
address with no punctuation after it: a stray backtick or quote copied from a
document makes it an invalid URL. The custom connector is the route verified end to
end against the hosted gateway. Your gateway needs `METAFORGE_OAUTH_LOGIN_SECRET`
set, because claude.ai signs in with OAuth and cannot send a static token.

For your own gateway, install `metaforge` for the skills and commands and add a
custom connector with your gateway's address for the tools. No manifest can carry
an address that is right for everyone, because each person runs their own gateway.

## Things that will bite you

**A quick tunnel's hostname is temporary.** `cloudflared tunnel --url` and
`ngrok http` assign a random name that changes whenever the process restarts,
and the process does not survive a reboot unless you supervise it. For anything
you intend to keep, use a named tunnel on a domain you control, or a private
network such as Tailscale.

**Writes are held for approval.** A tunnelled caller is never treated as the
engineer sitting at the machine, so a write arrives as a held request. Answer it
in the dashboard, or from a client that supports MCP elicitation. This is
deliberate and is not a sign anything is broken.

**A shared secret identifies nobody.** Whether it is the API key or the OAuth
login secret, every call is attributable to the credential, not to a person.
That is honest for a gateway with one owner and is *not* an access model for a
team — `/health` on the gateway reports the posture it is actually running.

## See also

- [`docs/runbooks/cloudflare-mcp-tunnel.md`](https://github.com/FidelOdok/MetaForge/blob/main/docs/runbooks/cloudflare-mcp-tunnel.md)
  — the named-tunnel setup in detail. Runbooks are not published to this
  site, so that link goes to the repository
- [Gateway authentication](../deployment/authentication.md) — the gateway's own auth, which is separate from the sidecar's
- [Claude Code integration](claude-code.md) — the plugin, its skills and commands
