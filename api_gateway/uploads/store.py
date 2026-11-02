"""Where staged uploads live until a person decides them.

An upload is one or more files (a folder arrives as a zip or as several files
in one upload) sent by a plugin client, a signed upload link, the local
plugin or the dashboard. Nothing is in the twin yet: the files sit in a
staging folder, and the record lists each one with the type it would become.
Approving it (``/v1/approvals``, kind ``upload``) imports every accepted file
through ``api_gateway.twin.import_commit``; rejecting it deletes the files.

Records are SQLite rows next to the other gateway stores, so a restart does
not lose an upload someone has not looked at yet. The bytes are plain files
under the staging folder, never in the database.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

UploadStatus = Literal["open", "pending", "approved", "rejected", "expired"]
UploadSource = Literal["inline", "link", "local", "dashboard", "api"]


def default_upload_store_path() -> Path:
    """``METAFORGE_UPLOAD_STORE_PATH``, else ``~/.metaforge/uploads.db``."""
    override = os.environ.get("METAFORGE_UPLOAD_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "uploads.db"


def default_staging_dir() -> Path:
    """``METAFORGE_UPLOAD_STAGING_DIR``, else ``~/.metaforge/staging``."""
    override = os.environ.get("METAFORGE_UPLOAD_STAGING_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "staging"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StagedFile(BaseModel):
    """One file in an upload, as it would be imported."""

    name: str = Field(..., description="Path inside the upload, '/'-separated")
    size: int = 0
    sha256: str = ""
    format: str = ""
    wp_type: str = ""
    domain: str = ""
    # Set when the file cannot be imported (unsupported type, empty, too
    # large); such a file is listed so the person sees it was left out.
    error: str | None = None
    # Set once the upload is approved and this file is in the twin.
    work_product_id: str | None = None


class UploadDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = ""
    approver: str | None = None
    approver_verified: bool = False
    surface: str = "unknown"
    decided_at: str = Field(default_factory=now_iso)


class StagedUpload(BaseModel):
    id: str
    status: UploadStatus = "pending"
    source: UploadSource = "api"
    project_id: str | None = None
    description: str = ""
    requested_by: str | None = None
    agent: str | None = None
    files: list[StagedFile] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    # An open upload (a signed link still accepting files) closes itself
    # here; a pending one is never expired by time.
    expires_at: str | None = None
    decision: UploadDecision | None = None

    @property
    def accepted(self) -> list[StagedFile]:
        return [f for f in self.files if f.error is None]

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.accepted)


class UploadNotFoundError(KeyError):
    """No upload with that id."""


class SqliteUploadStore:
    """Upload records in SQLite; their bytes under ``staging_dir``."""

    def __init__(self, path: str = ":memory:", *, staging_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS uploads ("
            " id TEXT PRIMARY KEY, status TEXT NOT NULL, project_id TEXT,"
            " created_at TEXT NOT NULL, data TEXT NOT NULL)"
        )
        self._db.commit()
        # An in-memory store (tests, or before the lifespan installs the
        # file) stages into a temporary folder rather than the real one.
        self.staging_dir = staging_dir or (
            default_staging_dir()
            if path != ":memory:"
            else Path(tempfile.mkdtemp(prefix="metaforge-staging-"))
        )

    def files_dir(self, upload_id: str) -> Path:
        return self.staging_dir / upload_id

    def file_path(self, upload_id: str, name: str) -> Path:
        """The staged bytes of one file; ``name`` was checked when it was staged."""
        return self.files_dir(upload_id) / Path(*name.split("/"))

    def save(self, upload: StagedUpload) -> StagedUpload:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO uploads (id, status, project_id, created_at, data)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    upload.id,
                    upload.status,
                    upload.project_id,
                    upload.created_at,
                    upload.model_dump_json(),
                ),
            )
            self._db.commit()
        return upload

    def get(self, upload_id: str) -> StagedUpload:
        with self._lock:
            row = self._db.execute("SELECT data FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise UploadNotFoundError(upload_id)
        return StagedUpload.model_validate(json.loads(row[0]))

    def list(
        self, *, project_id: str | None = None, status: str | None = None
    ) -> list[StagedUpload]:
        sql = "SELECT data FROM uploads WHERE 1=1"
        args: list[str] = []
        if project_id:
            sql += " AND project_id = ?"
            args.append(project_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [StagedUpload.model_validate(json.loads(r[0])) for r in rows]

    def discard_files(self, upload_id: str) -> None:
        shutil.rmtree(self.files_dir(upload_id), ignore_errors=True)
