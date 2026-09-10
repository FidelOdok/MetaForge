"""Gateway-side provider auth store (MET-10).

Where `forge auth login` credentials land so the harness runtime can use them
without editing env + restarting. Two things live here, persisted `0600`:

- ``credentials``: raw API keys per provider ``{provider: {api_key, base_url?}}``
  — injected into the ``ProviderSpec`` at resolve time (see
  ``adapters._require_key`` / ``harness_backend.provider_config_from_env``).
- ``selection``: the durable active ``{provider, model}`` override, consulted
  before the ``METAFORGE_LLM_*`` env defaults.

OAuth/subscription credentials (Codex/ChatGPT) are NOT stored here — the login
writes those to ``codex_auth.auth_json_path()`` so ``codex_invoke`` reads them
unchanged. This store only holds API keys + the selection.

Env vars remain the fallback everywhere, so an empty/absent store changes
nothing — this is purely additive.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def default_auth_path() -> Path:
    """Location of the harness auth store (override with METAFORGE_HARNESS_AUTH_PATH)."""
    override = os.environ.get("METAFORGE_HARNESS_AUTH_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "harness-auth.json"


@dataclass(frozen=True)
class StoredCredential:
    """A raw API key (and optional base_url) for one provider."""

    api_key: str
    base_url: str | None = None


@dataclass(frozen=True)
class Selection:
    """The durable active provider/model override."""

    provider: str
    model: str | None = None


class AuthStore:
    """Reads/writes ``harness-auth.json`` — API keys + active selection.

    Best-effort and tolerant: a missing/corrupt file loads as empty so the
    runtime always falls back to env. Writes are ``0600`` (secrets inside).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_auth_path()
        self._credentials: dict[str, StoredCredential] = {}
        self._selection: Selection | None = None
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("auth_store_load_failed", path=str(self._path), error=str(exc))
            return
        for provider, cred in (data.get("credentials") or {}).items():
            if isinstance(cred, dict) and cred.get("api_key"):
                self._credentials[provider] = StoredCredential(
                    api_key=str(cred["api_key"]), base_url=cred.get("base_url")
                )
        sel = data.get("selection")
        if isinstance(sel, dict) and sel.get("provider"):
            self._selection = Selection(provider=str(sel["provider"]), model=sel.get("model"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "credentials": {
                p: {"api_key": c.api_key, **({"base_url": c.base_url} if c.base_url else {})}
                for p, c in self._credentials.items()
            }
        }
        if self._selection is not None:
            payload["selection"] = {
                "provider": self._selection.provider,
                **({"model": self._selection.model} if self._selection.model else {}),
            }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    # ---- credentials ----
    def get_credential(self, provider: str) -> StoredCredential | None:
        return self._credentials.get(provider)

    def set_credential(self, provider: str, api_key: str, base_url: str | None = None) -> None:
        self._credentials[provider] = StoredCredential(api_key=api_key, base_url=base_url or None)
        self._save()
        logger.info("auth_store_credential_set", provider=provider)

    def delete_credential(self, provider: str) -> bool:
        """Drop a provider's key (and clear the selection if it pointed there)."""
        existed = self._credentials.pop(provider, None) is not None
        if self._selection is not None and self._selection.provider == provider:
            self._selection = None
        if existed:
            self._save()
            logger.info("auth_store_credential_deleted", provider=provider)
        return existed

    def configured_providers(self) -> set[str]:
        return set(self._credentials)

    # ---- selection ----
    def get_selection(self) -> Selection | None:
        return self._selection

    def set_selection(self, provider: str, model: str | None = None) -> None:
        self._selection = Selection(provider=provider, model=model)
        self._save()
        logger.info("auth_store_selection_set", provider=provider, model=model)
