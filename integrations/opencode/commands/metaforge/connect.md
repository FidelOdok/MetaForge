---
description: Find the gateway to point this plugin at
---

Work out which MetaForge gateway this machine should use.

If the `forge` CLI is installed, run `forge connect`. It probes the usual local endpoints plus anything already configured, and prints the URL to use. It exits non-zero when it finds nothing.

If `forge` is not installed, POST an MCP `initialize` to each candidate and look at `result.serverInfo.name`:
- `http://localhost:8765/mcp` (the MCP sidecar in the standard compose file)
- `http://localhost:8000/mcp` (a gateway serving MCP itself)

Only treat a candidate as the gateway when it answers and names itself `metaforge-mcp`. Something listening on the port is not the same thing: a dev machine has plenty of services, and another MCP server will answer `initialize` perfectly happily. A 401 or 403 *is* a match -- the URL is right and a token is the next step.

Then tell the user what to do with it: put the URL in the `url` of the `metaforge` server under `mcp.servers` in their opencode.json (`opencode mcp add metaforge --url <url>` writes it). If nothing was found, say so and give them the three options -- start one with `docker compose up gateway`, enter a team or hosted URL, or run MetaForge as a local process with no gateway at all: a `local` server whose `command` is `["metaforge-mcp", "--transport", "stdio", "--profile", "all"]`. Do not guess a URL on their behalf.
