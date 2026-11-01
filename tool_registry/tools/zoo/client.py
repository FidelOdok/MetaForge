"""Zoo (KittyCAD) HTTP client: cloud CAD file conversion and mass properties.

Zoo's file API reads the formats a local FreeCAD container cannot: SolidWorks,
CATIA, NX, Creo, Inventor, Parasolid and ACIS parts, as well as STEP and the
mesh formats. It converts any of them to STEP, STL, GLB, glTF, OBJ, PLY or FBX
and measures volume, mass, centre of mass, surface area and bounding box. The
work runs on Zoo's servers and only an API token lives here.

Every call uploads the file's bytes. A file up to 25 MB is answered in the same
response; a larger one is queued, and the response carries an operation id to
poll at ``/async/operations/{id}``. Statuses are ``queued``, ``uploaded``,
``in_progress``, ``completed`` and ``failed``.

Zoo's text-to-CAD is not here: it is now served only over the Zookeeper copilot
WebSocket and returns KCL source rather than a STEP file.

Token comes from ``ZOO_API_TOKEN``. It is never read from a file in the repo
and never logged. ``ZOO_BASE_URL`` overrides the API host (tests, a proxy).
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.zoo.client")

DEFAULT_BASE_URL = "https://api.zoo.dev"

IMPORT_FORMATS = (
    "step",
    "stl",
    "obj",
    "ply",
    "fbx",
    "gltf",
    "sldprt",
    "parasolid",
    "acis",
    "catia",
    "nx",
    "creo",
    "inventor",
)
EXPORT_FORMATS = ("step", "stl", "glb", "gltf", "obj", "ply", "fbx")

# A suffix that names exactly one format. `.prt` is NX or Creo, so a caller
# with one has to say which.
_SUFFIX_FORMATS: dict[str, str] = {
    ".step": "step",
    ".stp": "step",
    ".stl": "stl",
    ".obj": "obj",
    ".ply": "ply",
    ".fbx": "fbx",
    ".gltf": "gltf",
    ".glb": "gltf",
    ".sldprt": "sldprt",
    ".x_t": "parasolid",
    ".x_b": "parasolid",
    ".sat": "acis",
    ".catpart": "catia",
    ".ipt": "inventor",
}

# One length unit decides the rest, so a result never mixes millimetres with
# square inches.
LENGTH_UNITS = ("mm", "cm", "m", "in", "ft")
AREA_UNIT = {u: f"{u}2" for u in LENGTH_UNITS}
VOLUME_UNIT = {u: f"{u}3" for u in LENGTH_UNITS}

PENDING_STATUSES = frozenset({"queued", "uploaded", "in_progress"})

MAX_UPLOAD_BYTES = 100 * 1024 * 1024

REQUEST_TIMEOUT_SECONDS = 120.0

# Operation ids are UUIDs; they also name a workspace directory.
_OPERATION_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
# Output names come from Zoo and become file names, so only a plain name passes.
_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_STATUS_REASONS = {
    400: "Zoo rejected the request (an unreadable file, or the wrong src_format)",
    401: "Zoo refused the API token (ZOO_API_TOKEN is missing or invalid)",
    402: "the Zoo account needs a payment method or has used its credits",
    403: "the Zoo token is not allowed to make this call",
    404: "Zoo has no such operation",
    413: "the file is too large for Zoo",
    429: "Zoo rate-limited the request; wait and retry",
}


class ZooError(RuntimeError):
    """A Zoo call that FAILED: a refused token, an unreadable file, an
    unreachable API or a failed operation. Never raised for an operation that
    is simply still running; that is a status, not an error."""


class ZooOperation(BaseModel):
    """One Zoo file operation, as the create or status endpoint reports it."""

    id: str
    type: str | None = None
    status: str
    error: str | None = None
    outputs: dict[str, bytes] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return self.status in PENDING_STATUSES


def validate_operation_id(operation_id: str) -> str:
    """The operation id, or raise: it is about to name a directory."""
    if not _OPERATION_ID_RE.match(operation_id):
        raise ZooError(f"'operation_id' {operation_id!r} is not a Zoo operation id")
    return operation_id


def detect_format(path: Path, declared: str | None) -> str:
    """The Zoo import format for ``path``: the caller's, or its suffix's."""
    if declared:
        fmt = declared.lower().lstrip(".")
        if fmt not in IMPORT_FORMATS:
            raise ZooError(f"unsupported src_format {fmt!r}; choose from {list(IMPORT_FORMATS)}")
        return fmt
    suffix = path.suffix.lower()
    if suffix == ".prt":
        raise ZooError("a .prt file is NX or Creo; pass src_format 'nx' or 'creo'")
    fmt = _SUFFIX_FORMATS.get(suffix)
    if fmt is None:
        raise ZooError(
            f"cannot tell the format of {path.name!r} from its suffix; pass src_format "
            f"(one of {list(IMPORT_FORMATS)})"
        )
    return fmt


def validate_export_format(raw: Any) -> str:
    fmt = str(raw or "").lower().lstrip(".")
    if fmt not in EXPORT_FORMATS:
        raise ZooError(f"unsupported output_format {fmt!r}; choose from {list(EXPORT_FORMATS)}")
    return fmt


def safe_output_name(name: str) -> str:
    """The base name of a Zoo output key, or raise if it is not a plain name."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if not _OUTPUT_NAME_RE.match(base) or base in {".", ".."}:
        raise ZooError(f"Zoo returned an output named {name!r}; refusing to write it")
    return base


class ZooClient:
    """Zoo REST client for the file endpoints.

    Construction is pure config, no I/O and no token check, so the gateway
    boots the same whether or not the token works. A failed call raises
    :class:`ZooError` with a reason the model can act on.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_token = api_token or os.environ.get("ZOO_API_TOKEN", "")
        self._base_url = (
            base_url or os.environ.get("ZOO_BASE_URL", "") or DEFAULT_BASE_URL
        ).rstrip("/")

    @property
    def name(self) -> str:
        return "Zoo"

    async def convert(self, data: bytes, *, src_format: str, output_format: str) -> ZooOperation:
        """Convert ``data``; completed at once for a file up to 25 MB."""
        with tracer.start_as_current_span("zoo.convert") as span:
            span.set_attribute("zoo.src_format", src_format)
            span.set_attribute("zoo.output_format", output_format)
            span.set_attribute("zoo.bytes", len(data))
            payload = await self._request(
                "POST",
                f"/file/conversion/{src_format}/{output_format}",
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
            operation = parse_operation(payload)
            span.set_attribute("zoo.status", operation.status)
            logger.info(
                "zoo_conversion_started",
                operation_id=operation.id,
                status=operation.status,
                src_format=src_format,
                output_format=output_format,
            )
            return operation

    async def measure(
        self, endpoint: str, data: bytes, *, src_format: str, **query: Any
    ) -> ZooOperation:
        """One property endpoint (``volume``, ``mass``, ``center-of-mass``,
        ``surface-area`` or ``bounding-box``) over ``data``."""
        with tracer.start_as_current_span("zoo.measure") as span:
            span.set_attribute("zoo.endpoint", endpoint)
            span.set_attribute("zoo.src_format", src_format)
            params = {"src_format": src_format}
            params.update({k: str(v) for k, v in query.items() if v is not None})
            payload = await self._request(
                "POST",
                f"/file/{endpoint}",
                params=params,
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
            operation = parse_operation(payload)
            span.set_attribute("zoo.status", operation.status)
            return operation

    async def get_operation(self, operation_id: str) -> ZooOperation:
        """An operation's current status and, once completed, its result."""
        validate_operation_id(operation_id)
        with tracer.start_as_current_span("zoo.get_operation") as span:
            span.set_attribute("zoo.operation_id", operation_id)
            payload = await self._request("GET", f"/async/operations/{operation_id}")
            operation = parse_operation(payload)
            span.set_attribute("zoo.status", operation.status)
            return operation

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self._api_token:
            # Normally unreachable: bootstrap skips the adapter without a token.
            raise ZooError("Zoo is not configured (ZOO_API_TOKEN is unset)")
        all_headers = {"Authorization": f"Bearer {self._api_token}", **(headers or {})}
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=all_headers, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("zoo_unreachable", path=path, error=str(exc))
            raise ZooError(f"could not reach Zoo: {exc}") from exc
        if response.status_code >= 400:
            reason = _STATUS_REASONS.get(response.status_code, "Zoo returned an error")
            detail = _error_message(response)
            logger.warning("zoo_http_error", path=path, status=response.status_code)
            raise ZooError(
                f"{reason} (HTTP {response.status_code})" + (f": {detail}" if detail else "")
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZooError("Zoo returned a response that is not JSON") from exc
        if not isinstance(payload, dict):
            raise ZooError("Zoo returned an unexpected response shape")
        return payload


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        message = body.get("message") or body.get("error_code")
        if isinstance(message, str):
            return message[:300]
    return ""


def parse_operation(payload: dict[str, Any]) -> ZooOperation:
    """A create or status response as a :class:`ZooOperation`.

    ``outputs`` maps a file name to base64 bytes; a sync conversion of the
    older shape carries one ``output`` string instead.
    """
    operation_id = payload.get("id")
    if not isinstance(operation_id, str) or not operation_id:
        raise ZooError("Zoo returned an operation with no id")
    outputs: dict[str, bytes] = {}
    raw_outputs = payload.get("outputs")
    if isinstance(raw_outputs, dict):
        for key, value in raw_outputs.items():
            if isinstance(value, str):
                outputs[str(key)] = _b64(value)
    elif isinstance(payload.get("output"), str):
        fmt = str(payload.get("output_format") or "bin")
        outputs[f"output.{fmt}"] = _b64(payload["output"])
    error = payload.get("error")
    return ZooOperation(
        id=operation_id,
        type=payload.get("type") if isinstance(payload.get("type"), str) else None,
        status=str(payload.get("status") or "unknown"),
        error=error if isinstance(error, str) and error else None,
        outputs=outputs,
        raw={k: v for k, v in payload.items() if k not in ("outputs", "output")},
    )


def _b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ZooError("Zoo returned an output that is not valid base64") from exc
