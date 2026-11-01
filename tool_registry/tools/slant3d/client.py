"""Slant 3D HTTP client: print-farm quoting for an STL.

Slant 3D runs a print farm with a public v2 API. An uploaded STL is sliced on
Slant's side, which yields its size, volume, weight, print time and whether it
needs supports; a second call prices it in a chosen filament and quantity, in
USD. That is the quoting and slicing estimate a hosted gateway with no slicer
container can still give.

Upload is three steps: ask for a presigned URL (``POST /files/direct-upload``),
PUT the bytes to it, then confirm (``POST /files/confirm-upload``), which runs
the analysis. Every file belongs to a "platform" on the account; the client
uses ``SLANT3D_PLATFORM_ID`` or, without it, the account's first platform.

Ordering is deliberately not here: it charges the account and ships a part.

Key comes from ``SLANT3D_API_KEY``. It is never read from a file in the repo
and never logged. ``SLANT3D_BASE_URL`` overrides the API host (tests, a proxy).
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.slant3d.client")

DEFAULT_BASE_URL = "https://slant3dapi.com/v2/api"

MATERIALS = ("PLA", "PETG", "OPM")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_QUANTITY = 10_000

REQUEST_TIMEOUT_SECONDS = 30.0
# Confirming an upload slices the part before it answers.
CONFIRM_TIMEOUT_SECONDS = 180.0
UPLOAD_TIMEOUT_SECONDS = 120.0

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_STATUS_REASONS = {
    400: "Slant 3D rejected the request",
    401: "Slant 3D refused the API key, or the file belongs to another account",
    403: "the Slant 3D key is not allowed to make this call",
    404: "Slant 3D has no such file",
    429: "Slant 3D rate-limited the request (100 a minute); wait and retry",
}

# Slant's own metric names, mapped to names that carry their unit.
_METRICS = {
    "x": "size_x_mm",
    "y": "size_y_mm",
    "z": "size_z_mm",
    "volume": "volume_cm3",
    "surfaceArea": "surface_area_mm2",
    "weight": "weight_g",
    "printTimeEstimate": "print_time_minutes",
    "supportRequired": "support_required",
    "supportVolumeEstimate": "support_volume_cm3",
    "bodyCount": "body_count",
}


class Slant3DError(RuntimeError):
    """A Slant 3D call that failed: a refused key, an unknown file, an
    unreachable API or a response that does not match the documented shape."""


class Slant3DFile(BaseModel):
    """One uploaded STL and the analysis Slant ran on it."""

    id: str
    name: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


def validate_file_id(file_id: str) -> str:
    if not _UUID_RE.match(file_id):
        raise Slant3DError(f"'file_id' {file_id!r} is not a Slant 3D file id")
    return file_id


class Slant3DClient:
    """Slant 3D v2 REST client.

    Construction is pure config, no I/O and no key check, so the gateway
    boots the same whether or not the key works. A failed call raises
    :class:`Slant3DError` with a reason the model can act on.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        platform_id: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("SLANT3D_API_KEY", "")
        self._base_url = (
            base_url or os.environ.get("SLANT3D_BASE_URL", "") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._platform_id = platform_id or os.environ.get("SLANT3D_PLATFORM_ID", "") or None

    @property
    def name(self) -> str:
        return "Slant 3D"

    async def upload_stl(self, data: bytes, filename: str) -> Slant3DFile:
        """Upload an STL and return it with Slant's analysis."""
        with tracer.start_as_current_span("slant3d.upload") as span:
            span.set_attribute("slant3d.bytes", len(data))
            platform_id = await self._platform()
            created = await self._request(
                "POST", "/files/direct-upload", json={"name": filename, "platformId": platform_id}
            )
            if not isinstance(created, dict):
                raise Slant3DError("Slant 3D returned an unexpected upload response")
            url = created.get("presignedUrl")
            placeholder = created.get("filePlaceholder")
            if not isinstance(url, str) or not isinstance(placeholder, dict):
                raise Slant3DError("Slant 3D returned no upload URL")
            await self._put(url, data)
            confirmed = await self._request(
                "POST",
                "/files/confirm-upload",
                json={"filePlaceholder": placeholder},
                timeout=CONFIRM_TIMEOUT_SECONDS,
            )
            file = parse_file(confirmed)
            span.set_attribute("slant3d.file_id", file.id)
            logger.info("slant3d_file_uploaded", file_id=file.id, bytes=len(data))
            return file

    async def get_file(self, file_id: str) -> Slant3DFile:
        validate_file_id(file_id)
        with tracer.start_as_current_span("slant3d.get_file"):
            return parse_file(await self._request("GET", f"/files/{file_id}"))

    async def estimate(
        self, file_id: str, *, filament_id: str | None = None, quantity: int = 1
    ) -> dict[str, Any]:
        """Price ``quantity`` prints of the file, in USD."""
        validate_file_id(file_id)
        if filament_id is not None and not _UUID_RE.match(filament_id):
            raise Slant3DError(f"'filament_id' {filament_id!r} is not a Slant 3D filament id")
        if not 1 <= quantity <= MAX_QUANTITY:
            raise Slant3DError(f"'quantity' must be between 1 and {MAX_QUANTITY}")
        options: dict[str, Any] = {"quantity": quantity}
        if filament_id:
            options["filamentId"] = filament_id
        with tracer.start_as_current_span("slant3d.estimate") as span:
            span.set_attribute("slant3d.quantity", quantity)
            data = await self._request(
                "POST", f"/files/{file_id}/estimate", json={"options": options}
            )
            if not isinstance(data, dict):
                raise Slant3DError("Slant 3D returned an unexpected estimate")
            return data

    async def list_filaments(
        self, *, materials: list[str] | None = None, colors: list[str] | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if materials:
            unknown = [m for m in materials if m.upper() not in MATERIALS]
            if unknown:
                raise Slant3DError(f"unknown material(s) {unknown}; choose from {list(MATERIALS)}")
            params["profile"] = ",".join(m.upper() for m in materials)
        if colors:
            params["color"] = ",".join(colors)
        with tracer.start_as_current_span("slant3d.list_filaments"):
            data = await self._request("GET", "/filaments", params=params)
            if not isinstance(data, list):
                raise Slant3DError("Slant 3D returned an unexpected filament list")
            return [f for f in data if isinstance(f, dict)]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _platform(self) -> str:
        """The configured platform, or the account's first one."""
        if self._platform_id:
            return self._platform_id
        platforms = await self._request("GET", "/platforms")
        if isinstance(platforms, list):
            for platform in platforms:
                if isinstance(platform, dict) and isinstance(platform.get("id"), str):
                    self._platform_id = platform["id"]
                    return platform["id"]
        raise Slant3DError(
            "the Slant 3D account has no platform; create one in the Slant 3D "
            "dashboard or set SLANT3D_PLATFORM_ID"
        )

    async def _put(self, url: str, data: bytes) -> None:
        """Send the bytes to the presigned storage URL Slant handed out."""
        if urlparse(url).scheme != "https":
            raise Slant3DError("Slant 3D returned a non-https upload URL; refusing it")
        try:
            # No Authorization header: the URL carries its own signature, and
            # the API key must never reach the storage host.
            response = await self._client.put(
                url,
                content=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise Slant3DError(f"could not reach Slant 3D's file storage: {exc}") from exc
        if response.status_code >= 300:
            raise Slant3DError(f"uploading the file failed with HTTP {response.status_code}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """The ``data`` of a ``{"success": true, "data": ...}`` envelope."""
        if not self._api_key:
            # Normally unreachable: bootstrap skips the adapter without a key.
            raise Slant3DError("Slant 3D is not configured (SLANT3D_API_KEY is unset)")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("slant3d_unreachable", path=path, error=str(exc))
            raise Slant3DError(f"could not reach Slant 3D: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
            reason = _STATUS_REASONS.get(response.status_code, "Slant 3D returned an error")
            detail = _error_message(body)
            logger.warning("slant3d_http_error", path=path, status=response.status_code)
            raise Slant3DError(
                f"{reason} (HTTP {response.status_code})" + (f": {detail}" if detail else "")
            )
        if not isinstance(body, dict) or "data" not in body:
            raise Slant3DError("Slant 3D returned an unexpected response shape")
        return body["data"]


def _error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    parts = [body.get("message"), error]
    seen: list[str] = []
    for part in parts:
        if isinstance(part, str) and part and part not in seen:
            seen.append(part)
    return "; ".join(seen)[:300]


def parse_file(data: Any) -> Slant3DFile:
    """A file record with its metrics renamed to carry their units."""
    if not isinstance(data, dict):
        raise Slant3DError("Slant 3D returned an unexpected file record")
    file_id = data.get("publicFileServiceId")
    if not isinstance(file_id, str) or not file_id:
        raise Slant3DError("Slant 3D returned a file with no id")
    raw = data.get("metrics")
    if not isinstance(raw, dict) or not raw:
        # Older records carry only the deprecated STLMetrics.
        raw = data.get("STLMetrics") if isinstance(data.get("STLMetrics"), dict) else {}
    metrics = {
        name: raw[key] for key, name in _METRICS.items() if isinstance(raw.get(key), (int, float))
    }
    notes = data.get("notes")
    return Slant3DFile(
        id=file_id,
        name=str(data.get("name") or ""),
        metrics=metrics,
        notes=notes if isinstance(notes, str) and notes else None,
    )
