"""The durable operation event log (FORGE-785, RT-015/RT-016).

Two SQLite tables:

* ``operation_events`` is append-only. Each event gets the next
  ``project_seq`` of its project, allocated in the same transaction from
  ``operation_cursors``, so the sequence survives a restart and is never
  reused even after old events are pruned. ``project_seq`` is the SSE ``id:``
  a reconnecting client resumes from.
* ``operations`` holds the latest snapshot of each operation, its stages
  included, folded from its events as they are appended.

Append rules, all enforced in the transaction:

* an ``event_id`` already stored is a duplicate, answered with the stored
  event (an idempotent retry);
* a ``sequence`` at or below the operation's last one is refused (out of
  order, or a replay of something older);
* a non-terminal event after a terminal one is refused, and so is any
  terminal one bar ``stale`` after ``completed`` (FORGE-788: a late result
  of a cancelled or superseded operation never becomes its outcome).

The path is ``METAFORGE_OPERATIONS_PATH``, else ``~/.metaforge/operations.db``
(on the ``metaforge-home`` volume in compose). ``:memory:`` keeps nothing
across a restart. Old events are pruned after
``METAFORGE_OPERATIONS_RETENTION_DAYS`` (default 14); a cursor older than
what is kept gets a ``resync``.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog

from api_gateway.operations.schemas import (
    OperationEvent,
    OperationEventIn,
    OperationSnapshot,
    OperationState,
    StageSnapshot,
    is_terminal,
)

logger = structlog.get_logger(__name__)

OPERATIONS_PATH_ENV = "METAFORGE_OPERATIONS_PATH"
RETENTION_DAYS_ENV = "METAFORGE_OPERATIONS_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 14.0
#: Appends between two retention passes.
_PRUNE_EVERY = 1000

AppendStatus = Literal["stored", "duplicate", "refused"]


def default_operations_path(env: Mapping[str, str] | None = None) -> str:
    """``METAFORGE_OPERATIONS_PATH``, else ``~/.metaforge/operations.db``."""
    env = os.environ if env is None else env
    override = (env.get(OPERATIONS_PATH_ENV) or "").strip()
    if override:
        return override
    return str(Path.home() / ".metaforge" / "operations.db")


def retention_seconds(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(RETENTION_DAYS_ENV) or "").strip()
    try:
        days = float(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        days = DEFAULT_RETENTION_DAYS
    return max(days, 0.0) * 86400.0


@dataclass(frozen=True)
class AppendResult:
    """What an append did. ``event`` is set when stored or a duplicate."""

    status: AppendStatus
    event: OperationEvent | None = None
    reason: str | None = None


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS operation_cursors ("
    " project_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS operation_events ("
    " project_id TEXT NOT NULL, project_seq INTEGER NOT NULL,"
    " event_id TEXT NOT NULL UNIQUE, operation_id TEXT NOT NULL,"
    " sequence INTEGER NOT NULL, recorded_at REAL NOT NULL, body TEXT NOT NULL,"
    " PRIMARY KEY (project_id, project_seq))",
    "CREATE INDEX IF NOT EXISTS operation_events_op ON operation_events"
    " (project_id, operation_id, sequence)",
    "CREATE INDEX IF NOT EXISTS operation_events_age ON operation_events (recorded_at)",
    "CREATE TABLE IF NOT EXISTS operations ("
    " project_id TEXT NOT NULL, operation_id TEXT NOT NULL, kind TEXT NOT NULL,"
    " state TEXT NOT NULL, terminal INTEGER NOT NULL, last_sequence INTEGER NOT NULL,"
    " updated_at REAL NOT NULL, body TEXT NOT NULL,"
    " PRIMARY KEY (project_id, operation_id))",
    "CREATE INDEX IF NOT EXISTS operations_active ON operations (project_id, terminal, updated_at)",
)

#: Columns added after the first release, with their type (FORGE-788). A
#: database written before them gains them on open.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (("operations", "target_key", "TEXT"),)


class OperationStore:
    """The operation event log and snapshots in SQLite. Thread-safe."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        clock: Callable[[], float] = time.time,
        retention_s: float | None = None,
    ) -> None:
        self.path = str(path)
        self._clock = clock
        self._retention_s = retention_seconds() if retention_s is None else retention_s
        self._lock = threading.Lock()
        self._appends = 0
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA:
                self._db.execute(statement)
            for table, column, sql_type in _ADDED_COLUMNS:
                have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS operations_target ON operations"
                " (project_id, target_key, terminal)"
            )
        self.prune()

    # ── append ──────────────────────────────────────────────────────────

    def append(self, project_id: str, incoming: OperationEventIn) -> AppendResult:
        """Store one event and fold it into its operation's snapshot."""
        event_id = incoming.event_id or str(uuid.uuid4())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = self._append_locked(project_id, event_id, incoming)
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            if result.status == "stored":
                self._db.execute("COMMIT")
                self._appends += 1
            else:
                self._db.execute("ROLLBACK")
        if result.status == "stored" and self._appends % _PRUNE_EVERY == 0:
            self.prune()
        return result

    def _append_locked(
        self, project_id: str, event_id: str, incoming: OperationEventIn
    ) -> AppendResult:
        row = self._db.execute(
            "SELECT body FROM operation_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is not None:
            return AppendResult("duplicate", OperationEvent.model_validate_json(row[0]))
        op_row = self._db.execute(
            "SELECT state, last_sequence, body FROM operations"
            " WHERE project_id = ? AND operation_id = ?",
            (project_id, incoming.operation_id),
        ).fetchone()
        last_sequence = int(op_row[1]) if op_row else 0
        if incoming.sequence is not None and incoming.sequence <= last_sequence:
            return AppendResult(
                "refused",
                reason=f"sequence {incoming.sequence} is not after {last_sequence}",
            )
        if op_row and is_terminal(op_row[0]) and not _may_follow_terminal(op_row[0], incoming):
            return AppendResult(
                "refused",
                reason=f"operation is already {op_row[0]}; a '{incoming.type}' event cannot follow",
            )
        sequence = incoming.sequence if incoming.sequence is not None else last_sequence + 1
        cursor = self._db.execute(
            "SELECT last_seq FROM operation_cursors WHERE project_id = ?", (project_id,)
        ).fetchone()
        project_seq = (int(cursor[0]) if cursor else 0) + 1
        self._db.execute(
            "INSERT INTO operation_cursors (project_id, last_seq) VALUES (?, ?)"
            " ON CONFLICT(project_id) DO UPDATE SET last_seq = excluded.last_seq",
            (project_id, project_seq),
        )
        fields = incoming.model_dump(exclude={"event_id", "sequence", "project_id"})
        event = OperationEvent(
            **fields,
            event_id=event_id,
            project_id=project_id,
            project_seq=project_seq,
            sequence=sequence,
        )
        now = self._clock()
        self._db.execute(
            "INSERT INTO operation_events"
            " (project_id, project_seq, event_id, operation_id, sequence, recorded_at, body)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                project_seq,
                event_id,
                event.operation_id,
                sequence,
                now,
                event.model_dump_json(),
            ),
        )
        previous = OperationSnapshot.model_validate_json(op_row[2]) if op_row else None
        snapshot = fold(previous, event)
        self._db.execute(
            "INSERT INTO operations"
            " (project_id, operation_id, kind, state, terminal, last_sequence, updated_at,"
            " body, target_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id, operation_id) DO UPDATE SET"
            " kind = excluded.kind, state = excluded.state, terminal = excluded.terminal,"
            " last_sequence = excluded.last_sequence, updated_at = excluded.updated_at,"
            " body = excluded.body, target_key = excluded.target_key",
            (
                project_id,
                snapshot.operation_id,
                snapshot.kind.value,
                snapshot.state.value,
                int(is_terminal(snapshot.state)),
                snapshot.last_sequence,
                now,
                snapshot.model_dump_json(),
                snapshot.target_key,
            ),
        )
        return AppendResult("stored", event)

    # ── reads ───────────────────────────────────────────────────────────

    def cursor(self, project_id: str) -> int:
        """The project's latest ``project_seq`` (0 when it has none)."""
        with self._lock:
            row = self._db.execute(
                "SELECT last_seq FROM operation_cursors WHERE project_id = ?", (project_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def oldest_seq(self, project_id: str) -> int | None:
        """The oldest ``project_seq`` still kept, or None when none is."""
        with self._lock:
            row = self._db.execute(
                "SELECT MIN(project_seq) FROM operation_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def events_after(
        self, project_id: str, after_seq: int, *, limit: int | None = None
    ) -> list[OperationEvent]:
        """The replay query: events with ``project_seq > after_seq``, oldest first."""
        sql = (
            "SELECT body FROM operation_events WHERE project_id = ? AND project_seq > ?"
            " ORDER BY project_seq"
        )
        params: tuple[Any, ...] = (project_id, after_seq)
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, int(limit))
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [OperationEvent.model_validate_json(r[0]) for r in rows]

    def count_after(self, project_id: str, after_seq: int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM operation_events WHERE project_id = ? AND project_seq > ?",
                (project_id, after_seq),
            ).fetchone()
        return int(row[0]) if row else 0

    def get(self, project_id: str, operation_id: str) -> OperationSnapshot | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM operations WHERE project_id = ? AND operation_id = ?",
                (project_id, operation_id),
            ).fetchone()
        return OperationSnapshot.model_validate_json(row[0]) if row else None

    def find(self, operation_id: str) -> tuple[str, OperationSnapshot] | None:
        """An operation in whichever project holds it: ``(project_id, snapshot)``."""
        with self._lock:
            row = self._db.execute(
                "SELECT project_id, body FROM operations WHERE operation_id = ?"
                " ORDER BY updated_at DESC LIMIT 1",
                (operation_id,),
            ).fetchone()
        return (str(row[0]), OperationSnapshot.model_validate_json(row[1])) if row else None

    def list_operations(
        self, project_id: str, *, active: bool = False, limit: int = 50
    ) -> list[OperationSnapshot]:
        """A project's operations, most recently updated first."""
        sql = "SELECT body FROM operations WHERE project_id = ?"
        if active:
            sql += " AND terminal = 0"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (project_id, int(limit))).fetchall()
        return [OperationSnapshot.model_validate_json(r[0]) for r in rows]

    def active_for_target(
        self, project_id: str, target_key: str, *, exclude: str | None = None
    ) -> list[OperationSnapshot]:
        """Operations of a project still open on ``target_key``, oldest first (FORGE-788)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT operation_id, body FROM operations"
                " WHERE project_id = ? AND target_key = ? AND terminal = 0"
                " ORDER BY updated_at",
                (project_id, target_key),
            ).fetchall()
        return [
            OperationSnapshot.model_validate_json(body)
            for operation_id, body in rows
            if operation_id != exclude
        ]

    def count_active(self) -> dict[str, int]:
        """Operations not yet terminal, by kind, across every project."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, COUNT(*) FROM operations WHERE terminal = 0 GROUP BY kind"
            ).fetchall()
        return {str(k): int(n) for k, n in rows}

    # ── retention ───────────────────────────────────────────────────────

    def prune(self, *, now: float | None = None) -> int:
        """Drop events older than the retention window. Cursors are kept.

        Snapshots of terminal operations last updated before the window go
        too; an operation still active keeps its snapshot however old.
        """
        if self._retention_s <= 0:
            return 0
        cutoff = (self._clock() if now is None else now) - self._retention_s
        with self._lock:
            removed = self._db.execute(
                "DELETE FROM operation_events WHERE recorded_at < ?", (cutoff,)
            ).rowcount
            self._db.execute(
                "DELETE FROM operations WHERE terminal = 1 AND updated_at < ?", (cutoff,)
            )
        if removed:
            logger.info("operation_events_pruned", removed=removed, path=self.path)
        return int(removed or 0)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _may_follow_terminal(current: str, incoming: OperationEventIn) -> bool:
    """Whether ``incoming`` may follow an operation that already ended.

    Only one change is real after an ending: a completed result going stale
    because its target moved on. Anything else (a late result of a job that
    was cancelled or superseded, say) is refused, so it can never be reported
    as the current outcome (FORGE-788, DEF-008).
    """
    return (
        OperationState(current) is OperationState.COMPLETED
        and incoming.state is OperationState.STALE
    )


# ── folding events into a snapshot ──────────────────────────────────────


def fold(previous: OperationSnapshot | None, event: OperationEvent) -> OperationSnapshot:
    """The operation's snapshot after ``event``.

    The latest event sets state, message, severity, progress and the actions
    on offer. Artifacts accumulate (newest first, one per kind and id).
    Stages are upserted by ``stage_id``. When the operation ends, any stage
    still in flight ends with it: a stage cannot outlive its operation.
    """
    assert event.occurred_at is not None and event.kind is not None
    at: datetime = event.occurred_at
    # The clock starts when the operation leaves the queue, whatever state it
    # leaves in: an operation that opens straight at a decision
    # (``waiting_for_user``) has been going since then too. Starting it only
    # on ``running`` left such an operation with no start time at all.
    started_at = previous.started_at if previous else None
    if started_at is None and event.state is not OperationState.QUEUED:
        started_at = at
    artifacts = list(event.artifact_refs)
    seen = {(a.kind, a.id) for a in artifacts}
    for ref in previous.artifact_refs if previous else []:
        if (ref.kind, ref.id) not in seen:
            artifacts.append(ref)
            seen.add((ref.kind, ref.id))
    stages: dict[str, StageSnapshot] = {
        s.stage_id: s for s in (previous.stages if previous else [])
    }
    if event.stage_id:
        prior = stages.get(event.stage_id)
        stages[event.stage_id] = StageSnapshot(
            stage_id=event.stage_id,
            stage_title=event.stage_title or (prior.stage_title if prior else None),
            domain=event.domain,
            state=event.state,
            updated_at=at,
        )
    if is_terminal(event.state):
        for stage_id, stage in list(stages.items()):
            if not is_terminal(stage.state):
                stages[stage_id] = stage.model_copy(update={"state": event.state, "updated_at": at})
    return OperationSnapshot(
        operation_id=event.operation_id,
        kind=event.kind,
        # A stage's domain is its lane, not the operation's: an operation keeps
        # the domain its own (stage-less) events gave it.
        domain=previous.domain
        if previous is not None and (event.stage_id or event.domain.value == "general")
        else event.domain,
        title=event.title or (previous.title if previous else ""),
        state=event.state,
        run_id=event.run_id or (previous.run_id if previous else None),
        started_at=started_at,
        updated_at=at,
        last_sequence=event.sequence,
        base_revision=event.base_revision or (previous.base_revision if previous else None),
        target_revision=event.target_revision or (previous.target_revision if previous else None),
        target_key=event.target_key or (previous.target_key if previous else None),
        retry_of=event.retry_of or (previous.retry_of if previous else None),
        message=event.message,
        severity=event.severity,
        artifact_refs=artifacts,
        requires_action=event.requires_action,
        action_refs=list(event.action_refs),
        progress=event.progress,
        stages=list(stages.values()),
    )
