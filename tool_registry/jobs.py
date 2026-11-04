"""Slow tool calls as jobs that are polled (FORGE-783).

An MCP client gives up on a tool call after a fixed time (Claude Code after
120 s), and a paid 3D generation takes about 150. The call then looks failed
to the agent although the server finished and stored the result. A slow tool
therefore starts its work as a job and returns at once with
``{"job_id", "status": "running", "poll": "twin.job_status",
"expected_seconds"}``; the work runs as an asyncio task in this process and
its result (or error) is stored on the job.

Pieces, reusable by any slow tool:

* :class:`JobStore`: the persisted job records, in SQLite. Opening a store
  marks every job still ``queued`` or ``running`` as ``interrupted``: a job's
  task lives in one process, so a record that outlived its process will never
  finish. The store is therefore **one per process**: the gateway keeps
  ``~/.metaforge/tool_jobs.db`` (or ``METAFORGE_TOOL_JOBS_PATH``) and the MCP
  sidecar its own file, and a job is read back through the process that runs
  it. ``METAFORGE_TOOL_JOBS_PATH=:memory:`` keeps nothing across a restart.
* :class:`JobRunner`: starts a job, or returns the one already covering an
  identical call (same tool, arguments hash, session and project) that is
  still running or succeeded within :data:`REUSE_WINDOW_S`, so a retry never
  pays for a second generation. It also hands each session the jobs of its
  own that finished since it was last told (:meth:`JobRunner.take_notices`).
* :func:`caller_waits`: the default for a call that does not say. Service
  callers (the design-flow worker, an in-process caller with no MCP context)
  wait for the result as before; a client gets a job.

Each state change is a ``tool_job_state`` log event and one count of
``metaforge_tool_job_total{tool, status}``.

FORGE-785: each state change of a job that belongs to a project is also an
operation event (``operation_id = "job:<job_id>"``) handed to an injected
publisher. The default publisher is
:func:`mcp_core.operation_events.publish_operation_event`: inside the gateway
it writes to the durable operation log, in the MCP sidecar it posts to the
gateway. A job can be cancelled (:meth:`JobRunner.cancel`) or superseded by a
newer one (:meth:`JobRunner.supersede`); both end it at once and stop its task.

FORGE-788 (scheduling):

* A job may name a ``target_key`` (what it works on, an item key for
  example) and the ``base_revision`` of that target it starts from. Starting
  a job on a target supersedes this runner's older jobs still open on the
  same target in the same project.
* **Stale results.** With a head resolver installed
  (:func:`set_job_head_resolver`), a job whose target's head is no longer its
  ``base_revision`` ends ``stale``: before its work starts (nothing is paid
  for), or when its result arrives (the result is kept on the record, never
  adopted). Work that raises the twin's own revision conflict (a pinned
  ``KEY@n`` write refused because the head moved) ends ``stale`` too, so the
  head is protected by the twin itself and reported here.
* **Coalescing.** An identical call (same tool, arguments hash, project and
  target) from any session within :data:`COALESCE_WINDOW_S` returns the job
  already covering it, on top of the per-session reuse above.
* **Retry.** A tool that registers a retry factory (:func:`register_retry`)
  can have a failed or interrupted job run again with the same arguments, as
  a new job that names the old one in ``retry_of``.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

JOBS_PATH_ENV = "METAFORGE_TOOL_JOBS_PATH"
#: The read tool a job reply points the caller at.
POLL_TOOL = "twin.job_status"
#: Seconds after success during which an identical call returns the same job.
REUSE_WINDOW_S = 600.0
#: Seconds during which an identical call from any session joins the job
#: already covering it (FORGE-788). ``METAFORGE_JOB_COALESCE_WINDOW_S``.
COALESCE_WINDOW_S = 30.0
COALESCE_WINDOW_ENV = "METAFORGE_JOB_COALESCE_WINDOW_S"
STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "cancelled",
    "superseded",
    "stale",
)
ACTIVE = frozenset({"queued", "running"})
TERMINAL = frozenset({"succeeded", "failed", "interrupted", "cancelled", "superseded", "stale"})
#: The endings a retry can follow.
RETRYABLE = frozenset({"failed", "interrupted"})
INTERRUPTED_ERROR = "the server restarted before the job finished; call the tool again"
CANCELLED_ERROR = "the job was cancelled before it finished"
SUPERSEDED_ERROR = "a newer request replaced this job before it finished"
#: The exception names a twin write raises when its pinned base moved.
_REVISION_CONFLICTS = frozenset({"ItemRevisionConflictError", "ChangeSetConflictError"})

#: Takes ``(project_id, event)`` and delivers an operation event (FORGE-785).
JobEventPublisher = Callable[[str, dict[str, Any]], Awaitable[Any]]
#: ``(project_id, target_key) -> the target's head revision`` or ``None``.
JobHeadResolver = Callable[[str, str], Awaitable[str | None]]
#: Builds the work of a retried job from its stored arguments.
RetryFactory = Callable[[dict[str, Any]], Callable[[], Awaitable[dict[str, Any]]]]

_head_resolver: JobHeadResolver | None = None
_retry_factories: dict[str, RetryFactory] = {}
#: The job whose work is running in this task (CDE slice 6), so a tool's work
#: can report milestones on its own operation without being told its id.
_current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "metaforge_current_job", default=None
)


def current_job_id() -> str | None:
    """The id of the job whose work is running in this task, if any."""
    return _current_job.get()


class JobNotRetryableError(ValueError):
    """A job cannot be run again (unknown, not failed, or its tool cannot)."""


def set_job_head_resolver(resolver: JobHeadResolver | None) -> None:
    """Install (or remove) what reads a target's head for stale checks."""
    global _head_resolver  # noqa: PLW0603
    _head_resolver = resolver


def register_retry(tool_id: str, factory: RetryFactory) -> None:
    """Let failed jobs of ``tool_id`` be retried with ``factory(arguments)``."""
    _retry_factories[tool_id] = factory


def retryable_tool(tool_id: str) -> bool:
    return tool_id in _retry_factories


def stale_error(target_key: str | None, base: str | None, head: str | None) -> str:
    """Why a job ended stale (stored on the job and said on its event)."""
    target = target_key or "the target"
    if head is None:
        return (
            f"{target} changed while this ran from revision {base}; the newer revision was "
            "kept and this result was not applied"
        )
    return (
        f"{target} moved on to revision {head} while this ran from revision {base}; the "
        "newer revision was kept and this result was not applied"
    )


def coalesce_window_s(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(COALESCE_WINDOW_ENV) or "").strip()
    try:
        return max(float(raw), 0.0) if raw else COALESCE_WINDOW_S
    except ValueError:
        return COALESCE_WINDOW_S


#: Each step of a job's life has a fixed operation ``sequence``, so its events
#: stay ordered however they travel (an HTTP post from the sidecar can arrive
#: late) and a restart that ends a job continues where it left off. The gaps
#: leave room for milestones in between.
_JOB_EVENTS: dict[str, tuple[int, str, str, str, str]] = {
    # status: (sequence, type, state, severity, message)
    "queued": (10, "queued", "queued", "info", "Queued"),
    "running": (20, "started", "running", "info", "Working"),
    "succeeded": (90, "completed", "completed", "info", "Finished"),
    "failed": (90, "failed", "failed", "error", "The job failed"),
    "interrupted": (
        90,
        "failed",
        "failed",
        "error",
        "The job stopped when its server restarted; ask for it again",
    ),
    "cancelled": (90, "cancelled", "cancelled", "info", "Cancelled"),
    "superseded": (90, "superseded", "superseded", "info", "Replaced by a newer request"),
    "stale": (
        90,
        "stale",
        "stale",
        "warning",
        "The design moved on while this ran; the newer revision was kept",
    ),
}
_MAX_ERROR_CHARS = 2000
_SENTINEL_SESSION = "00000000-0000-0000-0000-000000000000"

_metrics: Any = None


def _count(tool_id: str, status: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_tool_job(tool_id, status)
    except Exception:  # noqa: BLE001 - a metric never fails a job
        pass


def _count_scheduling(action: str, outcome: str = "ok") -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_operation_scheduling("job", action, outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a job
        pass


def default_jobs_path(env: Mapping[str, str] | None = None) -> str:
    """``METAFORGE_TOOL_JOBS_PATH``, else ``~/.metaforge/tool_jobs.db``."""
    env = os.environ if env is None else env
    override = (env.get(JOBS_PATH_ENV) or "").strip()
    if override:
        return override
    return str(Path.home() / ".metaforge" / "tool_jobs.db")


def arguments_hash(arguments: Mapping[str, Any]) -> str:
    """A stable hash of a call's arguments (key order does not matter)."""
    raw = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat()


@dataclass
class JobRecord:
    """One job, as stored."""

    job_id: str
    tool_id: str
    arguments_hash: str
    session_id: str
    project_id: str | None
    status: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    expected_seconds: float | None = None
    notified: bool = False
    #: FORGE-788: the call's arguments, kept so a failed job can be retried.
    arguments: dict[str, Any] | None = None
    target_key: str | None = None
    base_revision: str | None = None
    retry_of: str | None = None
    #: CDE slice 6: a fixed phrase the work's own exception offers for the
    #: failed event (its ``operation_message``). Not stored.
    event_message: str | None = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "job_id": self.job_id,
            "tool_id": self.tool_id,
            "status": self.status,
            "arguments_hash": self.arguments_hash,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "expected_seconds": self.expected_seconds,
            "result": self.result,
            "error": self.error,
            "target_key": self.target_key,
            "base_revision": self.base_revision,
            "retry_of": self.retry_of,
        }
        if self.started_at is not None:
            end = self.finished_at if self.finished_at is not None else time.time()
            out["elapsed_seconds"] = round(max(0.0, end - self.started_at), 1)
        return out


_COLUMNS = (
    "job_id, tool_id, arguments_hash, session_id, project_id, status, created_at, "
    "started_at, finished_at, result, error, expected_seconds, notified, "
    "arguments, target_key, base_revision, retry_of"
)
#: Columns added after the first release (FORGE-788); an older file gains them.
_ADDED_COLUMNS = ("arguments", "target_key", "base_revision", "retry_of")


class JobStore:
    """Job records in SQLite. Opening it interrupts what an earlier process left running."""

    def __init__(self, path: str | Path = ":memory:", *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        self._clock = clock
        self._lock = threading.Lock()
        #: The records of the jobs this store interrupted when it opened.
        self.interrupted_records: list[JobRecord] = []
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS tool_jobs ("
                " job_id TEXT PRIMARY KEY, tool_id TEXT NOT NULL,"
                " arguments_hash TEXT NOT NULL, session_id TEXT NOT NULL,"
                " project_id TEXT, status TEXT NOT NULL, created_at REAL NOT NULL,"
                " started_at REAL, finished_at REAL, result TEXT, error TEXT,"
                " expected_seconds REAL, notified INTEGER NOT NULL DEFAULT 0)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS tool_jobs_call ON tool_jobs"
                " (tool_id, arguments_hash, session_id, project_id)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS tool_jobs_session ON tool_jobs (session_id, notified)"
            )
            have = {r[1] for r in self._db.execute("PRAGMA table_info(tool_jobs)")}
            for column in _ADDED_COLUMNS:
                if column not in have:
                    self._db.execute(f"ALTER TABLE tool_jobs ADD COLUMN {column} TEXT")
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS tool_jobs_target ON tool_jobs"
                " (project_id, target_key, status)"
            )
            stale = self._db.execute(
                "SELECT job_id, tool_id FROM tool_jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            stale_rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs"  # noqa: S608
                " WHERE status IN ('queued', 'running')"
            ).fetchall()
            if stale:
                self._db.execute(
                    "UPDATE tool_jobs SET status = 'interrupted', finished_at = ?, error = ?"
                    " WHERE status IN ('queued', 'running')",
                    (self._clock(), INTERRUPTED_ERROR),
                )
        #: Jobs this store marked interrupted when it opened.
        self.interrupted_on_open = [str(job_id) for job_id, _tool in stale]
        self.interrupted_records = [self._row(r) for r in stale_rows]
        for job_id, tool_id in stale:
            logger.warning(
                "tool_job_state",
                job_id=job_id,
                tool=tool_id,
                status="interrupted",
                reason="restart",
            )
            _count(str(tool_id), "interrupted")

    @staticmethod
    def _row(row: tuple[Any, ...]) -> JobRecord:
        result = json.loads(row[9]) if row[9] else None
        arguments = json.loads(row[13]) if row[13] else None
        return JobRecord(
            job_id=row[0],
            tool_id=row[1],
            arguments_hash=row[2],
            session_id=row[3],
            project_id=row[4],
            status=row[5],
            created_at=row[6],
            started_at=row[7],
            finished_at=row[8],
            result=result if isinstance(result, dict) else None,
            error=row[10],
            expected_seconds=row[11],
            notified=bool(row[12]),
            arguments=arguments if isinstance(arguments, dict) else None,
            target_key=row[14],
            base_revision=row[15],
            retry_of=row[16],
        )

    def insert(self, job: JobRecord) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO tool_jobs ({_COLUMNS})"  # noqa: S608
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    job.tool_id,
                    job.arguments_hash,
                    job.session_id,
                    job.project_id,
                    job.status,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    json.dumps(job.result, default=str) if job.result is not None else None,
                    job.error,
                    job.expected_seconds,
                    int(job.notified),
                    json.dumps(job.arguments, default=str) if job.arguments is not None else None,
                    job.target_key,
                    job.base_revision,
                    job.retry_of,
                ),
            )

    def update(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "result", "error", "notified"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], default=str)
        if "notified" in fields:
            fields["notified"] = int(bool(fields["notified"]))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE tool_jobs SET {assignments} WHERE job_id = ?",  # noqa: S608
                (*fields.values(), job_id),
            )

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs WHERE job_id = ?",  # noqa: S608
                (job_id,),
            ).fetchone()
        return self._row(row) if row else None

    def find_reusable(
        self,
        tool_id: str,
        args_hash: str,
        session_id: str,
        project_id: str | None,
        *,
        since: float,
        target_key: str | None = None,
        base_revision: str | None = None,
    ) -> JobRecord | None:
        """A job covering the same call: still running, or succeeded at or after ``since``.

        The same call means the same target and base revision too (FORGE-788).
        """
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs"  # noqa: S608
                " WHERE tool_id = ? AND arguments_hash = ? AND session_id = ?"
                " AND project_id IS ? AND target_key IS ? AND base_revision IS ?"
                " AND (status IN ('queued', 'running')"
                "      OR (status = 'succeeded' AND finished_at >= ?))"
                " ORDER BY created_at DESC LIMIT 1",
                (tool_id, args_hash, session_id, project_id, target_key, base_revision, since),
            ).fetchone()
        return self._row(row) if row else None

    def find_coalescible(
        self,
        tool_id: str,
        args_hash: str,
        project_id: str,
        target_key: str | None,
        *,
        since: float,
        base_revision: str | None = None,
    ) -> JobRecord | None:
        """A job of any session covering the same call (same project, target and
        base), open or created at or after ``since``, and not failed (FORGE-788)."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs"  # noqa: S608
                " WHERE tool_id = ? AND arguments_hash = ? AND project_id = ?"
                " AND target_key IS ? AND base_revision IS ? AND created_at >= ?"
                " AND status IN ('queued', 'running', 'succeeded')"
                " ORDER BY created_at DESC LIMIT 1",
                (tool_id, args_hash, project_id, target_key, base_revision, since),
            ).fetchone()
        return self._row(row) if row else None

    def active_for_target(self, project_id: str, target_key: str) -> list[JobRecord]:
        """Jobs still open on ``target_key`` in ``project_id``, oldest first."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs"  # noqa: S608
                " WHERE project_id = ? AND target_key = ? AND status IN ('queued', 'running')"
                " ORDER BY created_at",
                (project_id, target_key),
            ).fetchall()
        return [self._row(r) for r in rows]

    def take_unnotified(self, session_id: str) -> list[JobRecord]:
        """This session's finished jobs it has not been told of, now marked told."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM tool_jobs"  # noqa: S608
                " WHERE session_id = ? AND notified = 0"
                " AND status IN"
                " ('succeeded', 'failed', 'interrupted', 'cancelled', 'superseded', 'stale')"
                " ORDER BY finished_at",
                (session_id,),
            ).fetchall()
            if rows:
                self._db.executemany(
                    "UPDATE tool_jobs SET notified = 1 WHERE job_id = ?",
                    [(r[0],) for r in rows],
                )
        return [self._row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


class JobRunner:
    """Runs slow tool work as background tasks recorded in a :class:`JobStore`."""

    def __init__(
        self,
        store: JobStore | None = None,
        *,
        reuse_window_s: float = REUSE_WINDOW_S,
        clock: Callable[[], float] = time.time,
        publisher: JobEventPublisher | None = None,
    ) -> None:
        self.store = store if store is not None else JobStore(":memory:", clock=clock)
        self._reuse_window_s = reuse_window_s
        self._clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: Jobs ended by cancel or supersede; their task must not record again.
        self._ended: set[str] = set()
        self._publisher = publisher
        self._pending_events: set[asyncio.Task[Any]] = set()
        # A job the store interrupted on opening ended for real: say so.
        for record in self.store.interrupted_records:
            self._emit(record, "interrupted")

    def _emit(self, job: JobRecord, status: str) -> None:
        """Hand the job's operation event to the publisher. Never raises."""
        if self._publisher is None or not job.project_id or status not in _JOB_EVENTS:
            return
        sequence, event_type, state, severity, message = _JOB_EVENTS[status]
        event: dict[str, Any] = {
            # Deterministic, so a retried post is a duplicate, not a second event.
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"metaforge:job:{job.job_id}:{status}")),
            "operation_id": f"job:{job.job_id}",
            "kind": "job",
            "sequence": sequence,
            "type": event_type,
            "state": state,
            "title": _job_title(job.tool_id),
            "domain": _job_domain(job.tool_id),
            "occurred_at": _iso(self._clock()),
            "severity": severity,
            "message": message,
        }
        if job.target_key:
            event["target_key"] = job.target_key[:200]
        if job.base_revision:
            event["base_revision"] = job.base_revision[:200]
        if job.retry_of:
            event["retry_of"] = f"job:{job.retry_of}"
        if status == "stale" and job.error:
            event["message"] = job.error[:500]
        if status == "failed" and job.event_message:
            event["message"] = job.event_message[:500]
        hint = job.result.get("operation_event") if status == "succeeded" and job.result else None
        if isinstance(hint, dict) and isinstance(hint.get("message"), str):
            # CDE slice 6: a tool's result may name its own fixed completion
            # phrase (for example "Geometry validated").
            event["message"] = hint["message"][:500]
        if status in ACTIVE:
            event["action_refs"] = [
                {"kind": "cancel", "id": f"job:{job.job_id}", "label": "Cancel"}
            ]
        elif status in RETRYABLE and job.arguments is not None and retryable_tool(job.tool_id):
            event["action_refs"] = [{"kind": "retry", "id": f"job:{job.job_id}", "label": "Retry"}]
        if status == "succeeded" and job.result:
            node_id = job.result.get("node_id")
            refs = hint.get("artifact_refs") if isinstance(hint, dict) else None
            if isinstance(refs, list) and refs:
                event["artifact_refs"] = [r for r in refs if isinstance(r, dict)][:10]
            elif node_id:
                event["artifact_refs"] = [
                    {"kind": "work_product", "id": str(node_id), "label": _job_title(job.tool_id)}
                ]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info(
                "tool_job_event_not_published", job_id=job.job_id, status=status, reason="no loop"
            )
            return
        publisher = self._publisher
        project_id = job.project_id

        async def deliver() -> None:
            try:
                await publisher(project_id, event)
            except Exception as exc:  # noqa: BLE001 - a status line never fails a job
                logger.info(
                    "tool_job_event_dropped", job_id=job.job_id, status=status, error=str(exc)
                )

        task = loop.create_task(deliver())
        self._pending_events.add(task)
        task.add_done_callback(self._pending_events.discard)

    async def flush_events(self) -> None:
        """Wait for the operation events handed on so far (tests, shutdown)."""
        while True:
            pending = [t for t in self._pending_events if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def _changed(self, job: JobRecord, status: str, **extra: Any) -> None:
        log = logger.warning if status in {"failed", "interrupted"} else logger.info
        log(
            "tool_job_state",
            job_id=job.job_id,
            tool=job.tool_id,
            status=status,
            session_id=job.session_id,
            project_id=job.project_id,
            **extra,
        )
        _count(job.tool_id, status)
        self._emit(job, status)

    async def start(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        work: Callable[[], Awaitable[dict[str, Any]]],
        *,
        session_id: str,
        project_id: str | None = None,
        expected_seconds: float | None = None,
        target_key: str | None = None,
        base_revision: str | None = None,
        retry_of: str | None = None,
    ) -> tuple[JobRecord, bool]:
        """Start ``work`` as a job; ``(job, reused)``.

        An identical call (same tool, arguments hash, session and project)
        that is still running, or that succeeded within the reuse window,
        returns that job instead and ``work`` is never called. So does one
        from any session in the same project and target within the coalesce
        window (FORGE-788).

        A new job on a ``target_key`` supersedes this runner's older jobs
        still open on the same target in the same project.
        """
        args_hash = arguments_hash(arguments)
        # No await from the lookup to the task's creation, so two identical
        # calls on this event loop cannot both start one.
        now = self._clock()
        existing = self.store.find_reusable(
            tool_id,
            args_hash,
            session_id,
            project_id,
            since=now - self._reuse_window_s,
            target_key=target_key or None,
            base_revision=base_revision or None,
        )
        if (existing is None or not (existing.done or existing.job_id in self._tasks)) and (
            project_id and retry_of is None
        ):
            window = coalesce_window_s()
            existing = (
                self.store.find_coalescible(
                    tool_id,
                    args_hash,
                    project_id,
                    target_key or None,
                    since=now - window,
                    base_revision=base_revision or None,
                )
                if window > 0
                else None
            )
        if existing is not None and (existing.done or existing.job_id in self._tasks):
            logger.info(
                "tool_job_reused",
                job_id=existing.job_id,
                tool=tool_id,
                status=existing.status,
                session_id=session_id,
                coalesced=existing.session_id != session_id,
            )
            _count_scheduling("coalesced")
            return existing, True
        job = JobRecord(
            job_id=str(uuid.uuid4()),
            tool_id=tool_id,
            arguments_hash=args_hash,
            session_id=session_id,
            project_id=project_id,
            status="queued",
            created_at=now,
            expected_seconds=expected_seconds,
            arguments=dict(arguments),
            target_key=target_key or None,
            base_revision=base_revision or None,
            retry_of=retry_of,
        )
        self.store.insert(job)
        if project_id and job.target_key:
            for older in self.store.active_for_target(project_id, job.target_key):
                if older.job_id != job.job_id:
                    self.supersede(older.job_id, by=job.job_id)
        self._changed(job, "queued")
        task = asyncio.get_running_loop().create_task(self._run(job, work))
        self._tasks[job.job_id] = task
        return job, False

    async def retry(self, job_id: str) -> JobRecord:
        """Run a failed or interrupted job again, as a new job naming it in ``retry_of``.

        Raises :class:`JobNotRetryableError` when the job is unknown, did not
        fail, or its tool registered no retry. The new job takes the same
        arguments, session, project, target and base revision.
        """
        job = self.store.get(job_id)
        if job is None:
            raise JobNotRetryableError(f"job '{job_id}' is not known here")
        if job.status not in RETRYABLE:
            raise JobNotRetryableError(
                f"job '{job_id}' is {job.status}; only a failed job can be retried"
            )
        factory = _retry_factories.get(job.tool_id)
        if factory is None or job.arguments is None:
            raise JobNotRetryableError(f"{job.tool_id} jobs cannot be retried from here")
        try:
            work = factory(dict(job.arguments))
        except ValueError as exc:
            raise JobNotRetryableError(str(exc)) from exc
        new, _reused = await self.start(
            job.tool_id,
            job.arguments,
            work,
            session_id=job.session_id,
            project_id=job.project_id,
            expected_seconds=job.expected_seconds,
            target_key=job.target_key,
            base_revision=job.base_revision,
            retry_of=job.job_id,
        )
        logger.info("tool_job_retried", job_id=new.job_id, retry_of=job.job_id, tool=job.tool_id)
        _count_scheduling("retry")
        return new

    async def _head_moved(self, job: JobRecord) -> tuple[bool, str | None]:
        """``(moved, head)``: whether the job's target left its base revision."""
        resolver = _head_resolver
        if resolver is None or not job.project_id or not job.target_key or not job.base_revision:
            return False, None
        try:
            head = await resolver(job.project_id, job.target_key)
        except Exception as exc:  # noqa: BLE001 - no answer is not a stale result
            logger.info("tool_job_head_unavailable", job_id=job.job_id, error=str(exc))
            return False, None
        return head is not None and head != job.base_revision, head

    def _end_stale(
        self, job: JobRecord, head: str | None, result: dict[str, Any] | None = None
    ) -> None:
        error = stale_error(job.target_key, job.base_revision, head)
        job.error = error
        fields: dict[str, Any] = {"status": "stale", "finished_at": self._clock(), "error": error}
        if result is not None:
            fields["result"] = result
            job.result = result
        self.store.update(job.job_id, **fields)
        self._changed(job, "stale", head=head, base_revision=job.base_revision)

    async def _run(self, job: JobRecord, work: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        if job.job_id in self._ended:
            return
        try:
            moved, head = await self._head_moved(job)
        except asyncio.CancelledError:
            self._tasks.pop(job.job_id, None)
            raise
        if job.job_id in self._ended:
            return  # cancelled or superseded while the head was read
        if moved:
            # Nothing is paid for: the target moved before the work began.
            self._tasks.pop(job.job_id, None)
            self._end_stale(job, head)
            return
        started = self._clock()
        self.store.update(job.job_id, status="running", started_at=started)
        self._changed(job, "running")
        token = _current_job.set(job.job_id)
        try:
            result = await work()
            stored = result if isinstance(result, dict) else {"value": result}
            moved, head = await self._head_moved(job)
            produced = stored.get("target_revision")
            if moved and produced is not None and str(produced) == head:
                # CDE slice 6: the job's own pinned write is the new head (an
                # exact build committing its revision); the twin already
                # refused it had the base moved first. Not stale.
                moved = False
        except asyncio.CancelledError:
            if job.job_id in self._ended:
                raise  # cancel() or supersede() already recorded how it ended
            self.store.update(
                job.job_id,
                status="interrupted",
                finished_at=self._clock(),
                error="the job was cancelled before it finished",
            )
            self._changed(job, "interrupted", reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - recorded on the job, never lost
            if {c.__name__ for c in type(exc).__mro__} & _REVISION_CONFLICTS:
                # The twin refused a write pinned to a base that moved: the
                # head was protected; this job's result is stale, not failed.
                self._end_stale(job, None)
                return
            error = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            self.store.update(job.job_id, status="failed", finished_at=self._clock(), error=error)
            job.error = error
            phrase = getattr(exc, "operation_message", None)
            job.event_message = phrase if isinstance(phrase, str) else None
            self._changed(job, "failed", error=error, duration_s=round(self._clock() - started, 1))
        else:
            if job.job_id in self._ended:
                return
            if moved:
                self._end_stale(job, head, stored)
                return
            self.store.update(
                job.job_id, status="succeeded", finished_at=self._clock(), result=stored
            )
            job.result = stored
            self._changed(job, "succeeded", duration_s=round(self._clock() - started, 1))
        finally:
            _current_job.reset(token)
            self._tasks.pop(job.job_id, None)

    def get(self, job_id: str) -> JobRecord | None:
        return self.store.get(job_id)

    def owns(self, job_id: str) -> bool:
        """Whether this runner is running ``job_id`` now (so it can stop it)."""
        return job_id in self._tasks

    def cancel(self, job_id: str, *, reason: str | None = None) -> JobRecord | None:
        """Stop an active job of this runner and record it ``cancelled``.

        Returns the job as it now stands, ``None`` when it is unknown. A job
        that already ended, or one another process runs, is returned
        unchanged: the caller reads ``status`` to tell.
        """
        return self._end(job_id, "cancelled", reason or CANCELLED_ERROR)

    def supersede(self, job_id: str, *, by: str | None = None) -> JobRecord | None:
        """Stop an active job a newer request replaces; record it ``superseded``."""
        error = f"{SUPERSEDED_ERROR} ({by})" if by else SUPERSEDED_ERROR
        return self._end(job_id, "superseded", error)

    def _end(self, job_id: str, status: str, error: str) -> JobRecord | None:
        job = self.store.get(job_id)
        if job is None or job.done or job_id not in self._tasks:
            return job
        self._ended.add(job_id)
        self.store.update(
            job_id, status=status, finished_at=self._clock(), error=error[:_MAX_ERROR_CHARS]
        )
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._changed(job, status, reason=status)
        return self.store.get(job_id)

    def mark_notified(self, job_id: str) -> None:
        self.store.update(job_id, notified=True)

    def take_notices(self, session_id: str) -> list[JobRecord]:
        """Jobs of ``session_id`` that finished since it was last told, marked told."""
        return self.store.take_unnotified(session_id)

    async def wait(self, job_id: str, timeout: float | None = None) -> JobRecord | None:
        """Wait for a job of this runner to finish (tests and service callers)."""
        task = self._tasks.get(job_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
        return self.get(job_id)

    @staticmethod
    def reply(job: JobRecord, reused: bool = False) -> dict[str, Any]:
        """What a tool returns for a job it started (or found)."""
        status = "running" if job.status in ACTIVE else job.status
        out: dict[str, Any] = {
            "job_id": job.job_id,
            "status": status,
            "poll": POLL_TOOL,
            "expected_seconds": job.expected_seconds,
            "tool_id": job.tool_id,
            "reused": reused,
        }
        if job.status == "succeeded" and job.result is not None:
            out["result"] = job.result
        if job.error:
            out["error"] = job.error
        return out


_default: JobRunner | None = None
_default_lock = threading.Lock()


async def _publish_via_process_seam(project_id: str, event: dict[str, Any]) -> None:
    """The default job publisher: this process's operation event seam (FORGE-785)."""
    from mcp_core.operation_events import publish_operation_event

    await publish_operation_event(project_id, event)


def _job_domain(tool_id: str) -> str:
    try:
        from mcp_core.operation_events import domain_for_tool

        return domain_for_tool(tool_id)
    except Exception:  # noqa: BLE001
        return "general"


def _job_title(tool_id: str) -> str:
    """A title from the tool id: ``twin.generate_concept_mesh`` is "Generate concept mesh"."""
    name = tool_id.rpartition(".")[2].replace("_", " ").strip()
    return name[:1].upper() + name[1:] if name else tool_id


def default_job_runner() -> JobRunner:
    """This process's runner, opened on first use (which interrupts stale jobs)."""
    global _default  # noqa: PLW0603
    with _default_lock:
        if _default is None:
            path = default_jobs_path()
            try:
                store = JobStore(path)
            except (OSError, sqlite3.Error) as exc:
                logger.warning("tool_job_store_unavailable", path=path, error=str(exc))
                store = JobStore(":memory:")
            _default = JobRunner(store, publisher=_publish_via_process_seam)
            logger.info("tool_job_store_opened", path=store.path)
        return _default


def active_job_runner() -> JobRunner | None:
    """This process's runner if one was opened, without opening one."""
    return _default


def set_default_job_runner(runner: JobRunner | None) -> None:
    """Replace (or clear) the process runner: for tests and for wiring."""
    global _default  # noqa: PLW0603
    with _default_lock:
        _default = runner


def caller_waits() -> bool:
    """Whether a call that does not say should wait for its result.

    A service caller waits: the design-flow worker (a verified service, or a
    call claiming a run) and an in-process caller with no MCP context at all.
    Anyone else is a client, whose MCP call would time out, and gets a job.
    """
    try:
        from mcp_core.context import current_context

        ctx = current_context()
    except Exception:  # noqa: BLE001 - no context: an in-process caller
        return True
    if ctx.service_verified or ctx.run_id:
        return True
    return str(ctx.session_id) == _SENTINEL_SESSION


def session_of_call() -> tuple[str, str | None]:
    """``(session_id, project_id)`` of the current MCP call context."""
    try:
        from mcp_core.context import current_context

        ctx = current_context()
    except Exception:  # noqa: BLE001
        return _SENTINEL_SESSION, None
    project = str(ctx.project_id) if ctx.project_id else None
    return str(ctx.session_id), project
