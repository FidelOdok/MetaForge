"""A work product's files: which there are, their bytes, and links to them.

A work product has a primary file (its blob) and may have named ones:
``mesh_files`` (a robot description's meshes), ``image_files`` (a render's
other views) and ``design_files`` (a KiCad or firmware project, by path).
Every way out of MetaForge (an inline read, a signed link, the dashboard)
resolves a file through here.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

import structlog

from api_gateway.files import links
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.files.service")

_NAMED_FIELDS = ("mesh_files", "image_files", "design_files")

#: Inline reads stop here unless asked for less: the bytes go through a
#: model's context, and a link is the way to move anything bigger.
MAX_INLINE_BYTES = 2 * 1024 * 1024

#: Formats an inline read returns as text rather than base64.
TEXT_FORMATS = frozenset(
    {
        "txt", "md", "csv", "json", "yaml", "yml", "xml", "html", "svg",
        "gcode", "gco", "c", "h", "cpp", "hpp", "cc", "hh", "ino", "py", "s", "ld",
        "ini", "cmake", "kicad_sch", "kicad_pcb", "kicad_pro", "net", "gbr", "drl",
        "xln", "urdf", "xacro", "sdf", "usda", "step", "stp", "iges", "igs", "map",
        "obj", "log",
    }
)  # fmt: skip


class FileError(ValueError):
    """A file that cannot be found or served, with an HTTP status."""

    def __init__(self, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


async def _work_product(twin: Any, work_product_id: str) -> Any:
    try:
        uid = UUID(work_product_id)
    except ValueError as exc:
        raise FileError(f"'{work_product_id}' is not a work product id", 400) from exc
    wp = await twin.get_work_product(uid)
    if wp is None:
        raise FileError(f"work product {work_product_id} not found")
    return wp


def primary_name(wp: Any) -> str:
    name = str(wp.metadata.get("original_filename") or "")
    return name or (f"{wp.name}.{wp.format}" if wp.format else str(wp.name))


def file_names(wp: Any) -> list[dict[str, Any]]:
    """The files a work product holds, primary first."""
    out: list[dict[str, Any]] = [{"name": primary_name(wp), "primary": True}]
    seen = {out[0]["name"]}
    for field in _NAMED_FIELDS:
        named = wp.metadata.get(field)
        if isinstance(named, dict):
            for name in named:
                if name not in seen:
                    seen.add(name)
                    out.append({"name": str(name), "primary": False})
    return out


def _named_key(wp: Any, path: str) -> str | None:
    for field in _NAMED_FIELDS:
        named = wp.metadata.get(field)
        if isinstance(named, dict) and named.get(path):
            return str(named[path])
    return None


async def resolve(twin: Any, work_product_id: str, path: str = "") -> tuple[bytes, str, Any]:
    """``(content, filename, work_product)`` for the primary file or a named one."""
    from fastapi import HTTPException

    from api_gateway.twin.blob_store import fetch_work_product_blob, resolve_work_product_blob

    wp = await _work_product(twin, work_product_id)
    key = _named_key(wp, path) if path else None
    try:
        if key:
            return fetch_work_product_blob(key), path, wp
        content, filename = resolve_work_product_blob(wp)
    except HTTPException as exc:
        raise FileError(str(exc.detail), exc.status_code) from exc
    except Exception as exc:  # noqa: BLE001 - storage misconfigured / object gone
        raise FileError(f"the file could not be read from storage: {exc}", 502) from exc
    if path and path != filename:
        raise FileError(f"'{path}' is not a file on work product {work_product_id}")
    return content, filename, wp


def _format_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    for ext in ("kicad_sch", "kicad_pcb", "kicad_pro"):
        if base.endswith("." + ext):
            return ext
    return base.rsplit(".", 1)[-1] if "." in base else ""


async def read(
    twin: Any, work_product_id: str, path: str = "", max_bytes: int = MAX_INLINE_BYTES
) -> dict[str, Any]:
    """A file's content inline: text when it is text, else base64.

    Larger than ``max_bytes`` is refused with the file's size, not cut: half
    a STEP file or half a G-code program is wrong rather than short. A text
    file may be read in part with ``max_bytes`` set, and says it was cut.
    """
    with tracer.start_as_current_span("files.read") as span:
        span.set_attribute("files.work_product_id", work_product_id)
        limit = max(1, min(int(max_bytes), MAX_INLINE_BYTES))
        content, filename, wp = await resolve(twin, work_product_id, path)
        fmt = _format_of(filename)
        as_text = fmt in TEXT_FORMATS
        size = len(content)
        span.set_attribute("files.size", size)
        if size > limit and not as_text:
            raise FileError(
                f"{filename} is {size} bytes, over the {limit}-byte inline limit; "
                "ask for a download link instead",
                413,
            )
        body = content[:limit]
        result: dict[str, Any] = {
            "work_product_id": work_product_id,
            "name": filename,
            "format": fmt,
            "size": size,
            "truncated": size > limit,
        }
        if as_text:
            result["encoding"] = "text"
            result["content"] = body.decode("utf-8", errors="replace")
        else:
            result["encoding"] = "base64"
            result["content"] = base64.b64encode(body).decode()
        result["files"] = file_names(wp)
        return result


async def download_link(
    twin: Any,
    work_product_id: str,
    path: str = "",
    *,
    lifetime_s: int | None = None,
) -> dict[str, Any]:
    """A signed link to a file, through the gateway or the object store.

    ``url`` is set only for an object-store link (already absolute). A
    gateway link comes back as ``path``; the caller makes it absolute with
    the address its own client reaches (the sidecar uses its own).
    """
    with tracer.start_as_current_span("files.download_link") as span:
        span.set_attribute("files.work_product_id", work_product_id)
        content, filename, wp = await resolve(twin, work_product_id, path)
        lifetime = links.lifetime_for(len(content), lifetime_s)
        result: dict[str, Any] = {
            "work_product_id": work_product_id,
            "name": filename,
            "size": len(content),
            "files": file_names(wp),
        }
        key = _named_key(wp, path) if path else wp.metadata.get("minio_object_key")
        url = links.object_store_link(str(key), lifetime) if key else None
        if url:
            from datetime import UTC, datetime, timedelta

            result.update(
                via="object_store",
                url=url,
                path=None,
                expires_at=(datetime.now(UTC) + timedelta(seconds=lifetime)).isoformat(),
            )
        else:
            token, link = links.sign("download", work_product_id, path=path, lifetime_s=lifetime)
            result.update(
                via="gateway", url=None, path=f"/v1/links/{token}", expires_at=link.expires_at
            )
        logger.info(
            "download_link_issued",
            work_product_id=work_product_id,
            name=filename,
            via=result["via"],
            lifetime_s=lifetime,
        )
        return result
