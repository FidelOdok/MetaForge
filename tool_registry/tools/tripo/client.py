"""Tripo AI HTTP client: cloud text or image to 3D mesh, and mesh conversion.

Tripo generates a textured mesh from a prompt or a reference image, and
converts a mesh it made (or one uploaded to it) to STL, 3MF, OBJ, FBX, glTF or
USDZ. Unlike Meshy it takes an uploaded image, so a picture already in the
workspace works without a public URL.

This is Tripo's v3 API. Every job is asynchronous: a create call returns a
``task_id`` at once; the task then moves queued -> running -> success (or
failed, cancelled, banned, expired). A succeeded task carries signed download
URLs that expire, so the client keeps create (freezes credits, deducted only
on success), get (reads status) and download (writes into the workspace,
where ``twin.commit_geometry`` can take it by ``file_path``) apart.

Key comes from ``TRIPO_API_KEY``; keys are region-bound. The default host is
the global region; ``TRIPO_BASE_URL`` points a China-region key at
``https://openapi.tripo3d.com/v3`` (or a proxy, or tests). The key is never
read from a file in the repo and never logged.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.tripo.client")

DEFAULT_BASE_URL = "https://openapi.tripo3d.ai/v3"

CONVERT_FORMATS = ("stl", "3mf", "obj", "fbx", "gltf", "usdz")

# What Tripo accepts as an upload, by suffix, with its documented size cap.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MODEL_SUFFIXES = frozenset({".glb", ".gltf", ".fbx", ".obj", ".stl"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MODEL_BYTES = 150 * 1024 * 1024

# Suffixes a downloaded result may carry; anything else is refused rather
# than written under a name Tripo chose.
RESULT_SUFFIXES = frozenset(
    {".glb", ".gltf", ".fbx", ".obj", ".stl", ".3mf", ".usdz", ".zip", ".png", ".jpg", ".webp"}
)

PENDING_STATUSES = frozenset({"queued", "running"})

MAX_PROMPT_CHARS = 1024
MAX_NEGATIVE_PROMPT_CHARS = 255

MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024

REQUEST_TIMEOUT_SECONDS = 30.0
UPLOAD_TIMEOUT_SECONDS = 120.0
DOWNLOAD_TIMEOUT_SECONDS = 120.0

# Task ids look like ``task_abc123``; they become file names, so anything
# outside this alphabet is refused rather than escaped.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")

_STATUS_REASONS = {
    400: "Tripo rejected the request",
    401: "Tripo refused the API key (TRIPO_API_KEY is missing, invalid, or for the other region)",
    403: "the Tripo account has too few credits or no access to this call",
    404: "Tripo has no such task",
    429: "Tripo rate-limited the request or the concurrency limit is reached; wait and retry",
}

# Tripo's own codes that say more than the HTTP status does.
_CODE_REASONS = {
    2000: "Tripo's concurrency limit is reached; wait for a running task to finish",
    2008: "Tripo refused the input under its content policy",
    2010: "the Tripo account does not have enough credits",
    2018: "the model is too complex for Tripo; reduce its polycount",
}


class TripoError(RuntimeError):
    """A Tripo call that FAILED: bad arguments, a refused key, no credits, an
    unreachable API or a failed task. Never raised for a task that is simply
    still running; that is a status, not an error."""


class TripoTask(BaseModel):
    """One Tripo task, as ``GET /tasks/{task_id}`` reports it."""

    id: str
    type: str | None = None
    status: str
    progress: int = 0
    output_urls: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    credits_consumed: float | None = None

    @property
    def done(self) -> bool:
        return self.status not in PENDING_STATUSES

    @property
    def model_url(self) -> str | None:
        return self.output_urls.get("model_url")


def validate_task_id(task_id: str) -> str:
    """The task id, or raise: it is about to name a file."""
    if not _TASK_ID_RE.match(task_id):
        raise TripoError(f"'task_id' {task_id!r} is not a Tripo task id")
    return task_id


def validate_convert_format(raw: Any) -> str:
    fmt = str(raw or "").lower().lstrip(".")
    if fmt not in CONVERT_FORMATS:
        raise TripoError(f"unsupported format {fmt!r}; choose from {list(CONVERT_FORMATS)}")
    return fmt


class TripoClient:
    """Tripo v3 REST client.

    Construction is pure config, no I/O and no key check, so the gateway
    boots the same whether or not the key works. A failed call raises
    :class:`TripoError` with a reason the model can act on.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("TRIPO_API_KEY", "")
        self._base_url = (
            base_url or os.environ.get("TRIPO_BASE_URL", "") or DEFAULT_BASE_URL
        ).rstrip("/")

    @property
    def name(self) -> str:
        return "Tripo"

    # ------------------------------------------------------------------
    # Upload (free, but sends the file to Tripo)
    # ------------------------------------------------------------------

    async def upload(self, data: bytes, filename: str) -> str:
        """Upload an image or a mesh; returns its ``file_token``."""
        with tracer.start_as_current_span("tripo.upload") as span:
            span.set_attribute("tripo.bytes", len(data))
            payload = await self._request(
                "POST",
                "/files",
                files={"file": (filename, data)},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
            token = payload.get("file_token")
            if not isinstance(token, str) or not token:
                raise TripoError("Tripo accepted the upload but returned no file_token")
            logger.info("tripo_file_uploaded", bytes=len(data))
            return token

    # ------------------------------------------------------------------
    # Create (spends credits)
    # ------------------------------------------------------------------

    async def create_text_to_model(self, prompt: str, **options: Any) -> str:
        """Start a mesh from ``prompt``; returns the task id."""
        if len(prompt) > MAX_PROMPT_CHARS:
            raise TripoError(
                f"'prompt' is {len(prompt)} characters; Tripo's limit is {MAX_PROMPT_CHARS}"
            )
        negative = options.get("negative_prompt")
        if isinstance(negative, str) and len(negative) > MAX_NEGATIVE_PROMPT_CHARS:
            raise TripoError(
                f"'negative_prompt' is over Tripo's {MAX_NEGATIVE_PROMPT_CHARS} character limit"
            )
        return await self._create("/generation/text-to-model", {"prompt": prompt, **options})

    async def create_image_to_model(self, image: str, **options: Any) -> str:
        """Start a mesh from a ``file_token`` or a public https image URL."""
        return await self._create("/generation/image-to-model", {"input": image, **options})

    async def create_convert(self, source: str, *, format: str, **options: Any) -> str:
        """Convert a task's model or an uploaded ``file_token`` to ``format``."""
        body = {"input": source, "format": validate_convert_format(format).upper(), **options}
        return await self._create("/models/convert", body)

    async def _create(self, path: str, body: dict[str, Any]) -> str:
        with tracer.start_as_current_span("tripo.create") as span:
            span.set_attribute("tripo.endpoint", path)
            payload = await self._request("POST", path, json=_drop_none(body))
            task_id = payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise TripoError("Tripo accepted the request but returned no task_id")
            span.set_attribute("tripo.task_id", task_id)
            logger.info("tripo_task_created", endpoint=path, task_id=task_id)
            return task_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_task(self, task_id: str) -> TripoTask:
        """The task's current status, progress and (once done) its file URLs."""
        validate_task_id(task_id)
        with tracer.start_as_current_span("tripo.get_task") as span:
            span.set_attribute("tripo.task_id", task_id)
            payload = await self._request("GET", f"/tasks/{task_id}")
            task = _parse_task(payload, task_id=task_id)
            span.set_attribute("tripo.status", task.status)
            return task

    async def get_balance(self) -> dict[str, float]:
        """Available and frozen (held for running tasks) credits."""
        with tracer.start_as_current_span("tripo.get_balance"):
            payload = await self._request("GET", "/account/balance")
            return {
                "balance": _number(payload.get("balance")),
                "frozen": _number(payload.get("frozen")),
            }

    # ------------------------------------------------------------------
    # Download (writes into the workspace)
    # ------------------------------------------------------------------

    async def download(self, url: str, dest: Path) -> int:
        """Stream one result file to ``dest``; returns the bytes written.

        Writes to a temporary sibling and renames, so an interrupted
        download never leaves a truncated mesh where a whole one is expected.
        """
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise TripoError("Tripo returned a non-https download URL; refusing it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        written = 0
        with tracer.start_as_current_span("tripo.download") as span:
            span.set_attribute("tripo.host", parsed.hostname or "")
            try:
                async with self._client.stream(
                    "GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    if response.status_code != 200:
                        raise TripoError(
                            f"download failed with HTTP {response.status_code}; the "
                            "signed URL may have expired, so fetch the task again"
                        )
                    with partial.open("wb") as fh:
                        async for chunk in response.aiter_bytes():
                            written += len(chunk)
                            if written > MAX_DOWNLOAD_BYTES:
                                raise TripoError("download is over the 300 MB cap; refusing it")
                            fh.write(chunk)
            except httpx.HTTPError as exc:
                partial.unlink(missing_ok=True)
                span.record_exception(exc)
                raise TripoError(f"could not reach Tripo's file host: {exc}") from exc
            except TripoError:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
            span.set_attribute("tripo.bytes", written)
            return written

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """The ``data`` object of a ``{"code": 0, "data": ...}`` envelope."""
        if not self._api_key:
            # Normally unreachable: bootstrap skips the adapter without a key.
            raise TripoError("Tripo is not configured (TRIPO_API_KEY is unset)")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("tripo_unreachable", path=path, error=str(exc))
            raise TripoError(f"could not reach Tripo: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        code = body.get("code") if isinstance(body, dict) else None
        if response.status_code >= 400 or (isinstance(code, int) and code != 0):
            reason = _CODE_REASONS.get(code) if isinstance(code, int) else None
            reason = reason or _STATUS_REASONS.get(response.status_code, "Tripo returned an error")
            detail = _error_message(body)
            logger.warning("tripo_http_error", path=path, status=response.status_code, code=code)
            raise TripoError(
                f"{reason} (HTTP {response.status_code}"
                + (f", code {code}" if code is not None else "")
                + ")"
                + (f": {detail}" if detail else "")
            )
        if not isinstance(body, dict):
            raise TripoError("Tripo returned a response that is not a JSON object")
        data = body.get("data")
        if not isinstance(data, dict):
            raise TripoError("Tripo returned an unexpected response shape")
        return data


def _drop_none(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    parts = [body.get("message"), body.get("suggestion")]
    text = " ".join(p for p in parts if isinstance(p, str) and p)
    return text[:300]


def _parse_task(payload: dict[str, Any], *, task_id: str) -> TripoTask:
    raw_output = payload.get("output") or {}
    output_urls = (
        {
            str(k): v
            for k, v in raw_output.items()
            if str(k).endswith("_url") and isinstance(v, str) and v
        }
        if isinstance(raw_output, dict)
        else {}
    )
    error = payload.get("error_message")
    if not error and payload.get("error_code") is not None:
        error = f"error code {payload['error_code']}"
    progress = payload.get("progress")
    credits = payload.get("credits_consumed")
    return TripoTask(
        id=str(payload.get("task_id") or task_id),
        type=payload.get("type") if isinstance(payload.get("type"), str) else None,
        status=str(payload.get("status") or "unknown"),
        progress=progress if isinstance(progress, int) else 0,
        output_urls=output_urls,
        error=error if isinstance(error, str) and error else None,
        credits_consumed=float(credits) if isinstance(credits, (int, float)) else None,
    )
