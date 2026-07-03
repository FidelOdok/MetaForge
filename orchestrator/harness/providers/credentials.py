"""MetaForge credential store (MET-551).

A local, JSON-backed store holding *multiple credentials per provider* with
transparent **dead-token blacklisting** — mirroring Hermes's `~/.hermes/auth.json`.
A credential that fails terminally (revoked/invalid) is marked dead and no
longer replayed, so you don't get a flood of identical auth failures.

Path precedence: ``METAFORGE_CREDENTIALS_PATH`` env, else
``~/.metaforge/credentials.json``. The file is written ``0600`` (it references
key envs / tokens). Feeds :class:`ProfileRotor` via ``build_rotor`` (glue slice).
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Credential:
    """One credential for a provider (references a key env / endpoint)."""

    provider: str
    name: str
    api_key_env: str | None = None
    base_url: str | None = None
    org_id: str | None = None
    dead: bool = False
    dead_reason: str | None = None


def default_credentials_path() -> Path:
    override = os.environ.get("METAFORGE_CREDENTIALS_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "credentials.json"


class CredentialStore:
    """Multi-credential-per-provider store with dead-token blacklisting."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_credentials_path()
        self._by_provider: dict[str, list[Credential]] = {}
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not self._path.is_file():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for provider, creds in data.get("providers", {}).items():
            self._by_provider[provider] = [Credential(**c) for c in creds]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "providers": {p: [asdict(c) for c in creds] for p, creds in self._by_provider.items()}
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Tokens/keys inside — restrict to owner-only.
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    # ---- mutations ----
    def add(self, cred: Credential) -> Credential:
        """Add (or replace by name) a credential for a provider, then persist."""
        creds = self._by_provider.setdefault(cred.provider, [])
        existing = next((i for i, c in enumerate(creds) if c.name == cred.name), None)
        if existing is not None:
            creds[existing] = cred
        else:
            creds.append(cred)
        self._save()
        logger.info("credential_added", provider=cred.provider, name=cred.name)
        return cred

    def mark_dead(self, provider: str, name: str, reason: str | None = None) -> None:
        """Blacklist a credential terminally so it is no longer returned as healthy."""
        for cred in self._by_provider.get(provider, []):
            if cred.name == name:
                cred.dead = True
                cred.dead_reason = reason
                self._save()
                logger.warning(
                    "credential_marked_dead", provider=provider, name=name, reason=reason
                )
                return

    def revive(self, provider: str, name: str) -> None:
        """Clear the dead flag (e.g. after a re-login)."""
        for cred in self._by_provider.get(provider, []):
            if cred.name == name:
                cred.dead = False
                cred.dead_reason = None
                self._save()
                return

    # ---- queries ----
    def credentials(self, provider: str) -> list[Credential]:
        return list(self._by_provider.get(provider, []))

    def healthy(self, provider: str) -> list[Credential]:
        return [c for c in self._by_provider.get(provider, []) if not c.dead]

    def providers(self) -> list[str]:
        return sorted(self._by_provider)
