"""Meshy AI HTTP client: the cloud replacement for local mesh authoring.

Meshy generates a textured mesh from a prompt or a reference image. It is
the first cloud adapter: a hosted gateway with no FreeCAD or Blender
container can still produce a printable mesh, because the work runs on
Meshy's GPUs and only an API key lives here.

Every job is asynchronous. A create call returns a task id at once; the
task then moves PENDING -> IN_PROGRESS -> SUCCEEDED (or FAILED/CANCELED)
over one to a few minutes, and a succeeded task carries signed download
URLs that expire after a few days. So the client has three verbs that the
MCP adapter keeps apart: create (spends credits), get (reads status) and
download (writes the mesh into the adapter workspace, where
``twin.commit_geometry`` can take it by ``file_path``).

Key comes from ``MESHY_API_KEY``. It is never read from a file in the repo
and never logged. ``MESHY_BASE_URL`` overrides the API host (tests, a proxy).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.meshy.client")

DEFAULT_BASE_URL = "https://api.meshy.ai"

TaskKind = Literal["text_to_3d", "image_to_3d"]

# Text to 3D is on Meshy's v2 API and image to 3D is still on v1; the task
# id alone does not say which, so every status call names its kind.
_TASK_PATHS: dict[str, str] = {
    "text_to_3d": "/openapi/v2/text-to-3d",
    "image_to_3d": "/openapi/v1/image-to-3d",
}

MESH_FORMATS = ("glb", "stl", "3mf", "obj", "fbx", "usdz")
DEFAULT_FORMATS = ("glb", "stl")

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})

MAX_PROMPT_CHARS = 800
"""Meshy rejects a longer prompt with a 400; refusing it here says why."""

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 120.0

# Task ids are opaque, but they become file names in the workspace, so
# anything outside this alphabet is refused rather than escaped.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# What each documented HTTP status means for the person, not just the code.
_STATUS_REASONS = {
    400: "Meshy rejected the request",
    401: "Meshy refused the API key (MESHY_API_KEY is missing or invalid)",
    402: "the Meshy account has no credits left or is on a free plan",
    404: "Meshy has no such task",
    429: "Meshy rate-limited the request; wait and retry",
}


class MeshyError(RuntimeError):
    """A Meshy call that FAILED: bad arguments, a refused key, no credits,
    an unreachable API or a failed task. Never raised for a task that is
    simply still running; that is a status, not an error."""


class MeshyTask(BaseModel):
    """One Meshy generation task, as its status endpoint reports it."""

    id: str
    kind: TaskKind
    status: str
    progress: int = 0
    model_urls: dict[str, str] = Field(default_factory=dict)
    thumbnail_url: str | None = None
    error: str | None = None
    consumed_credits: int | None = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_STATUSES


def validate_task_id(task_id: str) -> str:
    """The task id, or raise: it is about to name a file."""
    if not _TASK_ID_RE.match(task_id):
        raise MeshyError(f"'task_id' {task_id!r} is not a Meshy task id")
    return task_id


def validate_formats(raw: Any) -> list[str]:
    """Caller-supplied mesh formats, lower-cased and checked."""
    if raw is None:
        return list(DEFAULT_FORMATS)
    if not isinstance(raw, list) or not raw:
        raise MeshyError("'formats' must be a non-empty list")
    formats = [str(f).lower().lstrip(".") for f in raw]
    unknown = [f for f in formats if f not in MESH_FORMATS]
    if unknown:
        raise MeshyError(f"unsupported format(s) {unknown}; choose from {list(MESH_FORMATS)}")
    return formats


class MeshyClient:
    """Meshy AI REST client.

    Construction is pure config, no I/O and no key check, so the gateway
    boots the same whether or not the key works. A failed call raises
    :class:`MeshyError` with a reason the model can act on.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("MESHY_API_KEY", "")
        self._base_url = (
            base_url or os.environ.get("MESHY_BASE_URL", "") or DEFAULT_BASE_URL
        ).rstrip("/")

    @property
    def name(self) -> str:
        return "Meshy"

    # ------------------------------------------------------------------
    # Create (spends credits)
    # ------------------------------------------------------------------

    async def create_text_to_3d(
        self,
        prompt: str,
        *,
        formats: list[str],
        ai_model: str | None = None,
        topology: str | None = None,
        target_polycount: int | None = None,
    ) -> str:
        """Start an untextured preview mesh from ``prompt``; returns the task id."""
        if len(prompt) > MAX_PROMPT_CHARS:
            raise MeshyError(f"'prompt' is {len(prompt)} characters; Meshy's limit is 800")
        body: dict[str, Any] = {"mode": "preview", "prompt": prompt, "target_formats": formats}
        body.update(_optional(ai_model=ai_model, topology=topology))
        if target_polycount is not None:
            body["target_polycount"] = target_polycount
            body["should_remesh"] = True
        return await self._create("text_to_3d", body)

    async def create_refine(
        self,
        preview_task_id: str,
        *,
        formats: list[str],
        texture_prompt: str | None = None,
        enable_pbr: bool = False,
    ) -> str:
        """Texture a succeeded preview; returns the refine task id."""
        validate_task_id(preview_task_id)
        if texture_prompt and len(texture_prompt) > MAX_PROMPT_CHARS:
            raise MeshyError("'texture_prompt' is over Meshy's 800 character limit")
        body: dict[str, Any] = {
            "mode": "refine",
            "preview_task_id": preview_task_id,
            "target_formats": formats,
            "enable_pbr": enable_pbr,
        }
        body.update(_optional(texture_prompt=texture_prompt))
        return await self._create("text_to_3d", body)

    async def create_image_to_3d(
        self,
        image_url: str,
        *,
        formats: list[str],
        should_texture: bool = True,
        ai_model: str | None = None,
        topology: str | None = None,
        target_polycount: int | None = None,
    ) -> str:
        """Start a mesh from a public image URL or a base64 data URI."""
        if not (image_url.startswith("https://") or image_url.startswith("data:image/")):
            raise MeshyError(
                "'image_url' must be a public https:// URL or a data:image/... URI; "
                "Meshy fetches it from its own servers, so a local path cannot work"
            )
        body: dict[str, Any] = {
            "image_url": image_url,
            "target_formats": formats,
            "should_texture": should_texture,
        }
        body.update(_optional(ai_model=ai_model, topology=topology))
        if target_polycount is not None:
            body["target_polycount"] = target_polycount
            body["should_remesh"] = True
        return await self._create("image_to_3d", body)

    async def _create(self, kind: TaskKind, body: dict[str, Any]) -> str:
        with tracer.start_as_current_span("meshy.create") as span:
            span.set_attribute("meshy.kind", kind)
            span.set_attribute("meshy.mode", str(body.get("mode", kind)))
            payload = await self._request("POST", _TASK_PATHS[kind], json=body)
            task_id = payload.get("result")
            if not isinstance(task_id, str) or not task_id:
                raise MeshyError("Meshy accepted the request but returned no task id")
            span.set_attribute("meshy.task_id", task_id)
            logger.info("meshy_task_created", kind=kind, task_id=task_id)
            return task_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_task(self, kind: TaskKind, task_id: str) -> MeshyTask:
        """The task's current status, progress and (once done) its file URLs."""
        validate_task_id(task_id)
        with tracer.start_as_current_span("meshy.get_task") as span:
            span.set_attribute("meshy.kind", kind)
            span.set_attribute("meshy.task_id", task_id)
            payload = await self._request("GET", f"{_TASK_PATHS[kind]}/{task_id}")
            task = _parse_task(payload, kind=kind, task_id=task_id)
            span.set_attribute("meshy.status", task.status)
            return task

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
            raise MeshyError("Meshy returned a non-https download URL; refusing it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        written = 0
        with tracer.start_as_current_span("meshy.download") as span:
            span.set_attribute("meshy.host", parsed.hostname or "")
            try:
                async with self._client.stream(
                    "GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    if response.status_code != 200:
                        raise MeshyError(
                            f"download failed with HTTP {response.status_code}; the "
                            "signed URL may have expired, so fetch the task again"
                        )
                    with partial.open("wb") as fh:
                        async for chunk in response.aiter_bytes():
                            written += len(chunk)
                            if written > MAX_DOWNLOAD_BYTES:
                                raise MeshyError("download is over the 200 MB cap; refusing it")
                            fh.write(chunk)
            except httpx.HTTPError as exc:
                partial.unlink(missing_ok=True)
                span.record_exception(exc)
                raise MeshyError(f"could not reach Meshy's file host: {exc}") from exc
            except MeshyError:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
            span.set_attribute("meshy.bytes", written)
            return written

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._api_key:
            # Normally unreachable: bootstrap skips the adapter without a key.
            raise MeshyError("Meshy is not configured (MESHY_API_KEY is unset)")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("meshy_unreachable", path=path, error=str(exc))
            raise MeshyError(f"could not reach Meshy: {exc}") from exc
        if response.status_code >= 400:
            reason = _STATUS_REASONS.get(response.status_code, "Meshy returned an error")
            detail = _error_message(response)
            logger.warning("meshy_http_error", path=path, status=response.status_code)
            raise MeshyError(
                f"{reason} (HTTP {response.status_code})" + (f": {detail}" if detail else "")
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MeshyError("Meshy returned a response that is not JSON") from exc
        if not isinstance(payload, dict):
            raise MeshyError("Meshy returned an unexpected response shape")
        return payload


def _optional(**fields: str | None) -> dict[str, str]:
    return {k: v for k, v in fields.items() if v}


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if isinstance(message, str):
            return message[:300]
    return ""


def _parse_task(payload: dict[str, Any], *, kind: TaskKind, task_id: str) -> MeshyTask:
    raw_urls = payload.get("model_urls") or {}
    model_urls = {
        str(k): str(v)
        for k, v in raw_urls.items()
        if isinstance(v, str) and v and str(k) in MESH_FORMATS
    }
    task_error = payload.get("task_error") or {}
    error = task_error.get("message") if isinstance(task_error, dict) else None
    progress = payload.get("progress")
    credits = payload.get("consumed_credits")
    return MeshyTask(
        id=str(payload.get("id") or task_id),
        kind=kind,
        status=str(payload.get("status") or "UNKNOWN"),
        progress=progress if isinstance(progress, int) else 0,
        model_urls=model_urls,
        thumbnail_url=payload.get("thumbnail_url") or None,
        error=error or None,
        consumed_credits=credits if isinstance(credits, int) else None,
    )
