"""Glue: credential store → auth-profile rotation (MET-551).

Ties :class:`CredentialStore` (multiple credentials per provider, dead-token
blacklisting) to :func:`rotating_invoke` (per-session profile rotation). A
provider's *healthy* credentials become a :class:`ProfileRotor`, and a terminal
auth failure (401/403) during rotation is written straight back to the store as
dead — so a revoked key is blacklisted transparently and not replayed on the
next run. This is the full Hermes auth model: multi-credential + rotation +
dead-token handling.
"""

from __future__ import annotations

import structlog

from orchestrator.harness.providers.credentials import CredentialStore
from orchestrator.harness.providers.pipeline import Invoke, ProviderError
from orchestrator.harness.providers.rotation import (
    AuthProfile,
    ProfileRotor,
    rotating_invoke,
)

logger = structlog.get_logger(__name__)


class NoHealthyCredentialsError(RuntimeError):
    """The store has no non-dead credentials for a provider."""


def rotor_from_store(store: CredentialStore, provider: str) -> ProfileRotor:
    """Build a ProfileRotor from a provider's healthy (non-dead) credentials."""
    healthy = store.healthy(provider)
    if not healthy:
        raise NoHealthyCredentialsError(f"no healthy credentials for '{provider}'")
    profiles = [
        AuthProfile(
            name=c.name,
            api_key_env=c.api_key_env or "",
            org_id=c.org_id,
            base_url=c.base_url,
        )
        for c in healthy
    ]
    return ProfileRotor(profiles)


def store_backed_invoke(
    base_invoke: Invoke,
    store: CredentialStore,
    provider: str,
    session_id: str,
) -> Invoke:
    """An ``Invoke`` that rotates a provider's stored credentials per session and
    blacklists any that fail terminally.

    Raises :class:`NoHealthyCredentialsError` up front if none are usable.
    """
    rotor = rotor_from_store(store, provider)

    def _on_dead(profile: AuthProfile, exc: ProviderError) -> None:
        store.mark_dead(provider, profile.name, reason=str(exc))
        logger.warning("credential_blacklisted", provider=provider, credential=profile.name)

    return rotating_invoke(base_invoke, rotor, session_id, on_dead=_on_dead)
