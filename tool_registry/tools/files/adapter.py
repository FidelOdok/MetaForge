"""MCP adapter for files in and out of MetaForge.

In: ``files.import`` sends small files as content in the call (every
client); ``files.request_upload`` returns a signed link that takes any size,
from ``curl`` or a browser page (Claude Code, Codex, or a person following a
link from a chat-only client); ``files.import_local`` reads the project
folder (the local plugin only). Every way in stages the files: they reach
the twin only once a person approves the upload in ``/v1/approvals``.

Out: ``files.read`` returns a file's content (small files), ``files.link``
a signed download link (any size), ``files.save_local`` writes into the
project folder (the local plugin only). ``files.list`` names a work
product's files.

A file is also a resource, ``metaforge://files/{work_product_id}`` (its
primary file) or ``metaforge://files/{work_product_id}/{path}`` (a named
one), for clients that attach resources: text formats come back as
``text``, others as a base64 ``blob`` with their MIME type, up to the same
2 MB as ``files.read``.

The adapter takes one service object, in-process in the gateway or the
local plugin (``api_gateway.files.mcp_bindings``), or over HTTP from the
sidecar (``metaforge.mcp.remote_files``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog

from mcp_core.context import current_context
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import (
    ResourceLimits,
    ResourceManifestEntry,
    ResourceNotFoundError,
    ToolManifest,
)
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.files.adapter")

FILE_RESOURCE_PREFIX = "metaforge://files/"
#: Resource reads stop where files.read does; a link moves the rest.
RESOURCE_LIMIT_BYTES = 2 * 1024 * 1024

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "step": "model/step",
    "stp": "model/step",
    "stl": "model/stl",
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "obj": "model/obj",
    "3mf": "model/3mf",
    "json": "application/json",
    "csv": "text/csv",
    "md": "text/markdown",
}


def mime_for(fmt: str, encoding: str) -> str:
    """The MIME type a resource block carries for a file of ``fmt``."""
    return _MIME.get(fmt, "text/plain" if encoding == "text" else "application/octet-stream")


#: Inline content stops here: it goes through the model's context. The
#: gateway enforces the same limit.
INLINE_LIMIT_BYTES = 8 * 1024 * 1024


class FilesService(Protocol):
    @property
    def supports_local_paths(self) -> bool: ...

    def absolute(self, path: str) -> str: ...

    async def stage_inline(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def request_upload(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def upload_status(self, upload_id: str) -> dict[str, Any]: ...

    async def list_files(self, work_product_id: str) -> dict[str, Any]: ...

    async def read(self, work_product_id: str, path: str, max_bytes: int) -> dict[str, Any]: ...

    async def link(
        self, work_product_id: str, path: str, lifetime_s: int | None
    ) -> dict[str, Any]: ...

    async def stage_local(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def save_local(self, request: dict[str, Any]) -> dict[str, Any]: ...


def _limits() -> ResourceLimits:
    return ResourceLimits(max_memory_mb=512, max_cpu_seconds=120, max_disk_mb=1024)


_FILE = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "File name, with folders as 'board/board.kicad_pcb'",
        },
        "text": {"type": "string", "description": "Content of a text file"},
        "content_base64": {"type": "string", "description": "Content of any file, base64"},
        "wp_type": {
            "type": "string",
            "description": "Work product type when the extension says too little "
            "(e.g. 'bom' for a .json, 'prd' for a .md)",
        },
    },
    "required": ["name"],
}

_AWAITS_APPROVAL = (
    "The files are staged, not in the twin: a person approves upload "
    "'{approval_id}' (dashboard Approvals, `forge approvals`, or approvals.decide "
    "where the client can ask them) before MetaForge uses them. Tell them so; "
    "do not approve it yourself."
)


def _project(arguments: dict[str, Any]) -> str | None:
    given = arguments.get("project_id")
    if given:
        return str(given)
    ctx = current_context()
    return str(ctx.project_id) if ctx.project_id else None


class FilesServer(McpToolServer):
    """Serves ``files.*``."""

    def __init__(self, files: FilesService) -> None:
        super().__init__(adapter_id="files", version="0.1.0")
        self._files = files
        self._tool(
            "Import Files",
            "Send small files into MetaForge as content in this call: a reference image, "
            "a STEP, STL, OBJ or 3MF, G-code, a KiCad file, firmware source, a PDF, CSV, "
            "JSON or Markdown, or a zip of a folder. Text goes in 'text', anything else "
            "base64 in 'content_base64'. Up to 8 MB in all; for larger files, or files "
            "you cannot read into this call, use files.request_upload. The files are "
            "staged and checked, then wait for a person to approve the upload before "
            "they enter the twin; the result says which file became what and which "
            "were left out and why.",
            {
                "files": {"type": "array", "items": _FILE, "minItems": 1},
                "project_id": {"type": "string"},
                "description": {"type": "string", "description": "What the files are"},
            },
            ["files"],
            self.import_files,
            tool_id="files.import",
        )
        self._tool(
            "Request Upload Link",
            "A signed link that takes files of any size into MetaForge, valid for a "
            "while (longer for a larger expected_bytes). With a shell, send each file "
            "with `curl -T <file> <url>/<name>` (or `curl -F files=@<file> <url>`), "
            "then `curl -X POST <url>/finish`. Without one (a chat client), give the "
            "person the url: it opens a page where they pick files or a whole folder and "
            "press Finish. The files then wait for a person to approve the upload. "
            "Check progress with files.upload_status.",
            {
                "project_id": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Shown on the upload page: what to send",
                },
                "expected_bytes": {"type": "integer", "minimum": 0},
                "lifetime_s": {"type": "integer", "minimum": 60},
            },
            [],
            self.request_upload,
            tool_id="files.request_upload",
        )
        self._tool(
            "Upload Status",
            "An upload's state (open, pending, approved, rejected, expired), each file "
            "with the type it becomes or why it was left out, and, once approved, the "
            "work product id of each file.",
            {"upload_id": {"type": "string"}},
            ["upload_id"],
            self.upload_status,
            tool_id="files.upload_status",
        )
        self._tool(
            "List Work Product Files",
            "The files a work product holds: its primary file and any named ones (a "
            "KiCad project's files, a render's views, a robot description's meshes).",
            {"work_product_id": {"type": "string"}},
            ["work_product_id"],
            self.list_files,
            tool_id="files.list",
        )
        self._tool(
            "Read Work Product File",
            "A work product's file content in this call: text for text formats "
            "(G-code, CSV, JSON, KiCad, source, STEP), base64 otherwise. Up to 2 MB; a "
            "larger binary file is refused with its size (use files.link), and a larger "
            "text file is cut at max_bytes and says so. 'path' picks a named file.",
            {
                "work_product_id": {"type": "string"},
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1},
            },
            ["work_product_id"],
            self.read,
            tool_id="files.read",
        )
        self._tool(
            "Download Link",
            "A signed link to download a work product's file, for the person to click or "
            "for `curl -o <name> <url>`. Works for any size; valid for a while, longer "
            "for larger files. 'path' picks a named file.",
            {
                "work_product_id": {"type": "string"},
                "path": {"type": "string"},
                "lifetime_s": {"type": "integer", "minimum": 60},
            },
            ["work_product_id"],
            self.link,
            tool_id="files.link",
        )
        self.register_resource(
            manifest=ResourceManifestEntry(
                uri_template="metaforge://files/{work_product_id}/{path}",
                name="Work product file",
                description=(
                    "A work product's file: the primary one without {path}, or a named "
                    "one (see files.list). Text formats as text, others as a base64 "
                    "blob, up to 2 MB; use files.link for anything larger."
                ),
                mime_type="application/octet-stream",
                adapter_id="files",
            ),
            reader=self.read_resource,
            matcher=lambda uri: uri.startswith(FILE_RESOURCE_PREFIX),
        )
        if files.supports_local_paths:
            self._tool(
                "Import Local Files",
                "Stage files or folders from the project folder on this machine (the "
                "local plugin only). Paths are relative to the project folder; nothing "
                "outside it is read. The files then wait for a person to approve them.",
                {
                    "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "project_id": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["paths"],
                self.import_local,
                tool_id="files.import_local",
            )
            self._tool(
                "Save File Locally",
                "Write a work product's file into the project folder on this machine (the "
                "local plugin only). Refuses to overwrite unless 'overwrite' is true.",
                {
                    "work_product_id": {"type": "string"},
                    "path": {"type": "string", "description": "A named file"},
                    "destination": {
                        "type": "string",
                        "description": "File or folder, relative to the project folder",
                    },
                    "overwrite": {"type": "boolean"},
                },
                ["work_product_id"],
                self.save_local,
                tool_id="files.save_local",
            )

    def _tool(
        self,
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        tool_id: str,
    ) -> None:
        # ``tool_id`` is a keyword so it is written as a ``tool_id=`` literal at each
        # call: the tool-id scans in the tests read exactly that.
        self.register_tool(
            manifest=ToolManifest(
                tool_id=tool_id,
                adapter_id="files",
                name=name,
                description=description,
                capability="file_transfer",
                input_schema={"type": "object", "properties": properties, "required": required},
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=handler,
        )

    @staticmethod
    def _who() -> str:
        return current_context().actor_id

    def _awaiting(self, upload: dict[str, Any]) -> dict[str, Any]:
        approval_id = f"upload:{upload.get('id') or upload.get('upload_id')}"
        return {
            **upload,
            "approval_id": approval_id,
            "next": _AWAITS_APPROVAL.format(approval_id=approval_id),
        }

    async def import_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("files.import") as span:
            files = list(arguments.get("files") or [])
            span.set_attribute("files.count", len(files))
            upload = await self._files.stage_inline(
                {
                    "files": files,
                    "project_id": _project(arguments),
                    "description": arguments.get("description") or "",
                    "requested_by": self._who(),
                }
            )
            return self._awaiting(upload)

    async def request_upload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._files.request_upload(
            {
                "project_id": _project(arguments),
                "description": arguments.get("description") or "",
                "expected_bytes": int(arguments.get("expected_bytes") or 0),
                "lifetime_s": arguments.get("lifetime_s"),
                "requested_by": self._who(),
            }
        )
        url = self._files.absolute(str(result.pop("path")))
        return {
            **result,
            "url": url,
            "curl": [
                f"curl -T <file> '{url}/<file name>'",
                f"curl -X POST '{url}/finish'",
            ],
            "next": (
                "Send the files, then finish the upload. A person approves upload "
                f"'{result['approval_id']}' before MetaForge uses the files."
            ),
        }

    async def upload_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._files.upload_status(str(arguments.get("upload_id") or ""))

    async def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._files.list_files(str(arguments.get("work_product_id") or ""))

    async def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._files.read(
            str(arguments.get("work_product_id") or ""),
            str(arguments.get("path") or ""),
            int(arguments.get("max_bytes") or 2 * 1024 * 1024),
        )

    async def link(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._files.link(
            str(arguments.get("work_product_id") or ""),
            str(arguments.get("path") or ""),
            arguments.get("lifetime_s"),
        )
        if not result.get("url") and result.get("path"):
            result["url"] = self._files.absolute(str(result["path"]))
        result.pop("path", None)
        return result

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        """``metaforge://files/{work_product_id}[/{path}]`` as one content block."""
        rest = uri[len(FILE_RESOURCE_PREFIX) :]
        work_product_id, _, path = rest.partition("/")
        if not work_product_id:
            raise ResourceNotFoundError(uri)
        with tracer.start_as_current_span("files.read_resource") as span:
            span.set_attribute("files.work_product_id", work_product_id)
            out = await self._files.read(work_product_id, path, RESOURCE_LIMIT_BYTES)
        if out.get("truncated"):
            raise ValueError(
                f"{out['name']} is {out['size']} bytes, over the {RESOURCE_LIMIT_BYTES}-byte "
                "resource limit; use files.link for a download link"
            )
        block: dict[str, Any] = {"uri": uri, "mimeType": mime_for(out["format"], out["encoding"])}
        block["text" if out["encoding"] == "text" else "blob"] = out["content"]
        return [block]

    async def import_local(self, arguments: dict[str, Any]) -> dict[str, Any]:
        upload = await self._files.stage_local(
            {
                "paths": list(arguments.get("paths") or []),
                "project_id": _project(arguments),
                "description": arguments.get("description") or "",
                "requested_by": self._who(),
            }
        )
        return self._awaiting(upload)

    async def save_local(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._files.save_local(dict(arguments))
