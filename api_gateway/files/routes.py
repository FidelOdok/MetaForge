"""``/v1/files`` (a work product's files) and ``/v1/links`` (signed links).

``/v1/files`` needs the gateway's credential like any route. ``/v1/links``
does not: a link is its own credential (``api_gateway.files.links``), checked
here, and the auth middleware lets ``/v1/links/`` through for that reason.

A download link serves one file. An upload link is an open upload
(``api_gateway.uploads``): ``GET`` shows a page to pick files or a folder in
a browser, ``POST`` (multipart) or ``PUT /{name}`` (a raw body, as
``curl -T`` sends) adds files, and ``POST /finish`` closes it for a person to
approve. A link never imports anything itself.
"""

from __future__ import annotations

import html
from datetime import timedelta
from typing import Any

import structlog
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from api_gateway.files import links, service
from api_gateway.files.upload_page import UPLOAD_PAGE
from api_gateway.uploads import service as uploads
from api_gateway.uploads.routes import get_upload_store, requester
from api_gateway.uploads.store import StagedUpload, UploadNotFoundError
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.files.routes")

files_router = APIRouter(prefix="/v1/files", tags=["files"])
links_router = APIRouter(prefix="/v1/links", tags=["files"])
upload_links_router = APIRouter(prefix="/v1/uploads", tags=["uploads"])


#: Served inline from a download link; anything else is an attachment.
_INLINE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _file_error(exc: service.FileError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


class LinkRequest(BaseModel):
    path: str = Field("", description="A named file; empty for the primary file")
    lifetime_s: int | None = Field(None, description="Seconds; default grows with the size")


class UploadLinkRequest(BaseModel):
    project_id: str | None = None
    description: str = ""
    expected_bytes: int = Field(0, ge=0, description="Total size expected; sets the lifetime")
    lifetime_s: int | None = None
    requested_by: str | None = None


# ── /v1/files ────────────────────────────────────────────────────────────


@files_router.get("/{work_product_id}")
async def list_files(work_product_id: str) -> dict[str, Any]:
    """The files a work product holds, primary first."""
    try:
        wp = await service._work_product(_twin(), work_product_id)  # noqa: SLF001
    except service.FileError as exc:
        raise _file_error(exc) from exc
    return {"work_product_id": work_product_id, "files": service.file_names(wp)}


@files_router.get("/{work_product_id}/read")
async def read_file(
    work_product_id: str,
    path: str = Query("", description="A named file; empty for the primary file"),
    max_bytes: int = Query(service.MAX_INLINE_BYTES, ge=1),
) -> dict[str, Any]:
    """A file's content inline: text, or base64. Too large is refused with its size."""
    try:
        return await service.read(_twin(), work_product_id, path, max_bytes)
    except service.FileError as exc:
        raise _file_error(exc) from exc


@files_router.post("/{work_product_id}/link")
async def create_download_link(work_product_id: str, body: LinkRequest) -> dict[str, Any]:
    """A signed link to the file: ``url`` for an object-store link, else ``path``."""
    try:
        return await service.download_link(
            _twin(), work_product_id, body.path, lifetime_s=body.lifetime_s
        )
    except service.FileError as exc:
        raise _file_error(exc) from exc


# ── upload links ─────────────────────────────────────────────────────────


@upload_links_router.post("/link", status_code=201)
async def create_upload_link(request: Request, body: UploadLinkRequest) -> dict[str, Any]:
    """Open an upload and return a signed link that takes files into it."""
    from api_gateway.auth.approver import approver_from_request

    lifetime = links.lifetime_for(body.expected_bytes, body.lifetime_s)
    store = get_upload_store()
    try:
        upload = uploads.open_upload(
            store,
            project_id=body.project_id,
            source="link",
            requested_by=requester(approver_from_request(request), body.requested_by),
            agent=(request.headers.get("X-MetaForge-Agent") or "").strip()[:100] or None,
            description=body.description,
            expires_in=timedelta(seconds=lifetime),
        )
    except uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    token, link = links.sign("upload", upload.id, lifetime_s=lifetime)
    logger.info("upload_link_issued", upload_id=upload.id, lifetime_s=lifetime)
    return {
        "upload_id": upload.id,
        "approval_id": f"upload:{upload.id}",
        "path": f"/v1/links/{token}",
        "expires_at": link.expires_at,
        "lifetime_s": lifetime,
    }


# ── /v1/links ────────────────────────────────────────────────────────────


def _verify(token: str, kind: links.LinkKind | None = None) -> links.Link:
    try:
        return links.verify(token, kind)
    except links.LinkError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _open_upload(link: links.Link) -> StagedUpload:
    try:
        upload = get_upload_store().get(link.target)
    except UploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="this upload no longer exists") from exc
    if upload.status != "open":
        raise HTTPException(
            status_code=409, detail=f"this upload is {upload.status} and takes no more files"
        )
    return upload


@links_router.get("/{token}")
async def follow_link(token: str) -> Response:
    """A download link's file, or an upload link's page."""
    link = _verify(token)
    if link.kind == "upload":
        upload = _open_upload(link)
        page = UPLOAD_PAGE.replace(
            "__DESCRIPTION__",
            html.escape(upload.description or "Add the files MetaForge asked for."),
        ).replace("__EXPIRES__", html.escape(link.expires_at.replace("T", " ")[:16] + " UTC"))
        return HTMLResponse(
            page,
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )
    try:
        content, filename, _wp = await service.resolve(_twin(), link.target, link.path)
    except service.FileError as exc:
        raise _file_error(exc) from exc
    fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media = _INLINE.get(fmt)
    safe = "".join(c for c in filename.rsplit("/", 1)[-1] if c.isprintable() and c not in '"')
    logger.info("download_link_followed", work_product_id=link.target, name=filename)
    return Response(
        content=content,
        media_type=media or "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if media else "attachment"}; filename="{safe}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
        },
    )


@links_router.post("/{token}")
async def upload_through_link(
    token: str, files: list[UploadFile] = File(..., description="Files, or zips of folders")
) -> StagedUpload:
    """Add files to an open upload (the page, or ``curl -F files=@part.step``)."""
    upload = _open_upload(_verify(token, "upload"))
    payload = [(f.filename or "unnamed", await f.read()) for f in files]
    try:
        return uploads.add_files(get_upload_store(), upload, payload)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@links_router.put("/{token}/{name:path}")
async def put_through_link(token: str, name: str, request: Request) -> StagedUpload:
    """Add one file from a raw body: ``curl -T part.step <link>/part.step``."""
    upload = _open_upload(_verify(token, "upload"))
    try:
        return uploads.add_files(get_upload_store(), upload, [(name, await request.body())])
    except uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@links_router.post("/{token}/finish")
async def finish_upload(token: str) -> dict[str, Any]:
    """Close the upload; it then waits in ``/v1/approvals`` for a person."""
    upload = _open_upload(_verify(token, "upload"))
    try:
        done = uploads.close_upload(get_upload_store(), upload)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        "upload_id": done.id,
        "approval_id": f"upload:{done.id}",
        "status": done.status,
        "accepted": len(done.accepted),
        "left_out": [{"name": f.name, "error": f.error} for f in done.files if f.error],
    }
