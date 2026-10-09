"""Phases a connected client does itself (FORGE-581).

A run in **client mode** does not call a model for its phases. Each phase is
posted as a :class:`PhaseTask`: the brief a phase brain would have been
given (goal, objective, deliverables, slots, flow context, retry feedback).
The connected client (Claude Code, Codex) claims the task, does the work
through MCP tools, and submits a summary. The workflow's ``run_phase``
activity waits for that submission instead of running an agent loop, and the
gate that follows checks the twin exactly as it would after a server phase.

The task id is ``run:phase:attempt``. The activity can be retried, and a
retry must find the task the first attempt posted (and any work already
submitted to it) rather than post a second one.

The store is SQLite, owned by the gateway, in the same shape as the run
ledger: a phase can wait for hours and must survive a gateway restart.
Stdlib only, so the workflow side can import the types.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "INTELLIGENCE_ENV",
    "ClientTaskStore",
    "Intelligence",
    "PhaseTask",
    "SqliteClientTaskStore",
    "TaskNotFoundError",
    "TaskStateError",
    "TaskStatus",
    "default_client_tasks_path",
    "default_intelligence",
    "parse_intelligence",
    "task_id_for",
]

#: Deployment default for runs that do not name a mode.
INTELLIGENCE_ENV = "METAFORGE_INTELLIGENCE"


class Intelligence(StrEnum):
    """Who does a run's phase work."""

    #: The gateway's own model provider (a phase brain). The default.
    SERVER = "server"
    #: The connected MCP client, through phase tasks.
    CLIENT = "client"


def parse_intelligence(value: Any) -> Intelligence:
    """``value`` as a mode; raises ``ValueError`` naming the allowed values."""
    raw = str(value or "").strip().lower()
    try:
        return Intelligence(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in Intelligence)
        raise ValueError(f"intelligence must be one of {allowed}; got '{value}'") from None


def default_intelligence() -> Intelligence:
    """The deployment default (``METAFORGE_INTELLIGENCE``), else server.

    A typo here is an error, not a quiet fall back to server: a deployment
    that meant to run phases on its clients and silently bills a provider
    instead is the failure this setting exists to prevent.
    """
    raw = os.environ.get(INTELLIGENCE_ENV, "").strip()
    return parse_intelligence(raw) if raw else Intelligence.SERVER


class TaskStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


class TaskNotFoundError(KeyError):
    """No task with that id."""


class TaskStateError(ValueError):
    """The task is not in a state that allows this (e.g. submitting twice)."""


def task_id_for(run_id: str, phase_id: str, attempt: int) -> str:
    return f"{run_id}:{phase_id}:{attempt}"


def default_client_tasks_path() -> Path:
    """``METAFORGE_CLIENT_TASKS_PATH``, else ``~/.metaforge/client_tasks.db``."""
    override = os.environ.get("METAFORGE_CLIENT_TASKS_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "client_tasks.db"


@dataclass
class PhaseTask:
    """One phase waiting for, or done by, a connected client."""

    id: str
    run_id: str
    phase_id: str
    attempt: int = 1
    project_id: str | None = None
    status: str = TaskStatus.OPEN.value
    #: What the phase brain would have been told: goal, title, objective,
    #: deliverables, slots, disciplines, flow context, retry feedback, prior.
    brief: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    claimed_by: str | None = None
    submitted_by: str | None = None
    cancel_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClientTaskStore:
    """The operations every store offers. :class:`SqliteClientTaskStore` is the one."""

    def open_task(self, task: PhaseTask) -> PhaseTask:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, task_id: str) -> PhaseTask:  # pragma: no cover - interface
        raise NotImplementedError

    def list_tasks(  # pragma: no cover - interface
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> list[PhaseTask]:
        raise NotImplementedError

    def claim(self, task_id: str, by: str) -> PhaseTask:  # pragma: no cover - interface
        raise NotImplementedError

    def submit(  # pragma: no cover - interface
        self, task_id: str, by: str, summary: str, artifacts: list[str]
    ) -> PhaseTask:
        raise NotImplementedError

    def cancel(self, task_id: str, reason: str) -> PhaseTask:  # pragma: no cover - interface
        raise NotImplementedError


_COLUMNS = (
    "id",
    "run_id",
    "phase_id",
    "attempt",
    "project_id",
    "status",
    "brief",
    "summary",
    "artifacts",
    "claimed_by",
    "submitted_by",
    "cancel_reason",
    "created_at",
    "updated_at",
)


class SqliteClientTaskStore(ClientTaskStore):
    """Phase tasks in SQLite (``:memory:`` for tests)."""

    def __init__(self, path: str = ":memory:", *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Same reasoning as the run ledger: one connection, used from whatever
        # thread the ASGI server hands a request to; the lock serialises it.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS client_tasks (
                id            TEXT PRIMARY KEY,
                run_id        TEXT NOT NULL,
                phase_id      TEXT NOT NULL,
                attempt       INTEGER NOT NULL,
                project_id    TEXT,
                status        TEXT NOT NULL,
                brief         TEXT NOT NULL,
                summary       TEXT NOT NULL,
                artifacts     TEXT NOT NULL,
                claimed_by    TEXT,
                submitted_by  TEXT,
                cancel_reason TEXT NOT NULL,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_client_tasks_status ON client_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_client_tasks_run ON client_tasks(run_id);
            """
        )
        self._conn.commit()
        logger.info("client_task_store_opened", path=path)

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, task_id: str) -> PhaseTask:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM client_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(task_id)
        return _from_row(row)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> list[PhaseTask]:
        clauses: list[str] = []
        args: list[Any] = []
        for column, value in (("status", status), ("project_id", project_id), ("run_id", run_id)):
            if value:
                clauses.append(f"{column} = ?")
                args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM client_tasks{where} ORDER BY created_at",  # noqa: S608 - fixed columns
                args,
            ).fetchall()
        return [_from_row(r) for r in rows]

    # ── transitions ──────────────────────────────────────────────────────

    def open_task(self, task: PhaseTask) -> PhaseTask:
        """Post ``task``, or return the one already posted under its id.

        A retried activity posts the same id again. An open, claimed or
        submitted task is returned as it is (a submission the first attempt
        never read is not thrown away); a cancelled one is reopened, since
        the activity that cancelled it is gone and this one is waiting.
        """
        now = self._clock()
        try:
            existing = self.get(task.id)
        except TaskNotFoundError:
            existing = None
        if existing is not None and existing.status != TaskStatus.CANCELLED.value:
            return existing
        if existing is not None:
            return self._write(
                existing, status=TaskStatus.OPEN.value, cancel_reason="", claimed_by=None
            )
        task.status = TaskStatus.OPEN.value
        task.created_at = task.updated_at = now
        with self._lock:
            self._conn.execute(
                f"INSERT INTO client_tasks ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                _to_row(task),
            )
            self._conn.commit()
        logger.info("client_task_opened", task_id=task.id, run_id=task.run_id, phase=task.phase_id)
        return task

    def claim(self, task_id: str, by: str) -> PhaseTask:
        """Take the task. A claimed task can be claimed again (a client that restarted)."""
        task = self.get(task_id)
        if task.status not in (TaskStatus.OPEN.value, TaskStatus.CLAIMED.value):
            raise TaskStateError(f"task '{task_id}' is {task.status}; it cannot be claimed")
        return self._write(task, status=TaskStatus.CLAIMED.value, claimed_by=by or "unknown")

    def submit(self, task_id: str, by: str, summary: str, artifacts: list[str]) -> PhaseTask:
        task = self.get(task_id)
        if task.status not in (TaskStatus.OPEN.value, TaskStatus.CLAIMED.value):
            raise TaskStateError(f"task '{task_id}' is {task.status}; nothing was recorded")
        if not summary.strip():
            raise TaskStateError("a submission needs a summary of what the phase did")
        return self._write(
            task,
            status=TaskStatus.SUBMITTED.value,
            summary=summary.strip(),
            artifacts=[str(a) for a in artifacts],
            submitted_by=by or "unknown",
        )

    def cancel(self, task_id: str, reason: str) -> PhaseTask:
        """Withdraw an open or claimed task; a finished one is left as it is."""
        task = self.get(task_id)
        if task.status not in (TaskStatus.OPEN.value, TaskStatus.CLAIMED.value):
            return task
        return self._write(task, status=TaskStatus.CANCELLED.value, cancel_reason=reason)

    def _write(self, task: PhaseTask, **changes: Any) -> PhaseTask:
        for key, value in changes.items():
            setattr(task, key, value)
        task.updated_at = self._clock()
        with self._lock:
            self._conn.execute(
                f"UPDATE client_tasks SET {', '.join(f'{c} = ?' for c in _COLUMNS[1:])} "
                "WHERE id = ?",
                (*_to_row(task)[1:], task.id),
            )
            self._conn.commit()
        logger.info("client_task_updated", task_id=task.id, status=task.status, run_id=task.run_id)
        return task


def _to_row(task: PhaseTask) -> tuple[Any, ...]:
    data = task.as_dict()
    data["brief"] = json.dumps(task.brief, sort_keys=True)
    data["artifacts"] = json.dumps(list(task.artifacts))
    return tuple(data[c] for c in _COLUMNS)


def _from_row(row: sqlite3.Row) -> PhaseTask:
    data = {c: row[c] for c in _COLUMNS}
    data["brief"] = json.loads(data["brief"] or "{}")
    data["artifacts"] = json.loads(data["artifacts"] or "[]")
    return PhaseTask(**data)
