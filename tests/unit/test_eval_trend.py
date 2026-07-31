"""Unit tests for the eval report diff/trend tool (MET-574). Network-free."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from trend import compare, extract_rows, headline, suite_of  # noqa: E402


def _chat_report(
    failed: int = 0, xfail: int = 2, xpass: int = 0, judge: float | None = None
) -> dict:
    report: dict = {
        "suite": "chat_context_v1",
        "summary": {
            "chat_mem_long_window": {
                "native": {
                    "runs": 1,
                    "checks_total": 8,
                    "passed": 8 - failed - xfail - xpass,
                    "failed_unexpected": failed,
                    "xfail_confirmed": xfail,
                    "xpass_improved": xpass,
                }
            }
        },
    }
    if judge is not None:
        report["judge"] = {"avg_overall_score": judge}
    return report


def _runs_report(rate: float = 1.0, completeness: float = 1.0) -> dict:
    return {
        "summary": {
            "l0_bracket": {
                "runs": 2,
                "completed_rate": rate,
                "avg_completeness": completeness,
            }
        }
    }


# --- shape detection + flattening ----------------------------------------------
def test_suite_detection() -> None:
    assert suite_of(_chat_report()) == "chat"
    assert suite_of(_runs_report()) == "runs"


def test_chat_rows_are_keyed_by_scenario_and_variant() -> None:
    rows = extract_rows(_chat_report())
    assert list(rows) == ["chat_mem_long_window::native"]


def test_runs_rows_are_keyed_by_scenario() -> None:
    assert list(extract_rows(_runs_report())) == ["l0_bracket"]


def test_headline_aggregates_and_includes_judge() -> None:
    h = headline(_chat_report(failed=1, judge=0.8))
    assert h["failed_unexpected"] == 1
    assert h["judge_avg"] == 0.8
    h2 = headline(_runs_report(rate=0.5, completeness=0.75))
    assert h2["completed_rate"] == 0.5 and h2["avg_completeness"] == 0.75


# --- diff: chat suite ----------------------------------------------------------------
def test_new_unexpected_failure_is_a_regression() -> None:
    result = compare(_chat_report(failed=0), _chat_report(failed=2))
    assert any("failed_unexpected 0 → 2" in r for r in result["regressions"])
    assert not result["improvements"]


def test_xfail_flipping_to_xpass_is_an_improvement() -> None:
    result = compare(_chat_report(xfail=2, xpass=0), _chat_report(xfail=0, xpass=2))
    assert any("fix landed" in i for i in result["improvements"])
    assert not result["regressions"]


def test_xpass_reverting_to_xfail_is_a_regression() -> None:
    result = compare(_chat_report(xfail=0, xpass=2), _chat_report(xfail=2, xpass=0))
    assert any("reverted" in r for r in result["regressions"])


def test_fixed_failure_is_an_improvement() -> None:
    result = compare(_chat_report(failed=2), _chat_report(failed=0))
    assert any("failed_unexpected 2 → 0" in i for i in result["improvements"])


# --- diff: runs suite -----------------------------------------------------------------
def test_completeness_drop_is_a_regression() -> None:
    result = compare(_runs_report(completeness=1.0), _runs_report(completeness=0.5))
    assert any("avg_completeness 1.0 → 0.5" in r for r in result["regressions"])


def test_completed_rate_gain_is_an_improvement() -> None:
    result = compare(_runs_report(rate=0.5), _runs_report(rate=1.0))
    assert any("completed_rate 0.5 → 1.0" in i for i in result["improvements"])


# --- diff: judge scores + guards ---------------------------------------------------------
def test_judge_drop_within_epsilon_is_noise() -> None:
    result = compare(_chat_report(judge=0.80), _chat_report(judge=0.78))
    assert not result["regressions"]


def test_judge_drop_beyond_epsilon_is_a_regression() -> None:
    result = compare(_chat_report(judge=0.80), _chat_report(judge=0.60))
    assert any("judge" in r for r in result["regressions"])


def test_mismatched_suites_raise() -> None:
    with pytest.raises(ValueError):
        compare(_chat_report(), _runs_report())


def test_no_change_yields_empty_diff() -> None:
    result = compare(_chat_report(), _chat_report())
    assert result["improvements"] == [] and result["regressions"] == []
