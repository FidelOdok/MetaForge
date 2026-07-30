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
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from orchestrator.harness.providers import registry
from orchestrator.harness.providers.auth_store import AuthStore
from orchestrator.harness.providers.codex_auth import (
    CodexCredentials,
    auth_json_path,
    parse_credentials,
    save_credentials,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/harness", tags=["harness"])

# Local / self-hosted providers need no API key to be considered "configured".
_KEYLESS_PROVIDERS = frozenset({"ollama", "vllm", "llamacpp", "lmstudio", "sglang", "custom"})


def _active_provider() -> str:
    """Active provider id — the durable store selection wins over env."""
    sel = AuthStore().get_selection()
    if sel is not None:
        return sel.provider.strip().lower()
    return (os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip().lower()


def _credentials_for(profile: registry.ProviderProfile) -> tuple[str, str | None]:
    """(api_key, base_url) for a provider. For the *active* provider, honor the
    METAFORGE_LLM_* env — where the working key lives on deployments whose
    provider key-env name differs from the registry default (e.g. the box uses
    OPEN_ROUTER_API_KEY while the registry expects OPENROUTER_API_KEY)."""
    if profile.id == _active_provider():
        key = os.environ.get("METAFORGE_LLM_API_KEY", "").strip()
        base_url = (os.environ.get("METAFORGE_LLM_BASE_URL") or "").strip() or profile.base_url
        if key:
            return key, base_url
    key = os.environ.get(profile.api_key_env or "", "").strip()
    base_url = profile.base_url or (os.environ.get(profile.base_url_env or "", "").strip() or None)
    return key, base_url


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
    # registry.CODEX == "openai-codex" — the family is never the bare string
    # "codex". Checking for that let this fall through to the "active provider
    # borrows METAFORGE_LLM_API_KEY" branch below and report a false positive
    # (e.g. the openrouter env key) instead of the real auth.json check.
    if profile.api_family == registry.CODEX:
        return auth_json_path() is not None
    if profile.id in _KEYLESS_PROVIDERS:
        return True
    if AuthStore().get_credential(profile.id) is not None:  # logged in via `forge auth`
        return True
    key, _ = _credentials_for(profile)
    return bool(key)


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
    sel = AuthStore().get_selection()
    active_provider = (
        sel.provider if sel else (os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip() or None
    )
    active_model = (
        (sel.model if sel and sel.model else None)
        or (os.environ.get("METAFORGE_LLM_MODEL") or "").strip()
        or None
    )
    return ProvidersResponse(
        active_provider=active_provider, active_model=active_model, providers=infos
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

    key, base_url = _credentials_for(profile)
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


# ---------------------------------------------------------------------------
# Credential + selection writes (`forge auth login` / `use` / `logout`)
#
# These accept SECRETS. When METAFORGE_HARNESS_ADMIN_TOKEN is set they require a
# matching `X-MetaForge-Admin` header; otherwise (dev) they're allowed but log a
# warning. Production MUST set the token (or front the gateway with auth).
# ---------------------------------------------------------------------------


def _require_admin(x_metaforge_admin: str | None = Header(default=None)) -> None:
    token = os.environ.get("METAFORGE_HARNESS_ADMIN_TOKEN", "").strip()
    if not token:
        logger.warning("harness_credential_write_unauthenticated")
        return
    if (x_metaforge_admin or "").strip() != token:
        raise HTTPException(status_code=401, detail="invalid or missing X-MetaForge-Admin token")


def _codex_target_path() -> Path:
    """Where to write Codex OAuth creds (existing file, else CODEX_HOME/~/.codex)."""
    existing = auth_json_path()
    if existing is not None:
        return existing
    base = os.environ.get("CODEX_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".codex") / "auth.json"


class SetCredentialRequest(BaseModel):
    provider: str = Field(..., description="Registry provider id (e.g. openai, openai-codex).")
    method: str = Field("api_key", description="'api_key' or 'oauth'.")
    api_key: str | None = Field(default=None, description="Raw API key (method=api_key).")
    base_url: str | None = Field(default=None, description="Optional base_url override.")
    tokens: dict[str, Any] | None = Field(
        default=None, description="OAuth token blob in auth.json shape (method=oauth)."
    )


class SetSelectionRequest(BaseModel):
    provider: str = Field(..., description="Registry provider id to make active.")
    model: str | None = Field(default=None, description="Optional model id.")


class OkResponse(BaseModel):
    ok: bool = True
    provider: str
    method: str | None = None


@router.post("/credentials", response_model=OkResponse)
async def set_credential(
    body: SetCredentialRequest, _: None = Depends(_require_admin)
) -> OkResponse:
    """Store a provider credential so the runtime uses it (no restart).

    ``api_key`` → the gateway auth store; ``oauth`` → the Codex ``auth.json`` the
    codex adapter reads. Validates the provider against the registry.
    """
    provider = body.provider.strip().lower()
    try:
        registry.get_profile(provider)
    except registry.UnknownProviderError as exc:
        raise HTTPException(status_code=400, detail=f"unknown provider '{provider}'") from exc

    if body.method == "oauth":
        if not body.tokens:
            raise HTTPException(status_code=400, detail="method=oauth requires 'tokens'")
        try:
            creds: CodexCredentials = parse_credentials(body.tokens)
        except Exception as exc:  # noqa: BLE001 - surface a clear 400 on a bad blob
            raise HTTPException(status_code=400, detail=f"bad oauth tokens: {exc}") from exc
        target = _codex_target_path()
        if not save_credentials(target, creds):
            # save_credentials is best-effort (never raises) — a write failure
            # (e.g. read-only mount) must not be reported as a successful login,
            # or the token is silently lost and the next chat turn fails with a
            # confusing "no Codex credentials found".
            raise HTTPException(
                status_code=500, detail=f"failed to persist Codex credentials to {target}"
            )
        logger.info("harness_oauth_credential_saved", provider=provider, path=str(target))
        return OkResponse(provider=provider, method="oauth")

    if not body.api_key or not body.api_key.strip():
        raise HTTPException(status_code=400, detail="method=api_key requires 'api_key'")
    AuthStore().set_credential(provider, body.api_key.strip(), body.base_url)
    return OkResponse(provider=provider, method="api_key")


@router.put("/selection", response_model=OkResponse)
async def set_selection(body: SetSelectionRequest, _: None = Depends(_require_admin)) -> OkResponse:
    """Set the durable active provider/model (overrides the METAFORGE_LLM_* env)."""
    provider = body.provider.strip().lower()
    try:
        registry.get_profile(provider)
    except registry.UnknownProviderError as exc:
        raise HTTPException(status_code=400, detail=f"unknown provider '{provider}'") from exc
    AuthStore().set_selection(provider, (body.model or "").strip() or None)
    return OkResponse(provider=provider, method="selection")


@router.delete("/credentials/{provider}", response_model=OkResponse)
async def delete_credential(provider: str, _: None = Depends(_require_admin)) -> OkResponse:
    """Forget a provider's stored API key (and clear the selection if it pointed there)."""
    AuthStore().delete_credential(provider.strip().lower())
    return OkResponse(provider=provider.strip().lower(), method="logout")
