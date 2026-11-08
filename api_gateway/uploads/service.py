"""Stage files, and import or discard them when a person decides.

Every way a file reaches MetaForge from outside (a plugin tool call, a
signed upload link, the local plugin, the dashboard) stages it here first.
Staging checks each file the way the import will (``check_importable``),
opens a zip into its files, and records what each would become. Approval
imports the accepted files through ``commit_import``; nothing reaches the
twin before that.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.import_commit import (
    ImportRejected,
    check_importable,
    commit_import,
    resolve_wp_type,
)
from api_gateway.twin.import_service import (
    ARCHIVE_EXTENSIONS,
    MAX_FILE_SIZE,
    get_extension,
    infer_domain,
)
from api_gateway.uploads.store import (
    SqliteUploadStore,
    StagedFile,
    StagedUpload,
    UploadDecision,
    UploadSource,
    now_iso,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.uploads.service")

#: A folder may hold many files, but not an unbounded number: a zip bomb is
#: a folder too.
MAX_FILES_PER_UPLOAD = 500
#: Everything in one upload together, after a zip is opened.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
#: Names a zip carries that are not the person's files.
_JUNK_PARTS = ("__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini")


class UploadError(ValueError):
    """An upload that cannot be staged or decided, with an HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_name(raw: str) -> str:
    """A '/'-separated relative path with no way out of its upload folder."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or ":" in parts[0]:
        raise UploadError(f"'{raw}' is not a usable file name")
    return str(PurePosixPath(*parts))


def _is_junk(name: str) -> bool:
    parts = name.split("/")
    return any(p in _JUNK_PARTS or p.startswith("._") for p in parts)


def expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes, str | None]]:
    """Open each zip into its files; anything else passes through.

    A zip's entries keep their folder structure under the zip's own name, so
    ``board.zip`` holding ``board.kicad_pcb`` stages as ``board/board.kicad_pcb``.
    The third item is why an entry cannot be staged (it is not read), else None.
    """
    out: list[tuple[str, bytes, str | None]] = []
    total = 0
    for raw_name, content in files:
        name = clean_name(raw_name)
        if get_extension(name) not in ARCHIVE_EXTENSIONS:
            out.append((name, content, None))
            total += len(content)
            continue
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise UploadError(f"{name} is not a readable zip") from exc
        folder = name[: -len(get_extension(name))]
        with archive:
            for info in archive.infolist():
                if info.is_dir() or _is_junk(info.filename):
                    continue
                if info.file_size > MAX_FILE_SIZE:
                    too_big = f"File too large ({info.file_size} bytes). Max: {MAX_FILE_SIZE}"
                    out.append((f"{folder}/{clean_name(info.filename)}", b"", too_big))
                    continue
                total += info.file_size
                if total > MAX_UPLOAD_BYTES or len(out) >= MAX_FILES_PER_UPLOAD:
                    raise UploadError("the upload is too large once its zips are opened", 413)
                out.append((f"{folder}/{clean_name(info.filename)}", archive.read(info), None))
    if len(out) > MAX_FILES_PER_UPLOAD:
        raise UploadError(f"an upload holds at most {MAX_FILES_PER_UPLOAD} files", 413)
    if total > MAX_UPLOAD_BYTES:
        raise UploadError("the upload is too large", 413)
    return out


def _staged_file(
    store: SqliteUploadStore,
    upload_id: str,
    name: str,
    content: bytes,
    wp_type: str | None,
    error: str | None = None,
) -> StagedFile:
    """Check one file, and write it to staging if it can be imported."""
    entry = StagedFile(name=name, size=len(content))
    if error is not None:
        entry.error = error
        return entry
    try:
        ext = check_importable(name, len(content))
        resolved_type = resolve_wp_type(wp_type, ext)
    except ImportRejected as exc:
        entry.error = str(exc)
        return entry
    entry.format = ext.lstrip(".")
    entry.sha256 = hashlib.sha256(content).hexdigest()
    entry.domain = infer_domain(ext)
    entry.wp_type = resolved_type.value
    path = store.file_path(upload_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return entry


def open_upload(
    store: SqliteUploadStore,
    *,
    project_id: str | None,
    source: UploadSource,
    requested_by: str | None = None,
    agent: str | None = None,
    description: str = "",
    expires_in: timedelta | None = None,
) -> StagedUpload:
    """An upload that is still accepting files (a signed link's session)."""
    if project_id:
        try:
            UUID(project_id)
        except ValueError as exc:
            raise UploadError(f"project_id '{project_id}' is not a project id", 422) from exc
    upload = StagedUpload(
        id=uuid4().hex,
        status="open",
        source=source,
        project_id=project_id,
        description=description,
        requested_by=requested_by,
        agent=agent,
        expires_at=(datetime.now(UTC) + expires_in).isoformat() if expires_in else None,
    )
    return store.save(upload)


def add_files(
    store: SqliteUploadStore,
    upload: StagedUpload,
    files: list[tuple[str, bytes]],
    *,
    wp_types: dict[str, str] | None = None,
) -> StagedUpload:
    """Stage ``files`` (zips opened) into an open upload."""
    if upload.status != "open":
        raise UploadError(f"upload {upload.id} is {upload.status}, not accepting files", 409)
    with tracer.start_as_current_span("uploads.add_files") as span:
        span.set_attribute("upload.id", upload.id)
        expanded = expand(files)
        have = {f.name for f in upload.files}
        if len(have) + len(expanded) > MAX_FILES_PER_UPLOAD:
            raise UploadError(f"an upload holds at most {MAX_FILES_PER_UPLOAD} files", 413)
        if upload.total_size + sum(len(c) for _, c, _ in expanded) > MAX_UPLOAD_BYTES:
            raise UploadError("the upload is too large", 413)
        for name, content, error in expanded:
            wp_type = (wp_types or {}).get(name)
            entry = _staged_file(store, upload.id, name, content, wp_type, error)
            upload.files = [f for f in upload.files if f.name != name] + [entry]
        span.set_attribute("upload.file_count", len(upload.files))
        return store.save(upload)


def close_upload(store: SqliteUploadStore, upload: StagedUpload) -> StagedUpload:
    """Stop accepting files and wait for a person. Refuses an empty upload."""
    if upload.status != "open":
        raise UploadError(f"upload {upload.id} is {upload.status}, not open", 409)
    if not upload.accepted:
        reasons = "; ".join(f"{f.name}: {f.error}" for f in upload.files) or "no files"
        raise UploadError(f"nothing in this upload can be imported ({reasons})", 422)
    upload.status = "pending"
    upload.expires_at = None
    logger.info(
        "upload_staged",
        upload_id=upload.id,
        source=upload.source,
        project_id=upload.project_id,
        files=len(upload.accepted),
        rejected=len(upload.files) - len(upload.accepted),
        bytes=upload.total_size,
    )
    return store.save(upload)


def stage(
    store: SqliteUploadStore,
    files: list[tuple[str, bytes]],
    *,
    project_id: str | None,
    source: UploadSource,
    requested_by: str | None = None,
    agent: str | None = None,
    description: str = "",
    wp_types: dict[str, str] | None = None,
) -> StagedUpload:
    """Open, fill and close an upload in one step (inline content, the dashboard)."""
    if not files:
        raise UploadError("no files were sent")
    upload = open_upload(
        store,
        project_id=project_id,
        source=source,
        requested_by=requested_by,
        agent=agent,
        description=description,
    )
    try:
        upload = add_files(store, upload, files, wp_types=wp_types)
        return close_upload(store, upload)
    except UploadError:
        store.discard_files(upload.id)
        upload.status = "rejected"
        store.save(upload)
        raise


def expire_overdue(store: SqliteUploadStore) -> int:
    """Close open uploads past their link's lifetime; returns how many."""
    now = datetime.now(UTC)
    expired = 0
    for upload in store.list(status="open"):
        if upload.expires_at and datetime.fromisoformat(upload.expires_at) < now:
            upload.status = "expired"
            store.discard_files(upload.id)
            store.save(upload)
            expired += 1
    return expired


async def approve(
    store: SqliteUploadStore,
    twin: Any,
    upload: StagedUpload,
    *,
    approver: str | None,
    approver_verified: bool = False,
    surface: str = "unknown",
    agent: str | None = None,
    on_behalf_of: str | None = None,
) -> StagedUpload:
    """Import every accepted file, then drop the staged bytes.

    ``agent`` names an agent that decided for ``on_behalf_of`` under the
    owner's delegation; the decision and each imported file record both.
    """
    if upload.status != "pending":
        raise UploadError(f"upload {upload.id} is {upload.status}; nothing was recorded", 409)
    with tracer.start_as_current_span("uploads.approve") as span:
        span.set_attribute("upload.id", upload.id)
        created_by = f"upload:{upload.requested_by or upload.source}"
        for entry in upload.accepted:
            content = store.file_path(upload.id, entry.name).read_bytes()
            try:
                result = await commit_import(
                    twin,
                    content,
                    entry.name.rsplit("/", 1)[-1],
                    project_id=upload.project_id,
                    domain=entry.domain or None,
                    wp_type=entry.wp_type or None,
                    description=upload.description if len(upload.accepted) == 1 else "",
                    created_by=created_by,
                    extra_metadata={
                        "upload_id": upload.id,
                        "upload_path": entry.name,
                        "upload_source": upload.source,
                        "approved_by": approver,
                        **({"approved_by_agent": agent} if agent else {}),
                    },
                )
                entry.work_product_id = result.id
            except Exception as exc:  # noqa: BLE001 - one bad file must not lose the rest
                span.record_exception(exc)
                entry.error = f"import failed: {exc}"
                logger.warning(
                    "upload_file_import_failed",
                    upload_id=upload.id,
                    name=entry.name,
                    error=str(exc),
                )
        upload.status = "approved"
        upload.decision = UploadDecision(
            decision="approve",
            approver=approver,
            approver_verified=approver_verified,
            surface=surface,
            decided_at=now_iso(),
            agent=agent,
            on_behalf_of=on_behalf_of if agent else None,
        )
        store.discard_files(upload.id)
        imported = sum(1 for f in upload.files if f.work_product_id)
        span.set_attribute("upload.imported", imported)
        logger.info(
            "upload_approved",
            upload_id=upload.id,
            imported=imported,
            approver=approver,
            agent=agent,
        )
        return store.save(upload)


def reject(
    store: SqliteUploadStore,
    upload: StagedUpload,
    *,
    reason: str,
    approver: str | None,
    approver_verified: bool = False,
    surface: str = "unknown",
    agent: str | None = None,
    on_behalf_of: str | None = None,
) -> StagedUpload:
    """Discard the staged files; the record stays, with who said no and why."""
    if upload.status not in ("pending", "open"):
        raise UploadError(f"upload {upload.id} is {upload.status}; nothing was recorded", 409)
    upload.status = "rejected"
    upload.decision = UploadDecision(
        decision="reject",
        reason=reason,
        approver=approver,
        approver_verified=approver_verified,
        surface=surface,
        decided_at=now_iso(),
        agent=agent,
        on_behalf_of=on_behalf_of if agent else None,
    )
    store.discard_files(upload.id)
    logger.info("upload_rejected", upload_id=upload.id, approver=approver, agent=agent)
    return store.save(upload)
