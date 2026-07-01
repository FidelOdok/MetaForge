"""SQLite session ledger (MET-547, Phase 4).

Durable record of runs and their events so a session survives process restarts
and is searchable. Uses SQLite FTS5 for full-text search over event detail when
the runtime's SQLite build supports it, falling back to a ``LIKE`` scan
otherwise -- so the ledger works on any stdlib ``sqlite3`` (FTS5 is a compile
option that CI images may or may not ship).

Transport-free and stdlib-only; the gateway/persistence layer chooses the DB
path. ``:memory:`` is the default for tests.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from orchestrator.harness.runs import Run

logger = structlog.get_logger(__name__)


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


@dataclass(frozen=True)
class LedgerEvent:
    """One recorded event in a run's history."""

    run_id: str
    ts: float
    kind: str
    detail: str


class SqliteRunLedger:
    """Persist runs + events to SQLite, with FTS5 (or LIKE-fallback) search."""

    def __init__(self, path: str = ":memory:", *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._fts = _fts5_available(self._conn)
        self._init_schema()
        logger.info("ledger_opened", path=path, fts5=self._fts)

    @property
    def fts_enabled(self) -> bool:
        return self._fts

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id          TEXT PRIMARY KEY,
                status      TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                request     TEXT NOT NULL,
                result      TEXT,
                error       TEXT
            );
            CREATE TABLE IF NOT EXISTS run_events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  TEXT NOT NULL,
                ts      REAL NOT NULL,
                kind    TEXT NOT NULL,
                detail  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);
            """
        )
        if self._fts:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS run_events_fts "
                "USING fts5(detail, run_id UNINDEXED, kind UNINDEXED)"
            )
        self._conn.commit()

    def record_run(self, run: Run) -> None:
        """Insert or update a run row from a harness :class:`Run`."""
        self._conn.execute(
            """
            INSERT INTO runs (id, status, created_at, updated_at, request, result, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                updated_at=excluded.updated_at,
                result=excluded.result,
                error=excluded.error
            """,
            (
                run.id,
                str(run.status),
                run.created_at,
                run.updated_at,
                json.dumps(run.request),
                json.dumps(run.result) if run.result is not None else None,
                run.error,
            ),
        )
        self._conn.commit()

    def record_event(self, run_id: str, kind: str, detail: str) -> LedgerEvent:
        ts = self._clock()
        self._conn.execute(
            "INSERT INTO run_events (run_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
            (run_id, ts, kind, detail),
        )
        if self._fts:
            self._conn.execute(
                "INSERT INTO run_events_fts (detail, run_id, kind) VALUES (?, ?, ?)",
                (detail, run_id, kind),
            )
        self._conn.commit()
        return LedgerEvent(run_id=run_id, ts=ts, kind=kind, detail=detail)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "request": json.loads(row["request"]),
            "result": json.loads(row["result"]) if row["result"] is not None else None,
            "error": row["error"],
        }

    def events(self, run_id: str) -> list[LedgerEvent]:
        rows = self._conn.execute(
            "SELECT run_id, ts, kind, detail FROM run_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [LedgerEvent(r["run_id"], r["ts"], r["kind"], r["detail"]) for r in rows]

    def search(self, query: str, *, limit: int = 20) -> list[LedgerEvent]:
        """Full-text search over event detail (FTS5, or LIKE fallback)."""
        if self._fts:
            rows = self._conn.execute(
                "SELECT run_id, kind, detail FROM run_events_fts "
                "WHERE run_events_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
            return [LedgerEvent(r["run_id"], 0.0, r["kind"], r["detail"]) for r in rows]
        rows = self._conn.execute(
            "SELECT run_id, ts, kind, detail FROM run_events WHERE detail LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [LedgerEvent(r["run_id"], r["ts"], r["kind"], r["detail"]) for r in rows]

    def close(self) -> None:
        self._conn.close()
