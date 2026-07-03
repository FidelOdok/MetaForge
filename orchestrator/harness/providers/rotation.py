"""Auth-profile rotation with per-session pinning (MET-547, Phase 1).

A provider may have several credential sets (an "auth profile" -- distinct API
key, org, or base URL). Two competing goals:

* **Cache warmth** -- prompt caches are keyed per credential, so a given session
  should keep hitting the *same* profile to stay warm. Hence per-session pinning.
* **Resilience / load spread** -- on an auth or rate failure the session should
  rotate to the next healthy profile, and new sessions should fan out across
  profiles rather than all pinning the first.

:class:`ProfileRotor` pins a profile per ``session_id`` (round-robin assignment
for spread), returns it stably while healthy, and rotates to the next profile
for that session on :meth:`mark_failed`. This is the "rotate profile -> fall to
the next model" ordering from the MET-547 success criteria: profile rotation is
tried before the :class:`~orchestrator.harness.providers.pipeline.ProviderPipeline`
falls through to the next provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import structlog

from orchestrator.harness.providers.pipeline import Invoke, ProviderError, ProviderSpec

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AuthProfile:
    """One credential set for a provider."""

    name: str
    api_key_env: str
    org_id: str | None = None
    base_url: str | None = None


class ProfileExhaustedError(Exception):
    """Every auth profile has failed for a session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"all auth profiles exhausted for session '{session_id}'")


class ProfileRotor:
    """Pin an auth profile per session; rotate on failure.

    Construction is pure config. State is per session: the index of the
    currently pinned profile, and the set of profile names that have failed
    for that session (so rotation never returns to a dead profile).
    """

    def __init__(self, profiles: Sequence[AuthProfile]) -> None:
        if not profiles:
            raise ValueError("ProfileRotor requires at least one auth profile")
        self._profiles: list[AuthProfile] = list(profiles)
        # session_id -> index into _profiles of the pinned profile
        self._pinned: dict[str, int] = {}
        # session_id -> set of failed profile indices
        self._failed: dict[str, set[int]] = {}
        # round-robin cursor for assigning a starting profile to new sessions
        self._cursor = 0

    def pin(self, session_id: str) -> AuthProfile:
        """Return the session's pinned profile, assigning one on first use.

        New sessions are assigned round-robin so load spreads across profiles;
        repeat calls return the same profile to keep the cache warm.
        """
        if session_id not in self._pinned:
            index = self._cursor % len(self._profiles)
            self._cursor += 1
            self._pinned[session_id] = index
            self._failed.setdefault(session_id, set())
            logger.info(
                "auth_profile_pinned",
                session_id=session_id,
                profile=self._profiles[index].name,
            )
        return self._profiles[self._pinned[session_id]]

    def current(self, session_id: str) -> AuthProfile:
        """The currently pinned profile (pins one if none yet)."""
        return self.pin(session_id)

    def mark_failed(self, session_id: str, profile: AuthProfile) -> AuthProfile:
        """Record ``profile`` as failed for the session and rotate to the next.

        Returns the newly pinned profile. Raises :class:`ProfileExhaustedError`
        when no healthy profile remains for the session.
        """
        self.pin(session_id)  # ensure state exists
        try:
            failed_index = self._profiles.index(profile)
        except ValueError as exc:
            raise ValueError(f"unknown profile: {profile.name}") from exc

        failed = self._failed[session_id]
        failed.add(failed_index)

        # Next healthy profile, scanning in order from just after the failed one.
        for offset in range(1, len(self._profiles) + 1):
            candidate = (failed_index + offset) % len(self._profiles)
            if candidate not in failed:
                self._pinned[session_id] = candidate
                logger.warning(
                    "auth_profile_rotated",
                    session_id=session_id,
                    from_profile=profile.name,
                    to_profile=self._profiles[candidate].name,
                )
                return self._profiles[candidate]

        logger.error("auth_profiles_exhausted", session_id=session_id)
        raise ProfileExhaustedError(session_id)

    def reset(self, session_id: str) -> None:
        """Forget a session's pin + failure history (e.g. on session end)."""
        self._pinned.pop(session_id, None)
        self._failed.pop(session_id, None)


# Auth failures that should trigger a profile rotation (vs. a hard error).
_ROTATE_STATUSES = frozenset({401, 403, 429})
# Terminal credential failures (bad/revoked key) — the credential is *dead*,
# distinct from a transient 429 rate limit which should be retried, not blacklisted.
_DEAD_STATUSES = frozenset({401, 403})

# Called when a profile fails terminally, so a store can blacklist its credential.
OnDead = Callable[[AuthProfile, ProviderError], None]


def _should_rotate(exc: ProviderError) -> bool:
    return exc.status_code in _ROTATE_STATUSES or exc.retryable


def rotating_invoke(
    base_invoke: Invoke,
    rotor: ProfileRotor,
    session_id: str,
    *,
    on_dead: OnDead | None = None,
) -> Invoke:
    """Wrap an ``Invoke`` so it uses (and rotates) a session's auth profile.

    The pinned profile's credentials (``api_key_env`` / ``base_url``) are applied
    to the ProviderSpec before each call. On an auth/rate failure (401/403/429 or
    a retryable error) the profile is marked failed and the call retries with the
    next healthy profile — so profile rotation happens *before* the pipeline falls
    through to the next provider (Hermes's 'rotate profile → fall to next model').
    A *terminal* failure (401/403) additionally fires ``on_dead`` so a credential
    store can blacklist it. Raises the last :class:`ProviderError` once every
    profile is exhausted.
    """

    async def invoke(spec: ProviderSpec, request: Any) -> Any:
        while True:
            profile = rotor.current(session_id)
            variant = replace(
                spec,
                api_key_env=profile.api_key_env,
                base_url=profile.base_url or spec.base_url,
            )
            try:
                return await base_invoke(variant, request)
            except ProviderError as exc:
                if not _should_rotate(exc):
                    raise
                if on_dead is not None and exc.status_code in _DEAD_STATUSES:
                    on_dead(profile, exc)
                try:
                    rotor.mark_failed(session_id, profile)
                except ProfileExhaustedError:
                    raise exc from exc
                logger.info("auth_profile_rotated_on_error", session_id=session_id)

    return invoke
