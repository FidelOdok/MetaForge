"""The Temporal worker's liveness probe (MET-726).

The property that matters is discrimination: "some worker is polling" is not
health. A dead worker sharing a task queue with a healthy peer must fail, or
the probe is worse than none — it would report healthy for exactly the outage
it exists to catch.
"""

from __future__ import annotations

import socket

import pytest

from orchestrator.worker_healthcheck import (
    check,
    expected_identity_suffix,
    is_polling,
    main,
)


class TestIdentityMatching:
    def test_this_containers_poller_counts(self):
        assert is_polling([f"1@{socket.gethostname()}"])

    def test_another_containers_poller_does_not(self):
        # THE point of the probe. Matching any poller would report healthy
        # while this worker was dead, as long as a peer was alive.
        assert not is_polling(["1@some-other-host"], suffix="@me")

    def test_a_peer_alongside_us_still_counts(self):
        assert is_polling(["1@peer", "7@me"], suffix="@me")

    def test_no_pollers_at_all(self):
        assert not is_polling([], suffix="@me")

    def test_the_suffix_is_the_hostname(self):
        assert expected_identity_suffix() == f"@{socket.gethostname()}"

    def test_a_hostname_that_merely_contains_ours_does_not_count(self):
        # endswith, not "in" -- "@me" must not match "@not-me-really".
        assert not is_polling(["1@me-staging"], suffix="@me")


class TestCheck:
    @pytest.mark.asyncio
    async def test_an_unreachable_server_is_unhealthy_with_a_reason(self, monkeypatch):
        # A probe whose output is a traceback is how the inherited healthcheck
        # managed to look like noise for so long, so every failure path has to
        # produce one readable line.
        monkeypatch.setenv("TEMPORAL_HOST", "127.0.0.1:1")

        healthy, reason = await check(timeout=2.0)

        assert healthy is False
        assert reason
        assert "Traceback" not in reason

    @pytest.mark.asyncio
    async def test_pollers_from_elsewhere_are_reported_as_not_ours(self, monkeypatch):
        async def _fake(*_args, **_kwargs):
            return ["1@somewhere-else"]

        monkeypatch.setattr(
            "orchestrator.worker_healthcheck._poller_identities",
            _fake,
        )

        healthy, reason = await check()

        assert healthy is False
        assert "none from this container" in reason

    @pytest.mark.asyncio
    async def test_an_empty_queue_says_so(self, monkeypatch):
        async def _fake(*_args, **_kwargs):
            return []

        monkeypatch.setattr("orchestrator.worker_healthcheck._poller_identities", _fake)

        healthy, reason = await check()

        assert healthy is False
        assert "no workers polling" in reason

    @pytest.mark.asyncio
    async def test_our_own_poller_is_healthy(self, monkeypatch):
        async def _fake(*_args, **_kwargs):
            return [f"1@{socket.gethostname()}"]

        monkeypatch.setattr("orchestrator.worker_healthcheck._poller_identities", _fake)

        healthy, reason = await check()

        assert healthy is True
        assert "polling" in reason

    @pytest.mark.asyncio
    async def test_the_task_queue_matches_the_worker_default(self, monkeypatch):
        """A probe on the wrong queue would report unhealthy forever -- the
        same bug in a new costume."""
        from orchestrator.temporal_worker import DEFAULT_TASK_QUEUE

        seen: dict[str, str] = {}

        async def _fake(task_queue, host, namespace):
            seen["queue"] = task_queue
            return [f"1@{socket.gethostname()}"]

        monkeypatch.delenv("TEMPORAL_TASK_QUEUE", raising=False)
        monkeypatch.setattr("orchestrator.worker_healthcheck._poller_identities", _fake)

        await check()

        assert seen["queue"] == DEFAULT_TASK_QUEUE


class TestExitCode:
    def test_main_exits_nonzero_when_unhealthy(self, monkeypatch, capsys):
        async def _fake(*_args, **_kwargs):
            return []

        monkeypatch.setattr("orchestrator.worker_healthcheck._poller_identities", _fake)

        assert main() == 1
        assert "unhealthy:" in capsys.readouterr().out

    def test_main_exits_zero_when_healthy(self, monkeypatch, capsys):
        async def _fake(*_args, **_kwargs):
            return [f"1@{socket.gethostname()}"]

        monkeypatch.setattr("orchestrator.worker_healthcheck._poller_identities", _fake)

        assert main() == 0
        assert "healthy:" in capsys.readouterr().out
