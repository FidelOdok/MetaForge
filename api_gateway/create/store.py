"""Creation records in SQLite, one row per project.

Writes are compare-and-set on ``version``: a writer that read version 4 and
saves while someone else has already saved version 5 gets a conflict back,
with the newer record, rather than silently overwriting the other view's edit.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from api_gateway.create.models import Creation, now_iso


def default_create_store_path() -> Path:
    """``METAFORGE_CREATE_STORE_PATH``, else ``~/.metaforge/create.db``."""
    override = os.environ.get("METAFORGE_CREATE_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "create.db"


class CreationConflictError(Exception):
    """The record changed since the writer read it."""

    def __init__(self, current: Creation) -> None:
        super().__init__(f"creation changed: now version {current.version}")
        self.current = current


class SqliteCreationStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS creations ("
            " project_id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL)"
        )
        self._db.commit()

    def _read(self, project_id: str) -> Creation:
        row = self._db.execute(
            "SELECT data FROM creations WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return Creation(project_id=project_id)
        return Creation.model_validate_json(row[0])

    def get(self, project_id: str) -> Creation:
        """The record, or an empty version-0 one for a project never opened in Create."""
        with self._lock:
            return self._read(project_id)

    def save(self, creation: Creation, *, expected_version: int | None) -> Creation:
        """Store ``creation`` as the next version, unless the stored one moved on."""
        with self._lock:
            current = self._read(creation.project_id)
            if expected_version is not None and current.version != expected_version:
                raise CreationConflictError(current)
            saved = creation.model_copy(
                update={"version": current.version + 1, "updated_at": now_iso()}
            )
            self._db.execute(
                "INSERT OR REPLACE INTO creations (project_id, version, data) VALUES (?, ?, ?)",
                (saved.project_id, saved.version, saved.model_dump_json()),
            )
            self._db.commit()
            return saved

    def delete(self, project_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM creations WHERE project_id = ?", (project_id,))
            self._db.commit()
