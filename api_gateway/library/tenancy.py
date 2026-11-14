"""Whose library a request sees and writes (FORGE-876).

The library is reference data every project draws on: processes, materials,
components, appearances, ergonomic limits and machines. On a local gateway
there is one library. On a gateway shared by several accounts there are two
layers:

* **shared** entries, curated for everyone and read-only to accounts;
* an account's **own** entries, which only that account sees and changes.

The account is the scope's ``tenant``, an opaque key the auth provider sets
(:class:`~api_gateway.auth.scope.ProjectScope`). Open core never interprets it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from twin_core.library.viewer import NO_TENANT

__all__ = ["NO_TENANT", "library_tenant", "with_ownership"]


def library_tenant(request: Request) -> str | None:
    """``None`` for an unscoped caller (local gateway, the deployment's own
    services): every entry, writes are shared. Otherwise the caller's tenant."""
    scope = getattr(request.state, "project_scope", None)
    if scope is None or getattr(scope, "unrestricted", False):
        return None
    return getattr(scope, "tenant", None) or NO_TENANT


def with_ownership(entry: dict[str, Any], owner: str | None) -> dict[str, Any]:
    """The entry as returned to a caller, saying whether it is shared."""
    return {**entry, "shared": owner is None}
