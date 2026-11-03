"""Collect what earlier runs on a project learned, for the intent and concept briefs.

The pure half (``orchestrator.design_flow.prior_lessons``) turns run records
into lessons; this half reads them, best-effort, from where they live:

- the run store, which holds every run's decision log and gate reviews in the
  gateway. The Temporal worker is another process whose store is empty, so
  there the same records are read from the run ledger file the gateway writes
  (compose shares ``~/.metaforge`` between the two), opened read-only;
- the twin: the latest deciding answers' lessons (an earlier run already
  gathered them) and the rejected item revisions, each with its gate reason.

A source that cannot be read costs its lessons, never the phase.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.design_flow.prior_lessons import (
    PriorLesson,
    lessons_from_runs,
    merge_lessons,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.prior_lessons")

__all__ = ["collect_prior_lessons", "make_lessons_reader", "run_records"]

#: ``project_id -> lessons``: what the phase brain is given.
LessonsReader = Callable[[str | None], Awaitable[list[PriorLesson]]]


def _ledger_disabled() -> bool:
    flag = os.environ.get("METAFORGE_RUNS_LEDGER_DISABLE", "").strip().lower()
    return flag in ("1", "true", "on", "yes")


def _ledger_records(path: Path) -> list[dict[str, Any]]:
    """Run rows from the ledger file, read-only; ``[]`` when there is none."""
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, updated_at, request FROM runs").fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for run_id, updated_at, request in rows:
        try:
            out.append({"id": run_id, "updated_at": updated_at, "request": json.loads(request)})
        except (TypeError, ValueError):
            continue
    return out


def run_records(ledger_path: Path | None = None) -> list[dict[str, Any]]:
    """Every run record this process can see.

    The gateway's run store first. When it holds nothing (the Temporal worker),
    the ledger file the gateway persists runs to.
    """
    from api_gateway.runs.routes import get_run_store

    records = [
        {"id": r.id, "updated_at": r.updated_at, "request": r.request}
        for r in get_run_store().list()
    ]
    if records or _ledger_disabled():
        return records
    if ledger_path is None:
        from orchestrator.harness.ledger import default_ledger_path

        ledger_path = default_ledger_path()
    try:
        return _ledger_records(ledger_path)
    except sqlite3.Error as exc:
        logger.debug("prior_lessons_ledger_unreadable", path=str(ledger_path), error=str(exc))
        return []


async def collect_prior_lessons(
    project_id: str | None,
    *,
    exclude_run_id: str | None = None,
    records: list[dict[str, Any]] | None = None,
    evaluator: Any = None,
    twin: Any = None,
) -> list[PriorLesson]:
    """Earlier runs' gate rejections, reviewer findings and recorded lessons.

    Deduplicated and capped (``MAX_PRIOR_LESSONS``). ``records`` defaults to
    :func:`run_records`; ``evaluator`` (a ``ProjectGateEvaluator``) gives the
    latest deciding answers' lessons, ``twin`` the rejected item revisions.
    """
    if not project_id:
        return []
    with tracer.start_as_current_span("prior_lessons.collect") as span:
        span.set_attribute("project.id", str(project_id))
        from_runs: list[PriorLesson] = []
        try:
            from_runs = lessons_from_runs(
                run_records() if records is None else records,
                project_id=project_id,
                exclude_run_id=exclude_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - a lesson source never fails the phase
            span.record_exception(exc)
            logger.warning("prior_lessons_runs_failed", project_id=project_id, error=str(exc))
        recorded: list[PriorLesson] = []
        if evaluator is not None and hasattr(evaluator, "deciding_answers"):
            try:
                answers = await evaluator.deciding_answers(project_id)
                if answers is not None:
                    recorded = [
                        PriorLesson(lesson.text, lesson.source_run, "recorded")
                        for lesson in answers.lessons
                    ]
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.warning(
                    "prior_lessons_recorded_failed", project_id=project_id, error=str(exc)
                )
        revisions: list[PriorLesson] = []
        if twin is not None:
            try:
                from api_gateway.projects.baseline_brief import read_baseline

                baseline = await read_baseline(twin, project_id)
                if baseline is not None:
                    revisions = [PriorLesson(text, "", "recorded") for text in baseline.lessons]
            except Exception as exc:  # noqa: BLE001
                span.record_exception(exc)
                logger.warning(
                    "prior_lessons_revisions_failed", project_id=project_id, error=str(exc)
                )
        lessons = merge_lessons(from_runs, recorded, revisions)
        span.set_attribute("lessons.count", len(lessons))
        logger.info(
            "prior_lessons_collected",
            project_id=project_id,
            from_runs=len(from_runs),
            recorded=len(recorded),
            revisions=len(revisions),
            kept=len(lessons),
        )
        return lessons


def make_lessons_reader(run_id: str, evaluator: Any = None, twin: Any = None) -> LessonsReader:
    """The phase brain's reader: this project's lessons, leaving this run out."""

    async def read(project_id: str | None) -> list[PriorLesson]:
        return await collect_prior_lessons(
            project_id, exclude_run_id=run_id, evaluator=evaluator, twin=twin
        )

    return read
