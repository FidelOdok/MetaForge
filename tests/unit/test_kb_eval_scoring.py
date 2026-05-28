"""Unit tests for the KB-eval scoring logic (MET-470 harness).

The runner lives in ``scripts/`` (not an importable package), so we load it
by path. Only the pure scoring functions are exercised here — the HTTP glue
is run manually against a live gateway.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "datasheets" / "run_kb_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_kb_eval", _RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve the module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kbe = _load_module()


def test_query_hit_matches_mpn_in_source_path():
    blobs = ["tests/fixtures/datasheets/nrf52840.txt nordic bluetooth", "other.txt resistor"]
    assert kbe.query_hit(["nRF52840"], blobs, top_k=10) == ["nRF52840"]


def test_query_hit_matches_in_content_case_insensitive():
    blobs = ["doc.txt The ESP32-WROOM-32E is a Wi-Fi module"]
    assert kbe.query_hit(["esp32-wroom-32e"], blobs, top_k=10) == ["esp32-wroom-32e"]


def test_query_hit_respects_top_k_cutoff():
    blobs = ["a.txt unrelated", "b.txt also unrelated", "tps62840.txt buck"]
    # The matching doc is at rank 3 (index 2); top_k=2 excludes it.
    assert kbe.query_hit(["TPS62840"], blobs, top_k=2) == []
    assert kbe.query_hit(["TPS62840"], blobs, top_k=3) == ["TPS62840"]


def test_query_hit_no_match_returns_empty():
    assert kbe.query_hit(["MCP2515"], ["x.txt nothing here"], top_k=10) == []


def test_query_hit_multiple_expected_any_counts():
    blobs = ["esp32-wroom-32.txt wifi soc"]
    matched = kbe.query_hit(["ESP32-WROOM-32", "ESP32-WROOM-32E"], blobs, top_k=10)
    assert "ESP32-WROOM-32" in matched


def test_summarize_pass_rate_and_meets():
    queries = [
        {"id": "q1", "tier": "easy", "query": "ble soc", "expected_mpns": ["nRF52840"]},
        {"id": "q2", "tier": "easy", "query": "can bus", "expected_mpns": ["MCP2515"]},
        {"id": "q3", "tier": "hard", "query": "obscure", "expected_mpns": ["RP2040"]},
    ]
    hits = {
        "q1": ["nrf52840.txt ble"],
        "q2": ["mcp2515.txt can controller"],
        "q3": ["unrelated.txt nothing"],
    }
    report = kbe.summarize(queries, hits, top_k=10)
    assert report.total == 3
    assert report.passed == 2
    assert report.pass_rate == pytest.approx(2 / 3)
    assert report.meets(0.6) is True
    assert report.meets(0.8) is False


def test_summarize_missing_hits_treated_as_fail():
    queries = [{"id": "q1", "query": "x", "expected_mpns": ["BME280"]}]
    report = kbe.summarize(queries, {}, top_k=10)  # no hits fetched for q1
    assert report.passed == 0
    assert report.results[0].passed is False


# ---------- MET-470 Task 1: precision@k / recall@k ----------


def test_precision_at_k_all_relevant():
    # Every top-k hit mentions an expected MPN → precision 1.0
    blobs = ["a.txt nRF52840", "b.txt nrf52840 datasheet", "c.txt NRF52840 pinout"]
    assert kbe.precision_at_k(["nRF52840"], blobs, top_k=3) == pytest.approx(1.0)


def test_precision_at_k_partial_relevance():
    # 1 of 4 top-k blobs is relevant → precision 0.25
    blobs = [
        "a.txt unrelated",
        "b.txt MCP2515 can controller",
        "c.txt unrelated",
        "d.txt unrelated",
    ]
    assert kbe.precision_at_k(["MCP2515"], blobs, top_k=4) == pytest.approx(0.25)


def test_precision_at_k_empty_expected_returns_zero():
    blobs = ["a.txt anything"]
    assert kbe.precision_at_k([], blobs, top_k=3) == 0.0


def test_precision_at_k_zero_k_returns_zero():
    assert kbe.precision_at_k(["nRF52840"], ["nrf52840.txt"], top_k=0) == 0.0


def test_precision_at_k_truncates_to_top_k():
    # The match is at rank 5 (index 4); precision@3 must not see it.
    blobs = [
        "a.txt junk",
        "b.txt junk",
        "c.txt junk",
        "d.txt junk",
        "e.txt RP2040 microcontroller",
    ]
    assert kbe.precision_at_k(["RP2040"], blobs, top_k=3) == 0.0
    assert kbe.precision_at_k(["RP2040"], blobs, top_k=5) == pytest.approx(0.2)


def test_recall_at_k_full_coverage():
    # Both expected MPNs appear in the top-k → recall 1.0
    blobs = ["a.txt nRF52840", "b.txt MCP2515"]
    assert kbe.recall_at_k(["nRF52840", "MCP2515"], blobs, top_k=2) == pytest.approx(1.0)


def test_recall_at_k_partial_coverage():
    # Only one of two expected MPNs is found → recall 0.5
    blobs = ["a.txt nRF52840 only"]
    assert kbe.recall_at_k(["nRF52840", "MCP2515"], blobs, top_k=5) == pytest.approx(0.5)


def test_recall_at_k_empty_expected_returns_zero():
    assert kbe.recall_at_k([], ["anything"], top_k=10) == 0.0


def test_summarize_attaches_per_query_precision_and_recall():
    queries = [
        {
            "id": "q1",
            "tier": "easy",
            "query": "ble soc",
            "expected_mpns": ["nRF52840", "nRF52832"],
        },
    ]
    # 2 of 4 top-k blobs are relevant (P@4=0.5); 1 of 2 expected found (R@4=0.5).
    hits = {
        "q1": [
            "a.txt nRF52840 ble soc",
            "b.txt unrelated buck regulator",
            "c.txt nrf52840 reference design",
            "d.txt unrelated wifi",
        ]
    }
    report = kbe.summarize(queries, hits, top_k=4)
    r = report.results[0]
    assert r.precision_at_k == pytest.approx(0.5)
    assert r.recall_at_k == pytest.approx(0.5)


def test_eval_report_aggregates_mean_precision_and_recall():
    queries = [
        {"id": "q1", "expected_mpns": ["nRF52840"]},
        {"id": "q2", "expected_mpns": ["MCP2515"]},
    ]
    # q1: 1/2 top-k relevant → P=0.5, R=1.0
    # q2: 0/2 top-k relevant → P=0.0, R=0.0
    hits = {
        "q1": ["doc.txt nRF52840 ble", "doc.txt unrelated buck"],
        "q2": ["doc.txt unrelated wifi", "doc.txt nothing useful"],
    }
    report = kbe.summarize(queries, hits, top_k=2)
    # Mean precision = (0.5 + 0.0) / 2 = 0.25
    assert report.mean_precision_at_k == pytest.approx(0.25)
    # Mean recall = (1.0 + 0.0) / 2 = 0.5
    assert report.mean_recall_at_k == pytest.approx(0.5)


def test_eval_report_mean_metrics_empty_report():
    empty = kbe.EvalReport()
    assert empty.mean_precision_at_k == 0.0
    assert empty.mean_recall_at_k == 0.0
