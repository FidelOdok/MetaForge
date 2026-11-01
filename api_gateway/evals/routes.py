"""Eval results API (FORGE-292, gap G-G6).

``GET /v1/evals`` answers the dashboard's own "Eval dashboard: pass rate per
scenario over releases" ask -- with one real correction: nothing in this
repo's eval pipeline (``evals/nightly.sh``, ``evals/trend.py``) has any
concept of a CI "release" to key history by. ``nightly.sh`` runs on a
timestamp (``evals/reports/<YYYYMMDD-HHMMSS>/``), not a release tag, so
"over releases" is aspirational Jira wording -- the honest, buildable
equivalent this route actually serves is "over nightly runs," the same
substitution FORGE-291 made for "tokens" against a loop that makes zero LLM
calls.

Reads whatever ``evals/reports/*/report*.json`` files exist on disk
directly -- no new persistence layer, since ``run_scenarios.py`` (and this
ticket's own ``design_loop_scenarios.py``) already write exactly this shape
and ``evals/trend.py history`` already knows how to walk it. The tiny
per-report ``completed_rate``/``avg_completeness`` averaging this module
does mirrors ``evals/trend.py``'s own ``headline()`` function; it is
reimplemented here rather than imported because ``evals/`` is a standalone,
stdlib-only tool directory that is deliberately NOT copied into the gateway
Docker image (see ``Dockerfile``) -- importing it would work in local dev
and fail in the deployed container. The reports directory itself IS
reachable in the deployed container via a read-only bind mount
(``docker-compose.yml``), since it's a host-local, gitignored artifact
``nightly.sh`` writes by running ON the box, not inside this container.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any

import structlog
from fastapi import APIRouter

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.evals.routes")

router = APIRouter(prefix="/v1/evals", tags=["evals"])

# api_gateway/evals/routes.py -> parents[2] is the repo root in local dev,
# and /app in the Docker image (see docker-compose.yml's bind mount, which
# targets the same relative depth: /app/evals/reports).
_DEFAULT_REPORTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "evals", "reports")
)


def _reports_dir() -> str:
    return os.environ.get("EVALS_REPORTS_DIR", _DEFAULT_REPORTS_DIR)


def _load_report(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("evals_report_unreadable", path=path, error=str(exc))
        return None


def _run_timestamp(report_path: str, reports_dir: str) -> str:
    """The report's own run identifier -- the timestamped directory name
    ``nightly.sh`` creates it under (``evals/reports/<ts>/report.json``),
    or the report's immediate parent directory name for any other layout."""
    rel = os.path.relpath(report_path, reports_dir)
    return rel.split(os.sep)[0]


def list_recent_runs(reports_dir: str) -> list[dict[str, Any]]:
    """Every ``report*.json`` under ``reports_dir``, oldest first, as
    ``{run, suite, scenarios}`` headline rows -- the "over nightly runs"
    history the dashboard's trend view reads."""
    paths = sorted(glob.glob(os.path.join(reports_dir, "**", "report*.json"), recursive=True))
    rows: list[dict[str, Any]] = []
    for path in paths:
        report = _load_report(path)
        if report is None or "summary" not in report:
            continue
        summary = report.get("summary") or {}
        rows_by_scenario = {sid: s for sid, s in summary.items() if isinstance(s, dict)}
        n = len(rows_by_scenario) or 1
        rows.append(
            {
                "run": _run_timestamp(path, reports_dir),
                "suite": report.get("suite", "runs_v1"),
                "scenarios": len(rows_by_scenario),
                "completed_rate": round(
                    sum(float(s.get("completed_rate", 0)) for s in rows_by_scenario.values()) / n,
                    3,
                ),
            }
        )
    return rows


def latest_scenario_rows(reports_dir: str) -> list[dict[str, Any]]:
    """The most recent report per suite, flattened to one row per scenario
    -- the dashboard's "pass rate per scenario" table."""
    paths = sorted(glob.glob(os.path.join(reports_dir, "**", "report*.json"), recursive=True))
    latest_by_suite: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in paths:
        report = _load_report(path)
        if report is None or "summary" not in report:
            continue
        suite = report.get("suite", "runs_v1")
        run = _run_timestamp(path, reports_dir)
        # Paths are already sorted oldest-first, so the last one wins.
        latest_by_suite[suite] = (run, report)

    rows: list[dict[str, Any]] = []
    for suite, (run, report) in sorted(latest_by_suite.items()):
        for scenario_id, stats in (report.get("summary") or {}).items():
            if not isinstance(stats, dict):
                continue
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "suite": suite,
                    "run": run,
                    "runs": stats.get("runs"),
                    "completed_rate": stats.get("completed_rate"),
                    "avg_completeness": stats.get("avg_completeness"),
                }
            )
    return rows


@router.get("")
async def get_evals() -> dict[str, Any]:
    with tracer.start_as_current_span("evals.get") as span:
        reports_dir = _reports_dir()
        span.set_attribute("evals.reports_dir", reports_dir)
        scenarios = latest_scenario_rows(reports_dir)
        history = list_recent_runs(reports_dir)
        span.set_attribute("evals.scenario_count", len(scenarios))
        return {"scenarios": scenarios, "history": history}
