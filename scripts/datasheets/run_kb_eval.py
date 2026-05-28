"""Knowledge-base search evaluation harness (MET-470).

Turnkey runner for the L1 search quality gate. Given a **running gateway**
with a **populated** knowledge base, it executes the queries in
``tests/fixtures/datasheets/kb_eval_queries.yaml`` against
``GET /v1/knowledge/search`` and scores hit@k: a query passes when at least
one of its ``expected_mpns`` appears in the top-k results (matched against
each hit's ``sourcePath`` / ``content``, case-insensitive). The run fails
(exit 1) when the pass rate is below the manifest's ``target_pass_rate``.

This is the evaluation MET-470 calls for; it stays decoupled from the
production code so it can be run ad-hoc once a corpus is ingested:

    # 1. bring up the gateway (Neo4j + Claude key wired), then:
    forge ingest .cache/datasheets/            # populate the KB (MET-468)
    python scripts/datasheets/run_kb_eval.py   # score search quality

The scoring functions (``query_hit`` / ``summarize``) are pure and unit
tested in ``tests/unit/test_kb_eval_scoring.py``; the HTTP/IO glue here is
exercised manually against a live gateway.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERIES = REPO_ROOT / "tests" / "fixtures" / "datasheets" / "kb_eval_queries.yaml"


@dataclass(frozen=True)
class QueryResult:
    """Outcome of scoring one evaluation query."""

    query_id: str
    tier: str
    query: str
    expected_mpns: tuple[str, ...]
    matched_mpns: tuple[str, ...]
    passed: bool
    precision_at_k: float = 0.0
    """Fraction of the top-k hits that contained any expected MPN.

    MET-470 Task 1 explicitly calls for ``precision@10`` alongside the
    hit@k pass/fail signal.
    """
    recall_at_k: float = 0.0
    """Fraction of the expected MPNs that surfaced anywhere in the top-k
    hits — the natural recall complement to ``precision_at_k``."""


@dataclass
class EvalReport:
    """Aggregate evaluation outcome."""

    results: list[QueryResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def mean_precision_at_k(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.precision_at_k for r in self.results) / len(self.results)

    @property
    def mean_recall_at_k(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.recall_at_k for r in self.results) / len(self.results)

    def meets(self, target: float) -> bool:
        return self.pass_rate >= target


def query_hit(expected_mpns: list[str], hit_blobs: list[str], top_k: int) -> list[str]:
    """Return the expected MPNs found within the top-k hit blobs.

    ``hit_blobs`` is one searchable string per hit (typically
    ``f"{source_path} {content}"``). Matching is a case-insensitive
    substring test of each MPN against the joined top-k blobs.
    """
    haystack = " ".join(hit_blobs[: max(0, top_k)]).lower()
    return [mpn for mpn in expected_mpns if mpn.lower() in haystack]


def precision_at_k(expected_mpns: list[str], hit_blobs: list[str], top_k: int) -> float:
    """Fraction of the top-k hits that contain at least one expected MPN.

    A hit is "relevant" when any expected MPN (case-insensitive) appears
    in its blob. Precision@k = |relevant top-k hits| / k. An empty
    expected or top-k collection returns 0.0.
    """
    k = max(0, top_k)
    if k == 0 or not expected_mpns:
        return 0.0
    needles = [m.lower() for m in expected_mpns]
    top = hit_blobs[:k]
    relevant = sum(1 for blob in top if any(n in blob.lower() for n in needles))
    return relevant / k


def recall_at_k(expected_mpns: list[str], hit_blobs: list[str], top_k: int) -> float:
    """Fraction of the expected MPNs that appear somewhere in the top-k hits.

    Complement to ``precision_at_k`` — measures coverage of the expected
    set rather than ranking density. An empty expected list returns 0.0.
    """
    if not expected_mpns:
        return 0.0
    matched = query_hit(expected_mpns, hit_blobs, top_k)
    return len(matched) / len(expected_mpns)


def summarize(
    queries: list[dict[str, Any]],
    hits_by_id: dict[str, list[str]],
    top_k: int,
) -> EvalReport:
    """Score every query against its retrieved hit blobs."""
    report = EvalReport()
    for q in queries:
        expected = [str(m) for m in q.get("expected_mpns", [])]
        blobs = hits_by_id.get(str(q["id"]), [])
        matched = query_hit(expected, blobs, top_k)
        report.results.append(
            QueryResult(
                query_id=str(q["id"]),
                tier=str(q.get("tier", "")),
                query=str(q.get("query", "")),
                expected_mpns=tuple(expected),
                matched_mpns=tuple(matched),
                passed=bool(matched),
                precision_at_k=precision_at_k(expected, blobs, top_k),
                recall_at_k=recall_at_k(expected, blobs, top_k),
            )
        )
    return report


def _fetch_hit_blobs(gateway_url: str, query: str, top_k: int) -> list[str]:
    """Call the live gateway search endpoint; return one blob per hit."""
    import httpx

    resp = httpx.get(
        f"{gateway_url.rstrip('/')}/v1/knowledge/search",
        params={"query": query, "limit": top_k},
        timeout=30.0,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [f"{r.get('sourcePath') or ''} {r.get('content') or ''}" for r in results]


def _load_spec(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return data


def _print_report(report: EvalReport, target: float) -> None:
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        got = ",".join(r.matched_mpns) if r.matched_mpns else "-"
        print(
            f"  [{mark}] {r.query_id} ({r.tier}): {r.query!r} -> matched [{got}] "
            f"P@k={r.precision_at_k:.2f} R@k={r.recall_at_k:.2f}"
        )
    print(
        f"\nhit@k pass rate: {report.passed}/{report.total} "
        f"= {report.pass_rate:.0%} (target {target:.0%})"
    )
    print(
        f"mean precision@k: {report.mean_precision_at_k:.2f}    "
        f"mean recall@k: {report.mean_recall_at_k:.2f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the KB search evaluation (MET-470).")
    parser.add_argument("--gateway-url", default="http://localhost:8000")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--target", type=float, default=None)
    args = parser.parse_args(argv)

    spec = _load_spec(args.queries)
    queries = spec.get("queries", [])
    top_k = args.top_k if args.top_k is not None else int(spec.get("default_top_k", 10))
    target = args.target if args.target is not None else float(spec.get("target_pass_rate", 0.8))

    hits_by_id: dict[str, list[str]] = {}
    for q in queries:
        try:
            hits_by_id[str(q["id"])] = _fetch_hit_blobs(args.gateway_url, str(q["query"]), top_k)
        except Exception as exc:  # noqa: BLE001 — report and continue per query
            print(f"  [ERROR] {q.get('id')}: {exc}", file=sys.stderr)
            hits_by_id[str(q["id"])] = []

    report = summarize(queries, hits_by_id, top_k)
    _print_report(report, target)
    return 0 if report.meets(target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
