---
description: Check the connection and what is reachable
---

Diagnose this MetaForge connection.

Call `health/check`, then `tools/list` and `resources/list`. Report the gateway version and auth mode.

Check `_meta.unavailableAdapters` on both listings — an adapter whose container is down contributes no tools and no resources, and the list simply looks shorter. Name any that are missing rather than describing what is left as if it were everything.
