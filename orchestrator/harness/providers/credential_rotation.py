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

import time
from collections.abc import Callable

import structlog

from orchestrator.harness.providers.credentials import CredentialStore
from orchestrator.harness.providers.pipeline import Invoke, ProviderError
from orchestrator.harness.providers.rotation import (
    AuthProfile,
    ProfileRotor,
    RotationStrategy,
    rotating_invoke,
)

logger = structlog.get_logger(__name__)


class NoHealthyCredentialsError(RuntimeError):
    """The store has no non-dead credentials for a provider."""


def rotor_from_store(
    store: CredentialStore,
    provider: str,
    *,
    now: float | None = None,
    strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
) -> ProfileRotor:
    """Build a ProfileRotor from a provider's healthy (non-dead, un-cooled) creds."""
    healthy = store.healthy(provider, now=now)
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

    def _usage(profile: AuthProfile) -> int:
        cred = store.get(provider, profile.name)
        return cred.usage_count if cred else 0

    return ProfileRotor(profiles, strategy=strategy, usage=_usage)


def store_backed_invoke(
    base_invoke: Invoke,
    store: CredentialStore,
    provider: str,
    session_id: str,
    *,
    now: Callable[[], float] = time.time,
    strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
) -> Invoke:
    """An ``Invoke`` that rotates a provider's stored credentials per session,
    cooling down transient failures and blacklisting terminal ones.

    Raises :class:`NoHealthyCredentialsError` up front if none are usable.
    """
    rotor = rotor_from_store(store, provider, now=now(), strategy=strategy)

    def _on_dead(profile: AuthProfile, exc: ProviderError) -> None:
        store.mark_dead(provider, profile.name, reason=str(exc))
        logger.warning("credential_blacklisted", provider=provider, credential=profile.name)

    def _on_failure(profile: AuthProfile, exc: ProviderError) -> None:
        store.mark_cooldown(provider, profile.name, now=now(), reason=str(exc))

    def _on_success(profile: AuthProfile) -> None:
        store.record_success(provider, profile.name)

    return rotating_invoke(
        base_invoke,
        rotor,
        session_id,
        on_dead=_on_dead,
        on_failure=_on_failure,
        on_success=_on_success,
    )
