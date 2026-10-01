---
description: Check the connection and what is reachable
---

Diagnose this MetaForge connection.

Call the `health.check` **tool**, then list the tools and resources available to you.

(This used to say `health/check`, `tools/list`, `resources/list`. Those are JSON-RPC methods, not tools -- a harness cannot call them, so doctor only worked where a shell was available and not at all in Codex, ChatGPT or claude.ai. FORGE-409.)

From `health.check`, report four things and do not infer any of them:
- `status`, and `unreachable_adapters` when it is `degraded`. A registered adapter that did not answer still contributes its tool count to `tools_registered`; `reachable` is the field that says whether those tools can be called.
- `auth.mode`. If it is `open`, say so plainly -- every connection is accepted and no call is attributable. If it is `unknown`, the server was not told; report that rather than assuming it is secured.
- `client.protocol_skew`, if present, with the version the client asked for and the one the server pinned.
- `version`, the gateway version, against the version of the plugin package you are running from.

An adapter whose container is down contributes no tools and no resources, and the list simply looks shorter. Name any that are missing rather than describing what is left as if it were everything -- `health.check`'s `adapters` array is the reliable source for that, since you can read it without a shell.

Finally, look for MetaForge registered **twice** -- a plugin plus a user-scope server entry. If any tool name appears more than once in your own tool list, say so: calls can land on whichever gateway answers first, so two connections to different gateways will silently disagree about the twin. Only you can see this; the server cannot.
