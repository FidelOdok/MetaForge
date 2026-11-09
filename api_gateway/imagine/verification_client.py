"""Client for the MetaForge-Verification service (its public HTTP contract).

Imagine validation is decided by that service, not here: it owns every rule that
decides pass or fail, and this gateway renders what it returns. This module is
the only place the gateway talks to it, and it speaks only the versioned HTTP
API (``POST /v1/validations``). The dependency runs one way: the service never
imports the gateway.

Two failures are kept apart on purpose:

- :class:`VerificationUnavailable`: the service could not be reached, refused
  the credential or failed. The caller reports ``blocked_external``. It never
  falls back to local rules, because a fallback nobody is alarmed by becomes
  permanent.
- :class:`VerificationRefused`: the service understood the request and said it
  is malformed (422). That is a bug in the mapper, not an outage, and is
  surfaced as an error rather than hidden as "unavailable".
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.verification_client")

URL_ENV = "METAFORGE_VERIFICATION_URL"
TOKEN_ENV = "METAFORGE_VERIFICATION_TOKEN"
DEFAULT_TIMEOUT_SECONDS = 10.0
VALIDATIONS_PATH = "/v1/validations"

__all__ = [
    "VerificationClient",
    "VerificationRefused",
    "VerificationUnavailable",
    "from_env",
]


class VerificationUnavailable(Exception):
    """The service could not be reached, refused the credential, or failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VerificationRefused(Exception):
    """The service rejected the request as malformed. A bug here, not an outage."""

    def __init__(self, detail: Any) -> None:
        super().__init__(f"the verification service refused the request: {detail}")
        self.detail = detail


class VerificationClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    async def validate(self, body: dict[str, Any]) -> dict[str, Any]:
        """Send a ``ValidationRequest``; return the service's ``ValidationReport``.

        ``body`` is the request exactly as the service defines it. The report is
        returned untouched: the gateway renders the decision and never edits it.
        """
        with tracer.start_as_current_span("imagine.verification.validate") as span:
            span.set_attribute("verification.url", self.base_url)
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as http:
                    response = await http.post(
                        f"{self.base_url}{VALIDATIONS_PATH}", json=body, headers=headers
                    )
            except httpx.HTTPError as exc:
                span.record_exception(exc)
                logger.warning(
                    "imagine_verification_unreachable",
                    url=self.base_url,
                    error=type(exc).__name__,
                )
                raise VerificationUnavailable(
                    f"could not reach the verification service ({type(exc).__name__})"
                ) from exc
            span.set_attribute("verification.status", response.status_code)
            if response.status_code in (401, 403):
                logger.warning("imagine_verification_credential_refused", url=self.base_url)
                raise VerificationUnavailable(
                    f"the verification service refused the credential (HTTP {response.status_code})"
                )
            if response.status_code == 422:
                try:
                    detail: Any = response.json().get("detail")
                except ValueError:
                    detail = response.text[:300]
                logger.error("imagine_verification_request_refused", detail=str(detail)[:500])
                raise VerificationRefused(detail)
            if response.is_error:
                logger.warning(
                    "imagine_verification_failed",
                    url=self.base_url,
                    status=response.status_code,
                )
                raise VerificationUnavailable(
                    f"the verification service answered HTTP {response.status_code}"
                )
            try:
                report = response.json()
            except ValueError as exc:
                raise VerificationUnavailable(
                    "the verification service did not answer JSON"
                ) from exc
            if not isinstance(report, dict):
                raise VerificationUnavailable(
                    "the verification service answered an unexpected shape"
                )
            return report


def from_env() -> VerificationClient | None:
    """The configured client, or ``None`` when no URL is set.

    ``None`` is not a quiet default: the caller must report the service as
    unconfigured, so a gateway without one says so instead of validating
    nothing.
    """
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        return None
    return VerificationClient(url, os.environ.get(TOKEN_ENV) or None)
