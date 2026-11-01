"""Unit tests for GET /v1/evals (FORGE-292, gap G-G6)."""

from __future__ import annotations

import json
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    from fastapi import FastAPI

    from api_gateway.evals.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _write_report(tmp_path, run_dir: str, filename: str, report: dict) -> None:
    d = tmp_path / run_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(json.dumps(report), encoding="utf-8")


async def test_no_reports_yet_returns_empty_lists(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALS_REPORTS_DIR", str(tmp_path))
    async with client:
        resp = await client.get("/v1/evals")
    assert resp.status_code == 200
    assert resp.json() == {"scenarios": [], "history": []}


async def test_reads_the_latest_report_per_suite(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALS_REPORTS_DIR", str(tmp_path))
    _write_report(
        tmp_path,
        "20260101-000000",
        "report.json",
        {
            "suite": "runs_v1",
            "summary": {"l0_bracket": {"runs": 1, "completed_rate": 0.5, "avg_completeness": 0.5}},
        },
    )
    _write_report(
        tmp_path,
        "20260102-000000",
        "report.json",
        {
            "suite": "runs_v1",
            "summary": {"l0_bracket": {"runs": 2, "completed_rate": 1.0, "avg_completeness": 1.0}},
        },
    )
    async with client:
        resp = await client.get("/v1/evals")
    body = resp.json()
    assert len(body["scenarios"]) == 1
    row = body["scenarios"][0]
    assert row["scenario_id"] == "l0_bracket"
    assert row["run"] == "20260102-000000"
    assert row["completed_rate"] == 1.0
    assert len(body["history"]) == 2
    assert [h["run"] for h in body["history"]] == ["20260101-000000", "20260102-000000"]


async def test_different_suites_are_tracked_independently(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALS_REPORTS_DIR", str(tmp_path))
    _write_report(
        tmp_path,
        "20260101-000000",
        "report.json",
        {"suite": "runs_v1", "summary": {"l0_bracket": {"runs": 1, "completed_rate": 1.0}}},
    )
    _write_report(
        tmp_path,
        "manual",
        "report_design_loop.json",
        {
            "suite": "design_loop_v1",
            "summary": {
                "design_loop_wall_thickness": {
                    "runs": 1,
                    "completed_rate": 1.0,
                    "avg_completeness": 1.0,
                }
            },
        },
    )
    async with client:
        resp = await client.get("/v1/evals")
    body = resp.json()
    scenario_ids = {row["scenario_id"] for row in body["scenarios"]}
    assert scenario_ids == {"l0_bracket", "design_loop_wall_thickness"}


async def test_malformed_report_is_skipped_not_fatal(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALS_REPORTS_DIR", str(tmp_path))
    d = tmp_path / "20260101-000000"
    d.mkdir(parents=True)
    (d / "report.json").write_text("{not valid json", encoding="utf-8")
    async with client:
        resp = await client.get("/v1/evals")
    assert resp.status_code == 200
    assert resp.json() == {"scenarios": [], "history": []}


async def test_report_missing_summary_key_is_skipped(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALS_REPORTS_DIR", str(tmp_path))
    _write_report(tmp_path, "20260101-000000", "report.json", {"gateway": "http://x"})
    async with client:
        resp = await client.get("/v1/evals")
    assert resp.json() == {"scenarios": [], "history": []}


def test_default_reports_dir_points_at_repo_evals_reports() -> None:
    from api_gateway.evals.routes import _DEFAULT_REPORTS_DIR

    assert _DEFAULT_REPORTS_DIR.replace(os.sep, "/").endswith("evals/reports")
