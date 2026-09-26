"""JWKS fetching and caching for Supabase-issued access tokens.

Supabase signs access tokens with rotating asymmetric keys and publishes the
public half at ``/auth/v1/.well-known/jwks.json``. Verifying a token therefore
means resolving its ``kid`` header against that document.

Two properties matter and neither is free:

**Rotation must not cause an outage.** A cache with a fixed TTL and nothing else
rejects every token signed by a newly-rotated key until the TTL lapses. So an
unknown ``kid`` triggers an immediate refresh, rate-limited so that a stream of
tokens bearing a bogus ``kid`` cannot be turned into a request amplifier against
the identity provider.

**A slow or dead JWKS endpoint must not hang the gateway.** Fetches are bounded
by an explicit timeout, and concurrent misses collapse onto a single in-flight
request rather than stampeding.

``PyJWKClient`` from PyJWT does much of this, but synchronously, via ``urllib``.
Blocking the event loop inside a FastAPI dependency is not acceptable, hence the
small async implementation here.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.auth.jwks")

__all__ = ["JwksCache", "JwksError"]

#: How long a successfully fetched JWKS document is trusted without re-fetching.
DEFAULT_TTL_SECONDS = 600.0

#: Floor between refreshes triggered by an unknown ``kid``. Without this, tokens
#: carrying a made-up ``kid`` would let any caller drive unbounded outbound
#: requests to the identity provider.
MIN_REFRESH_INTERVAL_SECONDS = 30.0

#: Bound on a single JWKS fetch.
DEFAULT_TIMEOUT_SECONDS = 5.0


class JwksError(RuntimeError):
    """The signing key could not be resolved.

    Distinct from a token being *invalid*: this means we could not determine
    whether it is valid, which is a 503 rather than a 401.
    """


class JwksCache:
    """Caches a JWKS document and resolves signing keys by ``kid``."""

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._client = client
        self._owns_client = client is None

        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._last_attempt: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return self._url

    async def aclose(self) -> None:
        """Release the HTTP client, if this cache created one."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_key(self, kid: str | None) -> Any:
        """Return the signing key for ``kid``.

        Raises
        ------
        JwksError
            If the document cannot be fetched, or contains no such key after a
            refresh.
        """
        with tracer.start_as_current_span("jwks.get_key") as span:
            span.set_attribute("jwks.kid", kid or "")

            if self._is_stale():
                await self._refresh(reason="stale")

            key = self._lookup(kid)
            if key is not None:
                span.set_attribute("jwks.cache_hit", True)
                return key

            # Unknown kid. Most likely a rotation we have not seen yet; possibly
            # a forged header. Refresh once, rate-limited, then decide.
            span.set_attribute("jwks.cache_hit", False)
            if self._may_refresh():
                await self._refresh(reason="unknown_kid")
                key = self._lookup(kid)
                if key is not None:
                    return key

            raise JwksError(
                f"No signing key matching kid={kid!r} in {self._url}"
                if kid
                else f"Token has no 'kid' header and {self._url} publishes multiple keys"
            )

    # -- internals ---------------------------------------------------------

    def _is_stale(self) -> bool:
        return not self._keys or (time.monotonic() - self._fetched_at) > self._ttl

    def _may_refresh(self) -> bool:
        return (time.monotonic() - self._last_attempt) >= MIN_REFRESH_INTERVAL_SECONDS

    def _lookup(self, kid: str | None) -> Any:
        if kid:
            return self._keys.get(kid)
        # A document publishing exactly one key is unambiguous even without a
        # kid. More than one and we cannot guess, so the caller gets an error.
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return None

    async def _refresh(self, *, reason: str) -> None:
        async with self._lock:
            # Another coroutine may have refreshed while we waited for the lock.
            if reason == "stale" and not self._is_stale():
                return
            self._last_attempt = time.monotonic()

            with tracer.start_as_current_span("jwks.fetch") as span:
                span.set_attribute("jwks.url", self._url)
                span.set_attribute("jwks.reason", reason)
                try:
                    payload = await self._fetch()
                    keys = self._parse(payload)
                except Exception as exc:
                    span.record_exception(exc)
                    logger.error(
                        "jwks_fetch_failed",
                        url=self._url,
                        reason=reason,
                        error=str(exc),
                        have_cached_keys=bool(self._keys),
                    )
                    # Keep serving the previous document if we have one: a
                    # transient identity-provider blip should degrade to "stale
                    # keys" rather than "every request 503s".
                    if self._keys:
                        return
                    raise JwksError(f"Could not fetch JWKS from {self._url}: {exc}") from exc

            self._keys = keys
            self._fetched_at = time.monotonic()
            logger.info("jwks_refreshed", url=self._url, reason=reason, key_count=len(keys))

    async def _fetch(self) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        response = await self._client.get(self._url, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise JwksError(f"JWKS at {self._url} is not a JSON object")
        return data

    @staticmethod
    def _parse(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            key_set = jwt.PyJWKSet.from_dict(payload)
        except Exception as exc:
            raise JwksError(f"Malformed JWKS document: {exc}") from exc

        keys: dict[str, Any] = {}
        for entry in key_set.keys:
            key_id = getattr(entry, "key_id", None)
            if key_id:
                keys[key_id] = entry.key
        if not keys:
            raise JwksError("JWKS document contains no usable signing keys")
        return keys
