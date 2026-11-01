"""Host watchdog (FORGE-713).

Docker restarts a container that exits, never one that keeps running in a bad
state. On fidel-dev the gateway served an empty in-memory twin while reporting
healthy, and memory ran out on a host with no swap, with nothing watching
either. These tests drive one watchdog pass at a time against a fake Docker.
"""

from __future__ import annotations

from typing import Any

import pytest

from observability import watchdog as wd
from observability.watchdog import Watchdog, WatchdogConfig


class _Docker:
    def __init__(self, containers: list[dict[str, Any]]) -> None:
        self.rows = containers
        self.restarted: list[str] = []
        self.fail = False

    def containers(self, project: str) -> list[dict[str, Any]]:
        assert project == "metaforge"
        return self.rows

    def restart(self, container_id: str) -> None:
        if self.fail:
            raise RuntimeError("docker said no")
        self.restarted.append(container_id)


class _Metrics:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.restarts: list[tuple[str, str, str]] = []

    def record_watchdog_check(self, **kw: Any) -> None:
        self.checks.append(kw)

    def record_watchdog_restart(self, service: str, reason: str, outcome: str) -> None:
        self.restarts.append((service, reason, outcome))


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _row(service: str, health: str = "healthy", cid: str | None = None) -> dict[str, Any]:
    suffix = f" ({health})" if health else ""
    return {
        "Id": cid or f"id-{service}",
        "State": "running",
        "Status": f"Up 3 minutes{suffix}",
        "Labels": {"com.docker.compose.service": service},
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, Any] = {"twin": False, "memory": 4 * 1024**3}
    monkeypatch.setattr(wd, "gateway_twin_in_memory", lambda url: state["twin"])
    monkeypatch.setattr(wd, "read_memory_available", lambda: state["memory"])
    return state


def _watchdog(rows: list[dict[str, Any]], **cfg: Any) -> tuple[Watchdog, _Docker, _Metrics, _Clock]:
    docker, metrics, clock = _Docker(rows), _Metrics(), _Clock()
    dog = Watchdog(
        config=WatchdogConfig(project="metaforge", **cfg),
        docker=docker,  # type: ignore[arg-type]
        metrics=metrics,  # type: ignore[arg-type]
        self_id="id-watchdog",
        clock=clock,
    )
    return dog, docker, metrics, clock


def test_a_healthy_stack_is_left_alone(env):
    dog, docker, metrics, _ = _watchdog([_row("gateway"), _row("neo4j")])
    for _ in range(5):
        dog.check()
    assert docker.restarted == []
    assert metrics.checks[-1] == {
        "unhealthy": 0,
        "twin_in_memory": False,
        "memory_available": 4 * 1024**3,
    }


def test_an_unhealthy_container_is_restarted_only_after_consecutive_checks(env):
    dog, docker, metrics, _ = _watchdog([_row("mcp-http", "unhealthy"), _row("gateway")])
    dog.check()
    dog.check()
    assert docker.restarted == []  # two passes is not yet a pattern
    dog.check()
    assert docker.restarted == ["id-mcp-http"]
    assert metrics.restarts == [("mcp-http", "unhealthy", "restarted")]


def test_one_healthy_pass_resets_the_streak(env):
    rows = [_row("mcp-http", "unhealthy")]
    dog, docker, _, _ = _watchdog(rows)
    dog.check()
    dog.check()
    rows[0] = _row("mcp-http", "healthy")
    dog.check()
    rows[0] = _row("mcp-http", "unhealthy")
    dog.check()
    dog.check()
    assert docker.restarted == []


def test_a_container_still_starting_or_without_a_healthcheck_is_not_touched(env):
    dog, docker, _, _ = _watchdog([_row("kafka", "health: starting"), _row("zookeeper", "")])
    for _ in range(5):
        dog.check()
    assert docker.restarted == []


def test_a_crash_loop_is_reported_not_restarted_forever(env):
    dog, docker, metrics, clock = _watchdog([_row("mcp-http", "unhealthy")], unhealthy_checks=1)
    for _ in range(3):
        dog.check()
        clock.now += 700  # past the 10-minute cooldown each time
    assert len(docker.restarted) == 3
    dog.check()  # the fourth within the hour hits the cap
    assert len(docker.restarted) == 3
    assert metrics.restarts[-1] == ("mcp-http", "unhealthy", "suppressed")


def test_cooldown_spaces_restarts_of_the_same_service(env):
    dog, docker, metrics, clock = _watchdog([_row("mcp-http", "unhealthy")], unhealthy_checks=1)
    dog.check()
    clock.now += 60
    dog.check()
    assert docker.restarted == ["id-mcp-http"]
    assert metrics.restarts[-1] == ("mcp-http", "unhealthy", "suppressed")
    clock.now += 60
    dog.check()  # still cooling down: reported once per episode, not every pass
    assert [r[2] for r in metrics.restarts].count("suppressed") == 1
    clock.now += 600
    dog.check()
    assert docker.restarted == ["id-mcp-http", "id-mcp-http"]


def test_the_gateway_on_an_in_memory_twin_is_restarted(env):
    # The FORGE-712 state: a healthy-looking gateway serving an empty twin.
    env["twin"] = True
    dog, docker, metrics, _ = _watchdog([_row("gateway"), _row("neo4j")])
    dog.check()
    assert docker.restarted == []
    assert metrics.checks[-1]["twin_in_memory"] is True
    dog.check()
    assert docker.restarted == ["id-gateway"]
    assert metrics.restarts == [("gateway", "twin_in_memory", "restarted")]


def test_an_unanswering_gateway_is_left_to_its_health_check(env):
    env["twin"] = None  # /health did not answer
    dog, docker, metrics, _ = _watchdog([_row("gateway", "health: starting")])
    for _ in range(3):
        dog.check()
    assert docker.restarted == []
    assert metrics.checks[-1]["twin_in_memory"] is False


def test_low_memory_is_reported_and_nothing_is_killed(env, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(wd.logger, "warning", lambda event, **kw: events.append(event))
    env["memory"] = 200 * 1024 * 1024
    dog, docker, metrics, _ = _watchdog([_row("gateway"), _row("dashboard-dev", "")])
    dog.check()
    dog.check()
    assert docker.restarted == []
    assert metrics.checks[-1]["memory_available"] == 200 * 1024 * 1024
    assert events.count("watchdog_memory_low") == 1  # once per episode, not every pass


def test_a_failed_restart_is_recorded_and_the_watch_continues(env):
    dog, docker, metrics, _ = _watchdog([_row("mcp-http", "unhealthy")], unhealthy_checks=1)
    docker.fail = True
    dog.check()
    assert metrics.restarts == [("mcp-http", "unhealthy", "failed")]
    assert len(metrics.checks) == 1


def test_the_watchdog_never_restarts_itself(env):
    dog, docker, _, _ = _watchdog(
        [_row("watchdog", "unhealthy", cid="id-watchdog")], unhealthy_checks=1
    )
    dog.check()
    assert docker.restarted == []


def test_twin_backend_degraded_is_read_from_health(monkeypatch):
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _health(components):
        body = json.dumps({"status": "degraded", "components": components}).encode()
        monkeypatch.setattr(wd.urllib.request, "urlopen", lambda url, timeout: _Resp(body))

    _health([{"name": "twin_backend", "status": "degraded"}])
    assert wd.gateway_twin_in_memory("http://gateway:8000/health") is True
    _health([{"name": "twin_backend", "status": "healthy"}])
    assert wd.gateway_twin_in_memory("http://gateway:8000/health") is False

    def _boom(url, timeout):
        raise OSError("refused")

    monkeypatch.setattr(wd.urllib.request, "urlopen", _boom)
    assert wd.gateway_twin_in_memory("http://gateway:8000/health") is None


def test_memory_is_read_from_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  8061088 kB\nMemAvailable:  2097152 kB\n")
    assert wd.read_memory_available(str(meminfo)) == 2097152 * 1024
    assert wd.read_memory_available(str(tmp_path / "missing")) is None


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("METAFORGE_WATCHDOG_INTERVAL", "1")  # floored at 5
    monkeypatch.setenv("METAFORGE_WATCHDOG_MEMORY_WARN_MB", "1024")
    monkeypatch.setenv("METAFORGE_WATCHDOG_MAX_RESTARTS_PER_HOUR", "nonsense")
    cfg = WatchdogConfig.from_env()
    assert cfg.interval == 5
    assert cfg.memory_warn_mb == 1024
    assert cfg.max_restarts_per_hour == WatchdogConfig().max_restarts_per_hour


def test_the_restart_call_outlasts_dockers_stop_timeout(monkeypatch):
    # Live on fidel-dev: a container that ignored SIGTERM held the restart call
    # for Docker's full 30s stop timeout, a 30s client timeout fired first, and
    # a restart that had happened was reported as failed.
    seen: dict[str, Any] = {}

    def _request(self, method, path, timeout=30.0):
        seen.update(method=method, path=path, timeout=timeout)

    monkeypatch.setattr(wd.DockerClient, "_request", _request)
    wd.DockerClient("/var/run/docker.sock").restart("abc")
    assert seen["method"] == "POST"
    assert f"t={wd._STOP_TIMEOUT}" in seen["path"]
    assert seen["timeout"] > wd._STOP_TIMEOUT
