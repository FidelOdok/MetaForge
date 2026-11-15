"""The credential the MCP sidecar presents when it calls the gateway.

The sidecar reaches the gateway over HTTP for approvals, flows, files, the
library, vendors and run checks. A gateway with an auth provider refuses a
request with no bearer token, and every one of those calls then failed with
401: writes were refused because the approval ledger could not record them.
``METAFORGE_GATEWAY_API_KEY`` is the deployment's service credential for the
gateway (the same one the live publisher already sends). Unset, as on every
local install whose gateway has no auth, nothing is added.
"""

from __future__ import annotations

import os

GATEWAY_KEY_ENV = "METAFORGE_GATEWAY_API_KEY"

__all__ = ["GATEWAY_KEY_ENV", "gateway_auth_headers"]


def gateway_auth_headers() -> dict[str, str]:
    """``Authorization`` for calls to the gateway, or no headers at all."""
    key = (os.environ.get(GATEWAY_KEY_ENV) or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}
