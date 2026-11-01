"""Tripo MCP adapter: ``tripo.*``, text or image to 3D mesh on Tripo AI.

Six tools, split by what each one does to the world, because that is what
decides whether a person is asked first (``mcp_core.annotations``):

- ``tripo.text_to_3d`` / ``tripo.image_to_3d`` / ``tripo.convert`` start a
  job on Tripo and spend the account's credits (frozen at once, deducted
  only if the job succeeds). The dashboard chat holds each call for approval
  (``_REQUIRES_APPROVAL_TOOL_IDS``).
- ``tripo.get_task`` reads a job's status, optionally waiting for it to
  finish, and ``tripo.get_balance`` reads the account's credits. Read-only.
- ``tripo.download`` writes the finished file into the adapter workspace,
  where ``twin.commit_geometry(file_path=..., format=...)`` stores it as a
  work product. Tripo's signed URLs expire; a mesh that was only ever a URL
  is not versioned, so it has to land here.

``image_to_3d`` and ``convert`` take a workspace ``file_path`` and upload it
to Tripo first, so a photo or a mesh already in the project needs no public
URL. In-process like ``meshy``: no container, only a key.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.tripo.client import (
    CONVERT_FORMATS,
    IMAGE_SUFFIXES,
    MAX_IMAGE_BYTES,
    MAX_MODEL_BYTES,
    MAX_NEGATIVE_PROMPT_CHARS,
    MAX_PROMPT_CHARS,
    MODEL_SUFFIXES,
    RESULT_SUFFIXES,
    TripoClient,
    TripoError,
    validate_task_id,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.tripo.mcp_adapter")

# Network-bound: a JSON call, an upload of up to 150 MB, or one streamed
# download of up to 300 MB.
_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=512, max_cpu_seconds=180, max_disk_mb=1024)

_CAPABILITY = "generative_mesh"
_MAX_WAIT_SECONDS = 120
_DEFAULT_WAIT_SECONDS = 60
_OUTPUT_SUBDIR = Path("output") / "tripo"
_GENERATION_TYPES = frozenset({"text_to_model", "image_to_model", "multiview_to_model"})

_MESH_OPTIONS: dict[str, Any] = {
    "model": {
        "type": "string",
        "description": (
            "Tripo model version, e.g. 'v3.1-20260211' (default, highest detail) or "
            "'P1-20260311' (clean low-poly topology)."
        ),
    },
    "texture": {
        "type": "boolean",
        "description": "False returns bare geometry (default true).",
    },
    "pbr": {
        "type": "boolean",
        "description": "PBR material maps (default true; forces texture on).",
    },
    "face_limit": {
        "type": "integer",
        "minimum": 48,
        "maximum": 2_000_000,
        "description": "Cap the output polycount; omitted means adaptive.",
    },
    "quad": {
        "type": "boolean",
        "description": "Quad mesh instead of triangles; forces FBX output.",
    },
    "smart_low_poly": {
        "type": "boolean",
        "description": "Hand-crafted style low-poly topology; may fail on complex shapes.",
    },
    "auto_size": {
        "type": "boolean",
        "description": (
            "Scale the model to real-world size in METRES. Without it the size "
            "is arbitrary, so set it (or scale later) before printing."
        ),
    },
}
_MESH_OPTION_TYPES: dict[str, type] = {
    "model": str,
    "texture": bool,
    "pbr": bool,
    "face_limit": int,
    "quad": bool,
    "smart_low_poly": bool,
    "auto_size": bool,
}
_CONVERT_OPTIONS: dict[str, Any] = {
    "face_limit": {
        "type": "integer",
        "minimum": 48,
        "description": "Decimate to at most this many faces; omitted keeps the original.",
    },
    "flatten_bottom": {
        "type": "boolean",
        "description": "Flatten the underside, so it sits on a print bed.",
    },
    "pivot_to_center_bottom": {"type": "boolean"},
    "scale_factor": {"type": "number", "exclusiveMinimum": 0},
}
_CONVERT_OPTION_TYPES: dict[str, type] = {
    "face_limit": int,
    "flatten_bottom": bool,
    "pivot_to_center_bottom": bool,
    "scale_factor": float,
}
_SUBMIT_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {"type": "string"},
    },
}


class TripoMcpServer(McpToolServer):
    """MCP wrapper over :class:`TripoClient`."""

    poll_interval_seconds = 3.0

    def __init__(self, client: TripoClient, workspace_dir: Path | None = None) -> None:
        super().__init__(adapter_id="tripo", version="0.1.0")
        self._client = client
        self._workspace = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.text_to_3d",
                adapter_id="tripo",
                name="Tripo Text to 3D",
                description=(
                    "Start a cloud job on Tripo AI that generates a 3D MESH (GLB) "
                    "from a text prompt. Spends Tripo credits (refunded if the job "
                    "fails). Returns at once with a task_id; the mesh takes about "
                    "one to three minutes. Then call tripo.get_task to follow it "
                    "and tripo.download to bring the file into MetaForge, or "
                    "tripo.convert first for STL or 3MF. A mesh is triangles, not "
                    "parametric CAD: use it for organic or decorative parts, "
                    "concept models and printable figures, not for a part that "
                    "needs exact dimensions, holes on a pattern or mating faces "
                    "(use cadquery.* or freecad.* for those)."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "maxLength": MAX_PROMPT_CHARS,
                            "description": "What to model: shape, material, style.",
                        },
                        "negative_prompt": {
                            "type": "string",
                            "maxLength": MAX_NEGATIVE_PROMPT_CHARS,
                            "description": "What the model should NOT have.",
                        },
                        **_MESH_OPTIONS,
                    },
                    "required": ["prompt"],
                },
                output_schema=_SUBMIT_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_text_to_3d,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.image_to_3d",
                adapter_id="tripo",
                name="Tripo Image to 3D",
                description=(
                    "Start a cloud job on Tripo AI that generates a 3D mesh from "
                    "one reference image. Spends Tripo credits. Pass 'file_path' "
                    "for a PNG, JPEG or WebP on the adapter workspace (it is "
                    "uploaded to Tripo first) or 'image_url' for a public https "
                    "image, not both. Returns a task_id at once; follow with "
                    "tripo.get_task and tripo.download."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Workspace-relative image path.",
                        },
                        "image_url": {"type": "string", "description": "Public https URL."},
                        **_MESH_OPTIONS,
                    },
                },
                output_schema=_SUBMIT_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_image_to_3d,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.convert",
                adapter_id="tripo",
                name="Tripo Convert Mesh",
                description=(
                    "Start a Tripo job that converts a mesh to STL, 3MF, OBJ, FBX, "
                    "glTF or USDZ, optionally decimating it ('face_limit') or "
                    "flattening its underside for a print bed. Spends Tripo "
                    "credits. The source is either 'task_id' of a succeeded Tripo "
                    "generation, or 'file_path' of a GLB, glTF, FBX, OBJ or STL on "
                    "the adapter workspace (uploaded first). Returns a new task_id; "
                    "follow with tripo.get_task and tripo.download. For native CAD "
                    "(STEP, SolidWorks) use zoo.convert instead."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "file_path": {"type": "string"},
                        "format": {"type": "string", "enum": list(CONVERT_FORMATS)},
                        **_CONVERT_OPTIONS,
                    },
                    "required": ["format"],
                },
                output_schema=_SUBMIT_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_convert,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.get_task",
                adapter_id="tripo",
                name="Tripo Task Status",
                description=(
                    "Status of a Tripo job: queued, running, success, failed, "
                    "cancelled, banned (the input broke Tripo's content policy) or "
                    "expired, with progress 0-100. Waits up to 'wait_seconds' "
                    "(default 60, max 120) for the job to finish, so call it again "
                    "only if it is still running. Spends nothing and writes "
                    "nothing. A failed job reports Tripo's own reason: tell the "
                    "person, do not resubmit the same input blindly."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "wait_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": _MAX_WAIT_SECONDS,
                            "default": _DEFAULT_WAIT_SECONDS,
                        },
                    },
                    "required": ["task_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "status": {"type": "string"},
                        "progress": {"type": "integer"},
                        "outputs": {"type": "array"},
                        "error": {"type": "string"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_task,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.download",
                adapter_id="tripo",
                name="Tripo Download Mesh",
                description=(
                    "Download a succeeded Tripo job's model into the adapter "
                    "workspace and return its workspace 'file_path' and 'format'. "
                    "To keep it, pass that file_path unchanged to "
                    "twin.commit_geometry with the returned format and "
                    "source_tool 'tripo.download'. Tripo's links expire, so a "
                    "mesh nobody downloaded is lost. 'include_preview' also saves "
                    "the rendered preview image."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "include_preview": {"type": "boolean", "default": False},
                    },
                    "required": ["task_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "file_path": {"type": "string"},
                        "format": {"type": "string"},
                        "files": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_download,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="tripo.get_balance",
                adapter_id="tripo",
                name="Tripo Credit Balance",
                description=(
                    "The Tripo account's available credits and the credits frozen "
                    "for jobs still running. Spends nothing; check it before "
                    "starting a batch of jobs."
                ),
                capability=_CAPABILITY,
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "balance": {"type": "number"},
                        "frozen": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_balance,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_text_to_3d(self, arguments: dict[str, Any]) -> dict[str, Any]:
        prompt = _require_str(arguments, "prompt")
        options = _options(arguments, _MESH_OPTION_TYPES)
        negative = arguments.get("negative_prompt")
        if isinstance(negative, str) and negative.strip():
            options["negative_prompt"] = negative.strip()
        with tracer.start_as_current_span("tripo.mcp.text_to_3d") as span:
            span.set_attribute("tripo.prompt_length", len(prompt))
            task_id = await self._submit(self._client.create_text_to_model(prompt, **options))
            return _submitted(task_id)

    async def handle_image_to_3d(self, arguments: dict[str, Any]) -> dict[str, Any]:
        options = _options(arguments, _MESH_OPTION_TYPES)
        file_path = _optional_str(arguments, "file_path")
        image_url = _optional_str(arguments, "image_url")
        if bool(file_path) == bool(image_url):
            raise ValueError("pass exactly one of 'file_path' or 'image_url'")
        with tracer.start_as_current_span("tripo.mcp.image_to_3d") as span:
            span.set_attribute("tripo.source", "file" if file_path else "url")
            if image_url:
                if urlparse(image_url).scheme != "https":
                    raise ValueError(
                        "'image_url' must be a public https:// URL; for a local image "
                        "pass its workspace 'file_path' instead"
                    )
                source = image_url
            else:
                source = await self._upload(
                    file_path or "", IMAGE_SUFFIXES, MAX_IMAGE_BYTES, "image"
                )
            task_id = await self._submit(self._client.create_image_to_model(source, **options))
            return _submitted(task_id)

    async def handle_convert(self, arguments: dict[str, Any]) -> dict[str, Any]:
        fmt = _require_str(arguments, "format").lower()
        options = _options(arguments, _CONVERT_OPTION_TYPES)
        task_id = _optional_str(arguments, "task_id")
        file_path = _optional_str(arguments, "file_path")
        if bool(task_id) == bool(file_path):
            raise ValueError("pass exactly one of 'task_id' or 'file_path'")
        with tracer.start_as_current_span("tripo.mcp.convert") as span:
            span.set_attribute("tripo.format", fmt)
            if task_id:
                try:
                    source = validate_task_id(task_id)
                except TripoError as exc:
                    raise ValueError(str(exc)) from exc
            else:
                source = await self._upload(
                    file_path or "", MODEL_SUFFIXES, MAX_MODEL_BYTES, "mesh"
                )
            new_id = await self._submit(self._client.create_convert(source, format=fmt, **options))
            return _submitted(new_id)

    async def handle_get_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(arguments, "task_id")
        wait = _coerce_wait(arguments.get("wait_seconds"))
        with tracer.start_as_current_span("tripo.mcp.get_task") as span:
            span.set_attribute("tripo.task_id", task_id)
            span.set_attribute("tripo.wait_seconds", wait)
            deadline = time.monotonic() + wait
            try:
                task = await self._client.get_task(task_id)
                while not task.done and time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval_seconds)
                    task = await self._client.get_task(task_id)
            except TripoError as exc:
                span.record_exception(exc)
                raise ValueError(f"tripo.get_task failed: {exc}") from exc
            span.set_attribute("tripo.status", task.status)
            result: dict[str, Any] = {
                "task_id": task.id,
                "type": task.type,
                "status": task.status,
                "progress": task.progress,
                "outputs": sorted(task.output_urls),
            }
            if task.error:
                result["error"] = task.error
            if task.status == "banned":
                result["error"] = task.error or "the input broke Tripo's content policy"
            if task.credits_consumed is not None:
                result["credits_consumed"] = task.credits_consumed
            return result

    async def handle_download(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(arguments, "task_id")
        include_preview = bool(arguments.get("include_preview", False))
        with tracer.start_as_current_span("tripo.mcp.download") as span:
            span.set_attribute("tripo.task_id", task_id)
            try:
                validate_task_id(task_id)
                task = await self._client.get_task(task_id)
                if task.status != "success":
                    raise TripoError(
                        f"task is {task.status}, not success; call tripo.get_task "
                        "and download once it has finished"
                    )
                if task.model_url is None:
                    raise TripoError(
                        f"the task has no model file; it has {sorted(task.output_urls)}"
                    )
                suffix = _result_suffix(task.model_url, task.type)
                relative = _OUTPUT_SUBDIR / f"{task_id}{suffix}"
                size = await self._client.download(task.model_url, self._workspace / relative)
                files = [
                    {
                        "kind": "model",
                        "format": suffix.lstrip("."),
                        "file_path": relative.as_posix(),
                        "size_bytes": size,
                    }
                ]
                preview_url = task.output_urls.get("rendered_image_url")
                if include_preview and preview_url:
                    preview_suffix = _result_suffix(preview_url, None, default=".png")
                    preview = _OUTPUT_SUBDIR / f"{task_id}_preview{preview_suffix}"
                    preview_size = await self._client.download(
                        preview_url, self._workspace / preview
                    )
                    files.append(
                        {
                            "kind": "preview",
                            "format": preview_suffix.lstrip("."),
                            "file_path": preview.as_posix(),
                            "size_bytes": preview_size,
                        }
                    )
            except TripoError as exc:
                span.record_exception(exc)
                raise ValueError(f"tripo.download failed: {exc}") from exc
            span.set_attribute("tripo.file_count", len(files))
            logger.info("tripo_downloaded", task_id=task_id, format=files[0]["format"])
            return {
                "task_id": task_id,
                "file_path": files[0]["file_path"],
                "format": files[0]["format"],
                "files": files,
            }

    async def handle_get_balance(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("tripo.mcp.get_balance"):
            try:
                return await self._client.get_balance()
            except TripoError as exc:
                raise ValueError(f"tripo.get_balance failed: {exc}") from exc

    async def close(self) -> None:
        """Release the client's HTTP pool."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _input_file(self, relative: str, suffixes: frozenset[str], max_bytes: int) -> Path:
        """A file under the workspace; anything that escapes it is refused."""
        root = self._workspace.resolve()
        path = Path(relative)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root):
            raise TripoError(f"{relative!r} is outside the adapter workspace")
        if resolved.suffix.lower() not in suffixes:
            raise TripoError(f"{relative!r} is not one of {sorted(suffixes)}")
        if not resolved.is_file():
            raise TripoError(f"no file at {relative!r} on the adapter workspace")
        size = resolved.stat().st_size
        if size > max_bytes:
            raise TripoError(
                f"{relative!r} is {size // (1024 * 1024)} MB; Tripo's limit is "
                f"{max_bytes // (1024 * 1024)} MB"
            )
        if size == 0:
            raise TripoError(f"{relative!r} is empty")
        return resolved

    async def _upload(
        self, relative: str, suffixes: frozenset[str], max_bytes: int, what: str
    ) -> str:
        try:
            path = self._input_file(relative, suffixes, max_bytes)
            return await self._client.upload(path.read_bytes(), path.name)
        except TripoError as exc:
            logger.warning("tripo_upload_failed", reason=str(exc))
            raise ValueError(f"could not upload the {what} to Tripo: {exc}") from exc

    @staticmethod
    async def _submit(call: Any) -> str:
        try:
            task_id: str = await call
        except TripoError as exc:
            logger.warning("tripo_submit_failed", reason=str(exc))
            raise ValueError(f"Tripo did not start the job: {exc}") from exc
        return task_id


def _submitted(task_id: str) -> dict[str, Any]:
    return {"task_id": task_id, "status": "queued"}


def _result_suffix(url: str, task_type: str | None, default: str | None = None) -> str:
    """The file suffix a result URL names, checked against the known set."""
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in RESULT_SUFFIXES:
        return suffix
    if default is not None:
        return default
    if task_type in _GENERATION_TYPES:
        # Generation always returns GLB; the CDN path just did not say so.
        return ".glb"
    raise TripoError(f"cannot tell the format of Tripo's result file ({suffix or 'no suffix'})")


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} is required and must be a non-empty string")
    return value.strip()


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key!r} must be a string")
    return value.strip() or None


def _options(arguments: dict[str, Any], types: dict[str, type]) -> dict[str, Any]:
    """The optional settings the caller gave, type-checked."""
    options: dict[str, Any] = {}
    for key, kind in types.items():
        value = arguments.get(key)
        if value is None:
            continue
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{key!r} must be true or false")
            options[key] = value
        elif kind is str:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key!r} must be a non-empty string")
            options[key] = value.strip()
        else:
            if isinstance(value, bool):
                raise ValueError(f"{key!r} must be a number")
            try:
                options[key] = kind(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key!r} must be a number") from exc
            if options[key] <= 0:
                raise ValueError(f"{key!r} must be positive")
    return options


def _coerce_wait(raw: Any) -> int:
    if raw is None:
        return _DEFAULT_WAIT_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("'wait_seconds' must be an integer") from exc
    if value < 0 or value > _MAX_WAIT_SECONDS:
        raise ValueError(f"'wait_seconds' must be in [0, {_MAX_WAIT_SECONDS}]")
    return value
