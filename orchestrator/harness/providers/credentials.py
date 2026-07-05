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
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Escalating cooldown for a credential that keeps failing transiently (429):
# 1st failure → 30s, 2nd → 1m, 3rd+ → 5m. A credential auto-revives once its
# cooldown expires (see ``healthy``), so no background timer is needed.
COOLDOWN_LADDER: tuple[float, ...] = (30.0, 60.0, 300.0)


def next_cooldown(failure_count: int) -> float:
    """Cooldown seconds for the Nth consecutive failure (1-based), capped."""
    if failure_count <= 0:
        return 0.0
    return COOLDOWN_LADDER[min(failure_count, len(COOLDOWN_LADDER)) - 1]


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
    # Transient backoff (epoch seconds) — set on a 429, auto-expires. Distinct
    # from ``dead``, which is a terminal (401/403) blacklist.
    cooldown_until: float | None = None
    failure_count: int = 0  # consecutive transient failures — drives escalation
    usage_count: int = 0  # successful uses — drives the least_used strategy


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
        """Clear the dead flag + any cooldown (e.g. after a re-login)."""
        for cred in self._by_provider.get(provider, []):
            if cred.name == name:
                cred.dead = False
                cred.dead_reason = None
                cred.cooldown_until = None
                cred.failure_count = 0
                self._save()
                return

    def mark_cooldown(
        self, provider: str, name: str, *, now: float, reason: str | None = None
    ) -> None:
        """Put a credential on an escalating transient cooldown (a 429).

        Increments its failure count and sets ``cooldown_until`` per
        :func:`next_cooldown`; the credential auto-revives once it expires.
        """
        for cred in self._by_provider.get(provider, []):
            if cred.name == name:
                cred.failure_count += 1
                cred.cooldown_until = now + next_cooldown(cred.failure_count)
                self._save()
                logger.info(
                    "credential_cooldown",
                    provider=provider,
                    name=name,
                    until=cred.cooldown_until,
                    failure_count=cred.failure_count,
                    reason=reason,
                )
                return

    def record_success(self, provider: str, name: str) -> None:
        """Clean call: reset failure/cooldown state and count the usage."""
        for cred in self._by_provider.get(provider, []):
            if cred.name == name:
                cred.failure_count = 0
                cred.cooldown_until = None
                cred.usage_count += 1
                self._save()
                return

    # ---- queries ----
    def get(self, provider: str, name: str) -> Credential | None:
        return next((c for c in self._by_provider.get(provider, []) if c.name == name), None)

    def credentials(self, provider: str) -> list[Credential]:
        return list(self._by_provider.get(provider, []))

    def healthy(self, provider: str, *, now: float | None = None) -> list[Credential]:
        """Non-dead credentials whose cooldown (if any) has expired.

        ``now`` is injectable for deterministic tests; defaults to wall-clock.
        """
        t = now if now is not None else time.time()
        return [
            c
            for c in self._by_provider.get(provider, [])
            if not c.dead and (c.cooldown_until is None or t >= c.cooldown_until)
        ]

    def providers(self) -> list[str]:
        return sorted(self._by_provider)
