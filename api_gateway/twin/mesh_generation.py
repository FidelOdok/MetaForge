"""3D reference mesh providers for illustrative concept models (FORGE-778).

One async callable, the same shape as the image providers:

    generate(image, image_mime, prompt) -> GeneratedMesh

``image`` is the concept image's bytes (image to model) or ``None``, in which
case ``prompt`` is the text to model from. ``GeneratedMesh`` carries the GLB
bytes and which provider, model and task made them, so the twin can say what
produced a mesh.

One implementation, chosen by environment:

* ``METAFORGE_MESH_PROVIDER=tripo`` turns it on. Unset, ``off`` or ``none``
  means no provider. Every mesh is a paid call, so nothing is enabled just
  because ``TRIPO_API_KEY`` happens to be present.
* ``TRIPO_API_KEY``: the Tripo API key, sent as a bearer token to Tripo's API
  only (never to the CDN the GLB is downloaded from). Its value is never
  logged or returned.
* ``METAFORGE_MESH_TIMEOUT_S``: the total seconds a generation may take,
  upload to download (default 600). A task still queued or running then is
  :class:`MeshGenerationTimeoutError`.
* ``METAFORGE_MESH_MODEL_VERSION``: Tripo's ``model`` (default
  :data:`DEFAULT_TRIPO_MODEL`).

The HTTP calls are Tripo's v3 API (``https://openapi.tripo3d.ai/v3``), kept to
four small methods on :class:`TripoMeshGenerator` so a change on Tripo's side
is a change in one place:

1. ``POST /files`` (multipart ``file``) -> ``data.file_token``;
2. ``POST /generation/image-to-model`` ``{"input": token, "model": ...}``,
   ``POST /generation/multiview-to-model``
   ``{"inputs": [{"front": token}, {"left": token}, ...], "model": ...}`` or
   ``POST /generation/text-to-model`` ``{"prompt": ..., "model": ...}``
   -> ``data.task_id``. The generation options (``texture``, ``pbr``,
   ``auto_size``, ``face_limit``, ``smart_low_poly``, ``generate_parts``) go
   in the same body; all three endpoints document them;
3. ``GET /tasks/{task_id}`` until ``data.status`` is terminal;
   ``data.output.model_url`` is the GLB;
4. ``GET <model_url>``, at once: Tripo's model URLs expire within minutes.

Two more paid calls work on a model Tripo already made (concept parts):

* ``POST /mesh/segment`` ``{"input": task id | file token | URL, "model": ...}``
  splits a model into parts (:meth:`TripoMeshGenerator.segment`). Model
  ``v1.0-20250506`` splits by geometry; ``v2.0-20260430`` (beta) adds
  semantic labels and takes ``segmentation_granularity`` and
  ``split_by_connectivity``.
* ``POST /mesh/complete`` ``{"input": segment task id, "part_names": [...],
  "completion_mode": "ai_completion" | "quick_cap"}`` seals or completes
  named parts of a segmented model (:meth:`TripoMeshGenerator.complete`).

Both are polled like a generation and return a GLB; the credits a task
reports, when it reports any, are kept on the result.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.mesh_generation")

PROVIDER_ENV = "METAFORGE_MESH_PROVIDER"
TIMEOUT_ENV = "METAFORGE_MESH_TIMEOUT_S"
MODEL_VERSION_ENV = "METAFORGE_MESH_MODEL_VERSION"
TRIPO_KEY_ENV = "TRIPO_API_KEY"

TRIPO_BASE_URL = "https://openapi.tripo3d.ai/v3"
#: Tripo's recommended generation model; its v3 API requires one.
DEFAULT_TRIPO_MODEL = "v3.1-20260211"
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_POLL_INTERVAL_S = 5.0
#: Seconds for any one HTTP request; the whole generation has its own limit.
REQUEST_TIMEOUT_S = 60.0
MAX_PROMPT_CHARS = 1024
#: Statuses after which a task never succeeds.
FAILED_STATUSES = frozenset({"failed", "cancelled", "banned", "expired"})
_OFF = {"", "off", "none", "disabled", "false", "0"}
_GLB_MAGIC = b"glTF"
#: The views Tripo's multiview endpoint takes, in its positional order.
MULTIVIEW_VIEWS: tuple[str, ...] = ("front", "left", "back", "right")
#: The generation options each endpoint documents; anything else is not sent.
#: Tripo documents the same six on image-to-model, multiview-to-model and
#: text-to-model.
ENDPOINT_OPTIONS: dict[str, frozenset[str]] = {
    mode: frozenset(
        {"texture", "pbr", "auto_size", "face_limit", "smart_low_poly", "generate_parts"}
    )
    for mode in ("image_to_model", "multiview_to_model", "text_to_model")
}


class MeshGenerationError(RuntimeError):
    """Mesh generation did not produce a mesh."""


class MeshGenerationUnavailableError(MeshGenerationError):
    """No mesh provider is configured, or it cannot be used."""


class MeshGenerationTimeoutError(MeshGenerationError):
    """The provider did not finish within the timeout."""


@dataclass(frozen=True)
class GeneratedMesh:
    """One generated GLB and what made it."""

    glb_bytes: bytes
    provider: str
    model_version: str
    task_id: str
    #: ``image_to_model``, ``multiview_to_model`` or ``text_to_model``.
    mode: str
    #: The generation options actually sent to the provider.
    options: dict[str, Any] = dataclass_field(default_factory=dict)
    #: Credits the task reported, when it reported any.
    credits: float | None = None
    #: Other output the task returned (``rendered_image_url``), kept as given.
    output: dict[str, Any] = dataclass_field(default_factory=dict)


#: Tripo's segmentation models: geometry only (the default), and semantic
#: labels (beta). Only the second takes the granularity and connectivity options.
SEGMENT_MODEL_GEOMETRY = "v1.0-20250506"
SEGMENT_MODEL_SEMANTIC = "v2.0-20260430"
SEGMENT_MODELS: tuple[str, ...] = (SEGMENT_MODEL_GEOMETRY, SEGMENT_MODEL_SEMANTIC)
SEGMENT_GRANULARITIES: tuple[str, ...] = ("simple", "balanced", "detailed")
COMPLETION_MODES: tuple[str, ...] = ("ai_completion", "quick_cap")


def credits_of(data: Mapping[str, Any]) -> float | None:
    """The credits a Tripo task reports, under any of the names seen, else ``None``."""
    for key in ("credits_consumed", "consumed_credit", "consumed_credits", "credits", "credit"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


class MeshGenerator(Protocol):
    """``(image | None, image_mime, prompt | None, *, views, options) -> GeneratedMesh``.

    ``views`` maps ``front`` / ``left`` / ``back`` / ``right`` to ``(bytes,
    mime)`` for a multiview model, in which case ``image`` and ``prompt`` are
    ``None``. ``options`` are the generation options; a provider sends only
    those its endpoint documents and reports them on the result.
    """

    def __call__(
        self,
        image: bytes | None,
        image_mime: str,
        prompt: str | None,
        *,
        views: Mapping[str, tuple[bytes, str]] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> Awaitable[GeneratedMesh]: ...


def timeout_from_env(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(TIMEOUT_ENV) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning("mesh_generation_timeout_invalid", value=raw)
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


_KEY_LIKE = re.compile(r"\b(sk|key|tsk)[-_][A-Za-z0-9_*\-]{4,}")


def redact(text: str, secret: str = "") -> str:
    """``text`` with the key, and anything shaped like a key, removed."""
    if secret:
        text = text.replace(secret, "[redacted]")
    return _KEY_LIKE.sub("[redacted]", text)


class TripoMeshGenerator:
    """Tripo3D's v3 API: upload, create a task, poll it, download the GLB."""

    provider = "tripo"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        model_version: str = DEFAULT_TRIPO_MODEL,
        base_url: str = TRIPO_BASE_URL,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        transport: Any = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.model_version = model_version
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._base_url = base_url.rstrip("/")
        self._poll_interval_s = poll_interval_s
        # Injectable for tests: an ``httpx.MockTransport``, and a sleep and
        # clock that do not wait.
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic

    # -- the HTTP layer: one method per Tripo call ---------------------------

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _data(self, response: Any, what: str) -> dict[str, Any]:
        """The ``data`` of a Tripo reply, or a :class:`MeshGenerationError` saying why."""
        if response.status_code == 401:
            raise MeshGenerationError(
                f"tripo refused the {what} (HTTP 401): the API key was not accepted"
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") != 0:
            message = ""
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("suggestion") or "")
            if not message:
                message = response.text
            raise MeshGenerationError(
                f"tripo refused the {what} (HTTP {response.status_code}): "
                f"{redact(message, self._api_key)[:400]}"
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise MeshGenerationError(f"tripo answered the {what} with no data")
        return data

    async def _upload(self, client: Any, image: bytes, mime: str, stem: str = "concept") -> str:
        ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
        response = await client.post(
            f"{self._base_url}/files",
            files={"file": (f"{stem}.{ext}", image, mime)},
            headers=self._auth(),
        )
        token = self._data(response, "upload").get("file_token")
        if not token:
            raise MeshGenerationError("tripo accepted the upload but returned no file_token")
        return str(token)

    async def _create(
        self, client: Any, mode: str, value: Any, options: Mapping[str, Any] | None = None
    ) -> str:
        body: dict[str, Any]
        if mode == "image_to_model":
            path, body = "generation/image-to-model", {"input": value}
        elif mode == "multiview_to_model":
            # The view-key form: one {view: token} object per view.
            path, body = "generation/multiview-to-model", {"inputs": value}
        else:
            path, body = "generation/text-to-model", {"prompt": value}
        body["model"] = self.model_version
        body.update(options or {})
        response = await client.post(f"{self._base_url}/{path}", json=body, headers=self._auth())
        task_id = self._data(response, "task").get("task_id")
        if not task_id:
            raise MeshGenerationError("tripo accepted the task but returned no task_id")
        return str(task_id)

    async def _status(self, client: Any, task_id: str) -> dict[str, Any]:
        response = await client.get(f"{self._base_url}/tasks/{task_id}", headers=self._auth())
        return self._data(response, "task status")

    async def _download(self, client: Any, url: str) -> bytes:
        # No Authorization header: the GLB is on Tripo's CDN, not its API.
        response = await client.get(url)
        if response.status_code >= 400:
            raise MeshGenerationError(
                f"tripo's model could not be downloaded (HTTP {response.status_code})"
            )
        content: bytes = response.content
        return content

    # -- the flow ------------------------------------------------------------

    @staticmethod
    def _model_url(data: Mapping[str, Any]) -> str:
        output = data.get("output") or {}
        if not isinstance(output, Mapping):
            return ""
        for field in ("model_url", "pbr_model_url", "pbr_model", "model"):
            value = output.get(field)
            if isinstance(value, Mapping):  # an older shape: {"url": ...}
                value = value.get("url")
            if isinstance(value, str) and value.startswith("https://"):
                return value
        return ""

    async def __call__(
        self,
        image: bytes | None,
        image_mime: str,
        prompt: str | None,
        *,
        views: Mapping[str, tuple[bytes, str]] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> GeneratedMesh:
        import httpx

        if views:
            mode = "multiview_to_model"
            unknown = sorted(set(views) - set(MULTIVIEW_VIEWS))
            if unknown or "front" not in views or len(views) < 2:
                raise MeshGenerationError(
                    "a multiview model needs a front view and at least one more of "
                    f"{', '.join(MULTIVIEW_VIEWS)}"
                )
        elif image is not None:
            mode = "image_to_model"
        else:
            mode = "text_to_model"
        sent = {k: v for k, v in (options or {}).items() if k in ENDPOINT_OPTIONS[mode]}
        deadline = self._clock() + self._timeout_s
        with tracer.start_as_current_span("mesh_generation.tripo") as span:
            span.set_attribute("mesh_generation.mode", mode)
            span.set_attribute("mesh_generation.model_version", self.model_version)
            task_id = ""
            try:
                async with httpx.AsyncClient(
                    timeout=min(REQUEST_TIMEOUT_S, self._timeout_s), transport=self._transport
                ) as client:
                    value: Any
                    if views:
                        value = []
                        for view in MULTIVIEW_VIEWS:
                            if view in views:
                                data_bytes, view_mime = views[view]
                                token = await self._upload(client, data_bytes, view_mime, view)
                                value.append({view: token})
                    elif image is not None:
                        value = await self._upload(client, image, image_mime)
                    else:
                        value = prompt or ""
                    task_id = await self._create(client, mode, value, sent)
                    span.set_attribute("mesh_generation.task_id", task_id)
                    glb, data = await self._finish(client, task_id, deadline)
            except MeshGenerationError as exc:
                span.record_exception(exc)
                raise
            except httpx.TimeoutException as exc:
                span.record_exception(exc)
                raise MeshGenerationTimeoutError(
                    f"tripo did not answer within {REQUEST_TIMEOUT_S:g}s"
                ) from exc
            except Exception as exc:
                span.record_exception(exc)
                raise MeshGenerationError(
                    f"tripo failed: {type(exc).__name__}: {redact(str(exc), self._api_key)[:400]}"
                ) from exc
        if not glb.startswith(_GLB_MAGIC):
            raise MeshGenerationError(
                f"tripo task {task_id} returned a file that is not a GLB ({len(glb)} bytes)"
            )
        return GeneratedMesh(
            glb_bytes=glb,
            provider=self.provider,
            model_version=self.model_version,
            task_id=task_id,
            mode=mode,
            options=sent,
            credits=credits_of(data),
        )

    async def _finish(
        self, client: Any, task_id: str, deadline: float
    ) -> tuple[bytes, dict[str, Any]]:
        """Poll ``task_id`` to success and download its GLB: ``(glb, task data)``."""
        while True:
            data = await self._status(client, task_id)
            status = str(data.get("status") or "unknown").lower()
            if status == "success":
                break
            if status in FAILED_STATUSES:
                reason = redact(
                    str(data.get("error_message") or data.get("error_code") or ""),
                    self._api_key,
                )[:300]
                raise MeshGenerationError(
                    f"tripo task {task_id} ended {status}" + (f": {reason}" if reason else "")
                )
            # queued, running, or a status Tripo may add: keep waiting.
            if self._clock() + self._poll_interval_s > deadline:
                raise MeshGenerationTimeoutError(
                    f"tripo task {task_id} was still {status} after {self._timeout_s:g}s"
                )
            await self._sleep(self._poll_interval_s)
        url = self._model_url(data)
        if not url:
            raise MeshGenerationError(f"tripo task {task_id} succeeded but returned no model URL")
        return await self._download(client, url), data

    async def _mesh_task(
        self,
        span_name: str,
        mode: str,
        path: str,
        body: dict[str, Any],
        *,
        upload: bytes | None = None,
    ) -> GeneratedMesh:
        """Create one ``/mesh/*`` task (uploading a GLB first if given), poll it, download it."""
        import httpx

        deadline = self._clock() + self._timeout_s
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("mesh_generation.mode", mode)
            task_id = ""
            try:
                async with httpx.AsyncClient(
                    timeout=min(REQUEST_TIMEOUT_S, self._timeout_s), transport=self._transport
                ) as client:
                    if upload is not None:
                        response = await client.post(
                            f"{self._base_url}/files",
                            files={"file": ("concept.glb", upload, "model/gltf-binary")},
                            headers=self._auth(),
                        )
                        token = self._data(response, "upload").get("file_token")
                        if not token:
                            raise MeshGenerationError(
                                "tripo accepted the upload but returned no file_token"
                            )
                        body["input"] = str(token)
                    response = await client.post(
                        f"{self._base_url}/{path}", json=body, headers=self._auth()
                    )
                    task_id = str(self._data(response, "task").get("task_id") or "")
                    if not task_id:
                        raise MeshGenerationError("tripo accepted the task but returned no task_id")
                    span.set_attribute("mesh_generation.task_id", task_id)
                    glb, data = await self._finish(client, task_id, deadline)
            except MeshGenerationError as exc:
                span.record_exception(exc)
                raise
            except httpx.TimeoutException as exc:
                span.record_exception(exc)
                raise MeshGenerationTimeoutError(
                    f"tripo did not answer within {REQUEST_TIMEOUT_S:g}s"
                ) from exc
            except Exception as exc:
                span.record_exception(exc)
                raise MeshGenerationError(
                    f"tripo failed: {type(exc).__name__}: {redact(str(exc), self._api_key)[:400]}"
                ) from exc
        if not glb.startswith(_GLB_MAGIC):
            raise MeshGenerationError(
                f"tripo task {task_id} returned a file that is not a GLB ({len(glb)} bytes)"
            )
        raw_output = data.get("output")
        output = dict(raw_output) if isinstance(raw_output, Mapping) else {}
        kept = {
            k: v
            for k, v in output.items()
            if k in ("rendered_image_url", "part_names", "labels") and v is not None
        }
        return GeneratedMesh(
            glb_bytes=glb,
            provider=self.provider,
            model_version=str(body.get("model") or self.model_version),
            task_id=task_id,
            mode=mode,
            options=dict(body),
            credits=credits_of(data),
            output=kept,
        )

    async def segment(
        self,
        *,
        task_id: str | None = None,
        glb: bytes | None = None,
        url: str | None = None,
        model: str = SEGMENT_MODEL_GEOMETRY,
        granularity: str | None = None,
        split_by_connectivity: bool | None = None,
    ) -> GeneratedMesh:
        """Split a model into parts: one Tripo made (``task_id``), an uploaded GLB or a URL."""
        given = [v for v in (task_id, glb, url) if v]
        if len(given) != 1:
            raise MeshGenerationError("a segmentation needs exactly one task id, GLB or URL")
        if model not in SEGMENT_MODELS:
            raise MeshGenerationError(f"unknown segmentation model {model!r}")
        body: dict[str, Any] = {"model": model}
        if task_id:
            body["input"] = task_id
        elif url:
            if not url.startswith("https://"):
                raise MeshGenerationError("a segmentation URL must be https")
            body["input"] = url
        if model == SEGMENT_MODEL_SEMANTIC:
            if granularity is not None:
                body["segmentation_granularity"] = granularity
            if split_by_connectivity is not None:
                body["split_by_connectivity"] = split_by_connectivity
        return await self._mesh_task(
            "mesh_generation.tripo.segment", "mesh_segment", "mesh/segment", body, upload=glb
        )

    async def complete(
        self,
        *,
        segment_task_id: str,
        part_names: list[str] | None = None,
        completion_mode: str = "ai_completion",
    ) -> GeneratedMesh:
        """Seal (``quick_cap``) or complete (``ai_completion``) parts of a segmented model."""
        if not segment_task_id:
            raise MeshGenerationError("a completion needs the segmentation task id")
        if completion_mode not in COMPLETION_MODES:
            raise MeshGenerationError(f"unknown completion mode {completion_mode!r}")
        body: dict[str, Any] = {"input": segment_task_id, "completion_mode": completion_mode}
        if part_names:
            body["part_names"] = list(part_names)
        return await self._mesh_task(
            "mesh_generation.tripo.complete", "mesh_complete", "mesh/complete", body
        )


def resolve_mesh_generator(env: Mapping[str, str] | None = None) -> TripoMeshGenerator:
    """The configured provider, or :class:`MeshGenerationUnavailableError` saying why."""
    env = os.environ if env is None else env
    provider = (env.get(PROVIDER_ENV) or "").strip().lower()
    if provider in _OFF:
        raise MeshGenerationUnavailableError(
            f"no 3D mesh provider is configured: set {PROVIDER_ENV}=tripo (with "
            f"{TRIPO_KEY_ENV}) to enable AI 3D reference models"
        )
    if provider != "tripo":
        raise MeshGenerationUnavailableError(
            f"{PROVIDER_ENV}={provider!r} is not a known mesh provider; supported: tripo"
        )
    key = (env.get(TRIPO_KEY_ENV) or "").strip()
    if not key:
        raise MeshGenerationUnavailableError(f"{PROVIDER_ENV}=tripo but {TRIPO_KEY_ENV} is not set")
    return TripoMeshGenerator(
        api_key=key,
        timeout_s=timeout_from_env(env),
        model_version=(env.get(MODEL_VERSION_ENV) or "").strip() or DEFAULT_TRIPO_MODEL,
    )
