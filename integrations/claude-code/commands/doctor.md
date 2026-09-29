---
description: Check the connection and what is reachable
---

Diagnose this MetaForge connection.

Call `health/check`, then `tools/list` and `resources/list`.

From `health/check`, report four things and do not infer any of them:
- `status`, and `unreachable_adapters` when it is `degraded`. A registered adapter that did not answer still contributes its tool count to `tools_registered`; `reachable` is the field that says whether those tools can be called.
- `auth.mode`. If it is `open`, say so plainly -- every connection is accepted and no call is attributable. If it is `unknown`, the server was not told; report that rather than assuming it is secured.
- `client.protocol_skew`, if present, with the version the client asked for and the one the server pinned.
- `version`, the gateway version, against the version of the plugin package you are running from.

Check `_meta.unavailableAdapters` on both listings — an adapter whose container is down contributes no tools and no resources, and the list simply looks shorter. Name any that are missing rather than describing what is left as if it were everything.
