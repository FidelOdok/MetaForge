"""Neo4j connect retry + the twin-backend health signal (MET-710).

The incident: the gateway and Neo4j restarted together, the gateway resolved
``neo4j`` a moment before its DNS entry existed, and it then ran on the
in-memory twin for 43 hours — ignoring 623 persisted nodes — while ``/health``
reported ``healthy`` with no neo4j component at all.

Two independent failures, so two independent guards:

1. a momentary connect failure should be retried, not treated as absence;
2. running in-memory *while configured for Neo4j* should be visible.
"""

from __future__ import annotations

from typing import Any

import pytest

from twin_core.api import (
    DEFAULT_NEO4J_CONNECT_ATTEMPTS,
    _connect_with_retry,
    neo4j_connect_attempts,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


class _Graph:
    """Fails ``fail_times`` times, then connects."""

    def __init__(self, fail_times: int, exc: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self._exc = exc or OSError(
            "Failed to DNS resolve address neo4j:7687: [Errno -3] "
            "Temporary failure in name resolution"
        )

    async def connect(self) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise self._exc


@pytest.fixture
def no_sleep():
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    return slept, _sleep


@pytest.mark.asyncio
async def test_connects_first_try_without_retrying(no_sleep):
    slept, sleep = no_sleep
    graph, log = _Graph(fail_times=0), _Logger()

    await _connect_with_retry(graph, "bolt://neo4j:7687", log, sleep=sleep)

    assert graph.attempts == 1
    assert slept == []
    # No noise on the happy path.
    assert "neo4j_connect_retrying" not in log.names()


@pytest.mark.asyncio
async def test_a_transient_dns_failure_is_retried_and_recovers(no_sleep):
    # This is the incident, reproduced: the name resolves on a later attempt.
    slept, sleep = no_sleep
    graph, log = _Graph(fail_times=2), _Logger()

    await _connect_with_retry(graph, "bolt://neo4j:7687", log, sleep=sleep)

    assert graph.attempts == 3
    assert slept == [0.5, 1.0]  # backoff, not a busy loop
    assert "neo4j_connect_retrying" in log.names()
    assert "neo4j_connect_recovered" in log.names()


@pytest.mark.asyncio
async def test_a_permanent_failure_still_raises_after_the_attempts(no_sleep):
    # The caller's contract is unchanged — the gateway still decides between
    # fail-fast and fallback via METAFORGE_REQUIRE_NEO4J.
    slept, sleep = no_sleep
    graph, log = _Graph(fail_times=99), _Logger()

    with pytest.raises(OSError, match="Temporary failure in name resolution"):
        await _connect_with_retry(graph, "bolt://neo4j:7687", log, attempts=3, sleep=sleep)

    assert graph.attempts == 3
    assert "neo4j_connect_exhausted" in log.names()


@pytest.mark.asyncio
async def test_backoff_never_indexes_past_its_table(no_sleep):
    # More attempts than backoff entries must reuse the last delay, not crash.
    slept, sleep = no_sleep
    graph, log = _Graph(fail_times=99), _Logger()

    with pytest.raises(OSError):
        await _connect_with_retry(graph, "bolt://x", log, attempts=8, sleep=sleep)

    assert len(slept) == 7
    assert slept[-1] == 4.0


def test_attempt_count_is_configurable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("METAFORGE_NEO4J_CONNECT_ATTEMPTS", raising=False)
    assert neo4j_connect_attempts() == DEFAULT_NEO4J_CONNECT_ATTEMPTS

    monkeypatch.setenv("METAFORGE_NEO4J_CONNECT_ATTEMPTS", "9")
    assert neo4j_connect_attempts() == 9

    # 0 or a negative value would skip the connect entirely; floor at 1.
    monkeypatch.setenv("METAFORGE_NEO4J_CONNECT_ATTEMPTS", "0")
    assert neo4j_connect_attempts() == 1

    monkeypatch.setenv("METAFORGE_NEO4J_CONNECT_ATTEMPTS", "nonsense")
    assert neo4j_connect_attempts() == DEFAULT_NEO4J_CONNECT_ATTEMPTS
