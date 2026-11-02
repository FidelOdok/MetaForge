"""``/v1/uploads``: stage files from outside, read them back, and list them.

Deciding an upload is ``/v1/approvals`` (kind ``upload``), like every other
approval, so the dashboard inbox, ``forge approvals`` and the plugin's
``approvals.decide`` all reach it.

The dashboard may approve in the same request (``approve=true``): a person
picking files in the dashboard is the decision. An agent may not; its files
wait for a person.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from api_gateway.auth.approver import approver_from_request
from api_gateway.uploads import service
from api_gateway.uploads.store import SqliteUploadStore, StagedUpload, UploadNotFoundError
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.uploads.routes")

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

SURFACE_HEADER = "X-MetaForge-Surface"
AGENT_HEADER = "X-MetaForge-Agent"

_store: SqliteUploadStore | None = None

#: Served inline only as these types; anything else downloads. SVG and HTML
#: from outside are active content, so they never render on this origin.
_INLINE_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "csv": "text/plain; charset=utf-8",
    "json": "text/plain; charset=utf-8",
    "gcode": "text/plain; charset=utf-8",
    "gco": "text/plain; charset=utf-8",
    "c": "text/plain; charset=utf-8",
    "h": "text/plain; charset=utf-8",
    "cpp": "text/plain; charset=utf-8",
    "hpp": "text/plain; charset=utf-8",
}


def get_upload_store() -> SqliteUploadStore:
    """The process's store; an in-memory one until the lifespan installs the file."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SqliteUploadStore()
    return _store


def init_upload_store(store: SqliteUploadStore | None) -> None:
    """Install ``store`` (the lifespan), or ``None`` to start over (tests)."""
    global _store  # noqa: PLW0603
    _store = store


def _get(upload_id: str) -> StagedUpload:
    try:
        return get_upload_store().get(upload_id)
    except UploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"upload '{upload_id}' not found") from exc


@router.post("", response_model=StagedUpload, status_code=201)
async def create_upload(
    request: Request,
    files: list[UploadFile] = File(..., description="Files, or zips of folders"),
    project_id: str | None = Form(None, description="Project the files are for"),
    description: str = Form("", description="What the files are"),
    wp_types: str | None = Form(
        None, description='JSON object of file name to work product type, e.g. {"a.json": "bom"}'
    ),
    approve: bool = Form(False, description="Import now; refused for an agent caller"),
) -> StagedUpload:
    """Stage files; they enter the twin only once a person approves the upload."""
    from api_gateway.twin.routes import get_twin

    surface = (request.headers.get(SURFACE_HEADER) or "").strip().lower() or "unknown"
    if approve and surface == "agent":
        raise HTTPException(
            status_code=403,
            detail="an agent cannot approve its own upload; it waits for a person",
        )
    try:
        types: dict[str, str] = json.loads(wp_types) if wp_types else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="wp_types must be a JSON object") from exc
    if not isinstance(types, dict):
        raise HTTPException(status_code=422, detail="wp_types must be a JSON object")

    approver = approver_from_request(request)
    payload = [(f.filename or "unnamed", await f.read()) for f in files]
    store = get_upload_store()
    try:
        upload = service.stage(
            store,
            payload,
            project_id=project_id,
            source="dashboard" if surface == "dashboard" else "api",
            requested_by=approver.actor_id,
            agent=(request.headers.get(AGENT_HEADER) or "").strip()[:100] or None,
            description=description,
            wp_types={str(k): str(v) for k, v in types.items()},
        )
        if approve:
            upload = await service.approve(
                store,
                get_twin(),
                upload,
                approver=approver.actor_id,
                approver_verified=approver.verified,
                surface=surface,
            )
    except service.UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return upload


@router.get("", response_model=list[StagedUpload])
async def list_uploads(
    project_id: str | None = Query(None),
    status: str | None = Query(None, description="open, pending, approved, rejected, expired"),
) -> list[StagedUpload]:
    """Uploads, newest first."""
    store = get_upload_store()
    service.expire_overdue(store)
    return store.list(project_id=project_id, status=status)


@router.get("/{upload_id}", response_model=StagedUpload)
async def get_upload(upload_id: str) -> StagedUpload:
    return _get(upload_id)


@router.get("/{upload_id}/files/{name:path}")
async def get_staged_file(upload_id: str, name: str) -> Response:
    """A staged file's bytes, for the person reviewing the upload.

    Gone once the upload is decided: an approved file is then read from its
    work product, a rejected one is deleted.
    """
    upload = _get(upload_id)
    entry = next((f for f in upload.files if f.name == name and f.error is None), None)
    path = get_upload_store().file_path(upload_id, name) if entry else None
    if entry is None or path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"'{name}' is not staged in this upload")
    media = _INLINE_TYPES.get(entry.format)
    filename = "".join(c for c in name.rsplit("/", 1)[-1] if c.isprintable() and c not in '"')
    headers: dict[str, Any] = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Content-Disposition": (
            f'inline; filename="{filename}"' if media else f'attachment; filename="{filename}"'
        ),
    }
    return Response(
        content=path.read_bytes(),
        media_type=media or "application/octet-stream",
        headers=headers,
    )
