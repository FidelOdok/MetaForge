"""Which organisation a written name is, for code outside the library (FORGE-450).

A component selection, a BOM line or a cost estimate holds a maker's or a seller's name as
written. :func:`make_organisation_resolver` returns an async function that answers with the
library's organisation for that name (any recorded spelling) or ``None``. In the gateway it
reads the gateway's own library; in the MCP sidecar, which has no library of its own, it
asks the gateway (``METAFORGE_GATEWAY_URL``). A lookup that fails answers ``None`` and is
logged: an organisation is context for a choice, never a reason to refuse one.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

OrganisationResolver = Callable[[str], Awaitable[dict[str, Any] | None]]


def org_summary(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """The part of an organisation a caller acts on."""
    if not entry:
        return None
    return {
        "id": entry.get("id"),
        "title": entry.get("title"),
        "name": entry.get("name"),
        "status": entry.get("status"),
        "status_reason": entry.get("status_reason") or "",
        "roles": entry.get("roles") or [],
        "regions": entry.get("regions") or [],
        "country": entry.get("country") or "",
    }


def make_organisation_resolver(gateway_url: str | None = None) -> OrganisationResolver:
    url = (
        gateway_url if gateway_url is not None else os.environ.get("METAFORGE_GATEWAY_URL") or ""
    ).strip()
    if url:
        import httpx

        async def remote(text: str) -> dict[str, Any] | None:
            if not str(text or "").strip():
                return None
            try:
                async with httpx.AsyncClient(base_url=url, timeout=10) as client:
                    resp = await client.get(
                        "/v1/library/organisations/resolve", params={"name": text}
                    )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return org_summary(resp.json().get("entry"))
            except Exception as exc:  # noqa: BLE001 - context only, never fatal
                logger.warning("organisation_lookup_failed", where="gateway", error=str(exc))
                return None

        return remote

    async def local(text: str) -> dict[str, Any] | None:
        if not str(text or "").strip():
            return None
        try:
            from api_gateway.library.catalog_routes import get_catalog

            org = get_catalog().resolve_organisation(text, viewer=None)
        except Exception as exc:  # noqa: BLE001 - context only, never fatal
            logger.warning("organisation_lookup_failed", where="local", error=str(exc))
            return None
        return org_summary(org.as_dict()) if org is not None else None

    return local
