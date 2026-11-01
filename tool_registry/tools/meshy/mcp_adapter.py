"""Meshy MCP adapter: ``meshy.*``, the first cloud adapter.

Five tools, split by what each one does to the world, because that is what
decides whether a person is asked first (``mcp_core.annotations``):

- ``meshy.text_to_3d`` / ``meshy.image_to_3d`` / ``meshy.refine_texture``
  start a job on Meshy and spend the account's credits. Held for approval.
- ``meshy.get_task`` reads a job's status, optionally waiting for it to
  finish. Read-only, so following a job never asks.
- ``meshy.download`` writes the finished mesh into the adapter workspace,
  where ``twin.commit_geometry(file_path=..., format=...)`` stores it as a
  work product. The signed URLs Meshy returns expire within days; a mesh
  that was only ever a URL is not versioned, so it has to land here.

In-process like ``web`` and the distributors: no container, only a key.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.meshy.client import (
    MAX_PROMPT_CHARS,
    MESH_FORMATS,
    MeshyClient,
    MeshyError,
    validate_formats,
    validate_task_id,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.meshy.mcp_adapter")

# Network-bound: a JSON call, or one streamed download of up to 200 MB.
_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=256, max_cpu_seconds=180, max_disk_mb=1024)

_KINDS = ("text_to_3d", "image_to_3d")
_MAX_WAIT_SECONDS = 120
_DEFAULT_WAIT_SECONDS = 60
_OUTPUT_SUBDIR = Path("output") / "meshy"

_FORMATS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "enum": list(MESH_FORMATS)},
    "description": (
        "Mesh formats Meshy should produce (default ['glb', 'stl']). Ask for "
        "'stl' or '3mf' when the part is going to a printer."
    ),
}
_MESH_OPTIONS: dict[str, Any] = {
    "ai_model": {
        "type": "string",
        "description": "Meshy model, e.g. 'latest' (default), 'meshy-6', 'meshy-t2.5'.",
    },
    "topology": {"type": "string", "enum": ["triangle", "quad"]},
    "target_polycount": {
        "type": "integer",
        "minimum": 100,
        "maximum": 300_000,
        "description": "Remesh to about this many faces.",
    },
}
_KIND_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(_KINDS),
    "description": "The 'kind' the create call returned.",
}
_SUBMIT_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "kind": {"type": "string"},
        "status": {"type": "string"},
    },
}


class MeshyMcpServer(McpToolServer):
    """MCP wrapper over :class:`MeshyClient`."""

    poll_interval_seconds = 5.0

    def __init__(self, client: MeshyClient, workspace_dir: Path | None = None) -> None:
        super().__init__(adapter_id="meshy", version="0.1.0")
        self._client = client
        self._workspace = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="meshy.text_to_3d",
                adapter_id="meshy",
                name="Meshy Text to 3D",
                description=(
                    "Start a cloud job on Meshy AI that generates an untextured 3D "
                    "MESH from a text prompt. Spends Meshy credits. Returns at once "
                    "with a task_id; the mesh takes one to a few minutes. Then call "
                    "meshy.get_task to follow it, meshy.refine_texture to texture "
                    "it, and meshy.download to bring the file into MetaForge. A "
                    "mesh is triangles, not parametric CAD: use it for organic or "
                    "decorative parts, concept models and printable figures, not "
                    "for a part that needs exact dimensions, holes on a pattern or "
                    "mating faces (use cadquery.* or freecad.* for those)."
                ),
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "maxLength": MAX_PROMPT_CHARS,
                            "description": "What to model, in plain words.",
                        },
                        "formats": _FORMATS_SCHEMA,
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
                tool_id="meshy.image_to_3d",
                adapter_id="meshy",
                name="Meshy Image to 3D",
                description=(
                    "Start a cloud job on Meshy AI that generates a 3D mesh from "
                    "one reference image. Spends Meshy credits. 'image_url' must "
                    "be a public https URL or a data:image/... base64 URI, because "
                    "Meshy fetches it itself. Returns a task_id at once; follow "
                    "with meshy.get_task and meshy.download."
                ),
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string"},
                        "should_texture": {
                            "type": "boolean",
                            "default": True,
                            "description": "False returns bare geometry, fewer credits.",
                        },
                        "formats": _FORMATS_SCHEMA,
                        **_MESH_OPTIONS,
                    },
                    "required": ["image_url"],
                },
                output_schema=_SUBMIT_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_image_to_3d,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="meshy.refine_texture",
                adapter_id="meshy",
                name="Meshy Refine Texture",
                description=(
                    "Texture a SUCCEEDED meshy.text_to_3d preview. Spends Meshy "
                    "credits and starts a new task (kind 'text_to_3d') with its "
                    "own task_id. Skip it when the part is going to a single-colour "
                    "printer: the preview geometry is already final."
                ),
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "preview_task_id": {"type": "string"},
                        "texture_prompt": {
                            "type": "string",
                            "maxLength": MAX_PROMPT_CHARS,
                            "description": "Optional guidance for the surface look.",
                        },
                        "enable_pbr": {"type": "boolean", "default": False},
                        "formats": _FORMATS_SCHEMA,
                    },
                    "required": ["preview_task_id"],
                },
                output_schema=_SUBMIT_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_refine_texture,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="meshy.get_task",
                adapter_id="meshy",
                name="Meshy Task Status",
                description=(
                    "Status of a Meshy job: PENDING, IN_PROGRESS, SUCCEEDED, "
                    "FAILED or CANCELED, with progress 0-100 and, once succeeded, "
                    "the formats available. Waits up to 'wait_seconds' (default "
                    "60, max 120) for the job to finish, so call it again only if "
                    "it is still running. Spends nothing and writes nothing. A "
                    "FAILED job reports Meshy's own reason: tell the person, do "
                    "not resubmit the same prompt blindly."
                ),
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "kind": _KIND_SCHEMA,
                        "wait_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": _MAX_WAIT_SECONDS,
                            "default": _DEFAULT_WAIT_SECONDS,
                        },
                    },
                    "required": ["task_id", "kind"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "status": {"type": "string"},
                        "progress": {"type": "integer"},
                        "formats": {"type": "array"},
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
                tool_id="meshy.download",
                adapter_id="meshy",
                name="Meshy Download Mesh",
                description=(
                    "Download a SUCCEEDED Meshy job's mesh files into the adapter "
                    "workspace and return each one's workspace 'file_path'. To "
                    "keep the mesh, pass that file_path unchanged to "
                    "twin.commit_geometry with format set to the file's format "
                    "(e.g. 'stl') and source_tool 'meshy.download'. Meshy's own "
                    "links expire within days, so a mesh nobody downloaded is lost."
                ),
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "kind": _KIND_SCHEMA,
                        "formats": _FORMATS_SCHEMA,
                    },
                    "required": ["task_id", "kind"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "files": {"type": "array"},
                        "missing_formats": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_download,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_text_to_3d(self, arguments: dict[str, Any]) -> dict[str, Any]:
        prompt = _require_str(arguments, "prompt")
        with tracer.start_as_current_span("meshy.mcp.text_to_3d") as span:
            span.set_attribute("meshy.prompt_length", len(prompt))
            task_id = await self._submit(
                self._client.create_text_to_3d(
                    prompt,
                    formats=_formats(arguments),
                    **_mesh_options(arguments),
                )
            )
            return _submitted(task_id, "text_to_3d")

    async def handle_image_to_3d(self, arguments: dict[str, Any]) -> dict[str, Any]:
        image_url = _require_str(arguments, "image_url")
        with tracer.start_as_current_span("meshy.mcp.image_to_3d"):
            task_id = await self._submit(
                self._client.create_image_to_3d(
                    image_url,
                    formats=_formats(arguments),
                    should_texture=bool(arguments.get("should_texture", True)),
                    **_mesh_options(arguments),
                )
            )
            return _submitted(task_id, "image_to_3d")

    async def handle_refine_texture(self, arguments: dict[str, Any]) -> dict[str, Any]:
        preview_task_id = _require_str(arguments, "preview_task_id")
        texture_prompt = arguments.get("texture_prompt")
        with tracer.start_as_current_span("meshy.mcp.refine_texture"):
            task_id = await self._submit(
                self._client.create_refine(
                    preview_task_id,
                    formats=_formats(arguments),
                    texture_prompt=texture_prompt if isinstance(texture_prompt, str) else None,
                    enable_pbr=bool(arguments.get("enable_pbr", False)),
                )
            )
            return _submitted(task_id, "text_to_3d")

    async def handle_get_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(arguments, "task_id")
        kind = _require_kind(arguments)
        wait = _coerce_wait(arguments.get("wait_seconds"))
        with tracer.start_as_current_span("meshy.mcp.get_task") as span:
            span.set_attribute("meshy.task_id", task_id)
            span.set_attribute("meshy.wait_seconds", wait)
            deadline = time.monotonic() + wait
            try:
                task = await self._client.get_task(kind, task_id)
                while not task.done and time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval_seconds)
                    task = await self._client.get_task(kind, task_id)
            except MeshyError as exc:
                span.record_exception(exc)
                raise ValueError(f"meshy.get_task failed: {exc}") from exc
            span.set_attribute("meshy.status", task.status)
            result: dict[str, Any] = {
                "task_id": task.id,
                "kind": kind,
                "status": task.status,
                "progress": task.progress,
                "formats": sorted(task.model_urls),
                "thumbnail_url": task.thumbnail_url,
            }
            if task.error:
                result["error"] = task.error
            if task.consumed_credits is not None:
                result["consumed_credits"] = task.consumed_credits
            return result

    async def handle_download(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(arguments, "task_id")
        kind = _require_kind(arguments)
        with tracer.start_as_current_span("meshy.mcp.download") as span:
            span.set_attribute("meshy.task_id", task_id)
            try:
                validate_task_id(task_id)
                formats = validate_formats(arguments.get("formats"))
                task = await self._client.get_task(kind, task_id)
                if task.status != "SUCCEEDED":
                    raise MeshyError(
                        f"task is {task.status}, not SUCCEEDED; call meshy.get_task "
                        "and download once it has finished"
                    )
                files: list[dict[str, Any]] = []
                missing: list[str] = []
                for fmt in formats:
                    url = task.model_urls.get(fmt)
                    if url is None:
                        missing.append(fmt)
                        continue
                    relative = _OUTPUT_SUBDIR / f"{task_id}.{fmt}"
                    size = await self._client.download(url, self._workspace / relative)
                    files.append(
                        {"format": fmt, "file_path": relative.as_posix(), "size_bytes": size}
                    )
            except MeshyError as exc:
                span.record_exception(exc)
                raise ValueError(f"meshy.download failed: {exc}") from exc
            if not files:
                raise ValueError(
                    f"meshy.download: the task produced none of {formats}; it has "
                    f"{sorted(task.model_urls)}. Ask for one of those."
                )
            span.set_attribute("meshy.file_count", len(files))
            logger.info("meshy_downloaded", task_id=task_id, formats=[f["format"] for f in files])
            return {"task_id": task_id, "files": files, "missing_formats": missing}

    async def close(self) -> None:
        """Release the client's HTTP pool."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _submit(call: Any) -> str:
        try:
            task_id: str = await call
        except MeshyError as exc:
            logger.warning("meshy_submit_failed", reason=str(exc))
            raise ValueError(f"Meshy did not start the job: {exc}") from exc
        return task_id


def _submitted(task_id: str, kind: str) -> dict[str, Any]:
    return {"task_id": task_id, "kind": kind, "status": "PENDING"}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} is required and must be a non-empty string")
    return value.strip()


def _formats(arguments: dict[str, Any]) -> list[str]:
    try:
        return validate_formats(arguments.get("formats"))
    except MeshyError as exc:
        raise ValueError(str(exc)) from exc


def _require_kind(arguments: dict[str, Any]) -> Any:
    kind = arguments.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"'kind' must be one of {list(_KINDS)}, as the create call returned")
    return kind


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


def _mesh_options(arguments: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in ("ai_model", "topology"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            options[key] = value.strip()
    polycount = arguments.get("target_polycount")
    if polycount is not None:
        try:
            options["target_polycount"] = int(polycount)
        except (TypeError, ValueError) as exc:
            raise ValueError("'target_polycount' must be an integer") from exc
    return options
