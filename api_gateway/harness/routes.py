"""Harness capability endpoints for the model + tools/connectors selector (MET-548).

- ``GET /v1/harness/providers`` — the registered providers, each flagged with
  whether it's configured (key present / local / codex logged in) + the active
  provider/model from env.
- ``GET /v1/harness/models`` — models for a provider. For OpenAI-compatible
  providers with a base_url + key it live-fetches ``{base_url}/models``; other
  families return an empty list (the UI falls back to a free-text model field).
- ``GET /v1/harness/tools`` — MCP tools/connectors reachable via the gateway's
  bridge (empty until a real bridge is wired in).

All read-only and best-effort — a provider's model API being down never 500s.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from orchestrator.harness.providers import registry
from orchestrator.harness.providers.codex_auth import auth_json_path

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/harness", tags=["harness"])

# Local / self-hosted providers need no API key to be considered "configured".
_KEYLESS_PROVIDERS = frozenset({"ollama", "vllm", "llamacpp", "lmstudio", "sglang", "custom"})


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class ProviderInfo(BaseModel):
    id: str
    family: str
    configured: bool
    base_url: str | None = None


class ProvidersResponse(BaseModel):
    active_provider: str | None = None
    active_model: str | None = None
    providers: list[ProviderInfo] = Field(default_factory=list)


def _is_configured(profile: registry.ProviderProfile) -> bool:
    if profile.api_family == "codex":
        return auth_json_path() is not None
    if profile.id in _KEYLESS_PROVIDERS:
        return True
    env = profile.api_key_env
    return bool(env and os.environ.get(env, "").strip())


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """List registered providers (configured ones first) + the active selection."""
    infos: list[ProviderInfo] = []
    for pid in registry.available_providers():
        p = registry.get_profile(pid)
        infos.append(
            ProviderInfo(
                id=p.id, family=p.api_family, configured=_is_configured(p), base_url=p.base_url
            )
        )
    infos.sort(key=lambda i: (not i.configured, i.id))
    return ProvidersResponse(
        active_provider=(os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip() or None,
        active_model=(os.environ.get("METAFORGE_LLM_MODEL") or "").strip() or None,
        providers=infos,
    )


# ---------------------------------------------------------------------------
# Models (live-fetched for OpenAI-compatible providers)
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    id: str


class ModelsResponse(BaseModel):
    provider: str
    models: list[ModelInfo] = Field(default_factory=list)
    source: str  # "live" | "none"


async def _fetch_openai_models(base_url: str, api_key: str) -> list[str]:
    import httpx

    async with httpx.AsyncClient(timeout=12.0) as http:
        resp = await http.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    items = data.get("data", data.get("models", []))
    ids: list[str] = []
    for m in items:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("slug") or m.get("name")
            if mid:
                ids.append(str(mid))
        elif isinstance(m, str):
            ids.append(m)
    return sorted(set(ids))


@router.get("/models", response_model=ModelsResponse)
async def list_models(provider: str = Query(..., description="Provider id")) -> ModelsResponse:
    """Models for a provider. OpenAI-compatible + configured → live-fetched; else empty."""
    try:
        profile = registry.get_profile(provider)
    except registry.UnknownProviderError:
        return ModelsResponse(provider=provider, models=[], source="none")

    key = os.environ.get(profile.api_key_env or "", "").strip()
    base_url = profile.base_url or os.environ.get(profile.base_url_env or "", "").strip()
    if profile.api_family == "openai" and base_url and key:
        try:
            ids = await _fetch_openai_models(base_url, key)
            return ModelsResponse(
                provider=provider, models=[ModelInfo(id=i) for i in ids], source="live"
            )
        except Exception as exc:  # noqa: BLE001 - best-effort; UI falls back to free text
            logger.warning("harness_models_fetch_failed", provider=provider, error=str(exc))
    return ModelsResponse(provider=provider, models=[], source="none")


# ---------------------------------------------------------------------------
# Tools / connectors
# ---------------------------------------------------------------------------


class ToolInfo(BaseModel):
    id: str
    name: str
    server: str
    capability: str | None = None


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools() -> list[ToolInfo]:
    """MCP tools/connectors reachable via the gateway's bridge (empty if none wired)."""
    from api_gateway.chat.routes import get_mcp_bridge

    tools = await get_mcp_bridge().list_tools()
    out: list[ToolInfo] = []
    for t in tools:
        tid = str(t.get("tool_id") or t.get("name") or "").strip()
        if not tid:
            continue
        server = tid.split(".")[0] if "." in tid else "mcp"
        out.append(
            ToolInfo(
                id=tid,
                name=str(t.get("name") or tid),
                server=server,
                capability=t.get("capability"),
            )
        )
    return out
