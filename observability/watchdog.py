"""Host watchdog for a MetaForge compose stack (FORGE-713).

Docker restarts a container that *exits*; it does nothing about one that keeps
running in a bad state. On fidel-dev that left two failures with nobody to
notice them:

* the gateway came up on the in-memory twin after a reboot and served an
  empty twin while its container reported healthy (FORGE-712), and
* memory ran down to ~200 MB on a host with no swap until the box went
  offline, with no warning beforehand.

This process runs beside the stack and, every ``interval`` seconds:

1. restarts a container of the same compose project that Docker has reported
   ``unhealthy`` for ``unhealthy_checks`` passes in a row;
2. restarts the gateway when its ``/health`` says the twin runs in memory
   although Neo4j is configured;
3. reads the host's ``MemAvailable`` and warns when it is low. It never kills
   anything for memory: choosing a victim is a human decision.

A restart is skipped when the same service was restarted within ``cooldown``
seconds, or ``max_restarts_per_hour`` times in the last hour, so a container
that fails every start is reported rather than restarted in a loop.

Docker is reached over its unix socket with the standard library, so the
watchdog needs nothing beyond this image. Run with ``python -m
observability.watchdog``.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import structlog

from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

# Named, not __name__: run as ``python -m`` that would be "__main__".
logger = structlog.get_logger("observability.watchdog")
tracer = get_tracer("observability.watchdog")

_PROJECT_LABEL = "com.docker.compose.project"
_SERVICE_LABEL = "com.docker.compose.service"
_TWIN_IN_MEMORY = "twin_in_memory"
_UNHEALTHY = "unhealthy"
_STOP_TIMEOUT = 30
"""Seconds Docker waits for a container to stop before killing it on restart.

The restart call does not answer until the container is back, so its HTTP
timeout must outlast this. Live on fidel-dev, a container that ignored SIGTERM
made a 30s client timeout report a restart that had in fact happened as failed.
"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class WatchdogConfig:
    """Settings, each overridable with a ``METAFORGE_WATCHDOG_*`` variable."""

    interval: int = 30
    unhealthy_checks: int = 3
    twin_checks: int = 2
    cooldown: int = 600
    max_restarts_per_hour: int = 3
    memory_warn_mb: int = 512
    gateway_service: str = "gateway"
    gateway_health_url: str = "http://gateway:8000/health"
    docker_socket: str = "/var/run/docker.sock"
    project: str = ""

    @classmethod
    def from_env(cls) -> WatchdogConfig:
        d = cls()
        return cls(
            interval=max(5, _env_int("METAFORGE_WATCHDOG_INTERVAL", d.interval)),
            unhealthy_checks=max(
                1, _env_int("METAFORGE_WATCHDOG_UNHEALTHY_CHECKS", d.unhealthy_checks)
            ),
            twin_checks=max(1, _env_int("METAFORGE_WATCHDOG_TWIN_CHECKS", d.twin_checks)),
            cooldown=max(0, _env_int("METAFORGE_WATCHDOG_COOLDOWN", d.cooldown)),
            max_restarts_per_hour=max(
                0, _env_int("METAFORGE_WATCHDOG_MAX_RESTARTS_PER_HOUR", d.max_restarts_per_hour)
            ),
            memory_warn_mb=max(0, _env_int("METAFORGE_WATCHDOG_MEMORY_WARN_MB", d.memory_warn_mb)),
            gateway_service=os.environ.get("METAFORGE_WATCHDOG_GATEWAY_SERVICE", d.gateway_service),
            gateway_health_url=os.environ.get(
                "METAFORGE_WATCHDOG_GATEWAY_HEALTH_URL", d.gateway_health_url
            ),
            docker_socket=os.environ.get("METAFORGE_WATCHDOG_DOCKER_SOCKET", d.docker_socket),
            project=os.environ.get("METAFORGE_WATCHDOG_PROJECT", d.project),
        )


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 30.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class DockerClient:
    """The three Docker Engine API calls the watchdog needs."""

    def __init__(self, socket_path: str) -> None:
        self._socket = socket_path

    def _request(self, method: str, path: str, timeout: float = 30.0) -> Any:
        conn = _UnixConnection(self._socket, timeout=timeout)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            body = resp.read()
        finally:
            conn.close()
        if resp.status >= 400:
            raise RuntimeError(f"docker {method} {path}: {resp.status} {body[:200]!r}")
        return json.loads(body) if body else None

    def inspect(self, container_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", f"/containers/{quote(container_id)}/json")
        return result

    def containers(self, project: str) -> list[dict[str, Any]]:
        filters = quote(json.dumps({"label": [f"{_PROJECT_LABEL}={project}"]}))
        result: list[dict[str, Any]] = self._request("GET", f"/containers/json?filters={filters}")
        return result

    def restart(self, container_id: str) -> None:
        self._request(
            "POST",
            f"/containers/{quote(container_id)}/restart?t={_STOP_TIMEOUT}",
            timeout=_STOP_TIMEOUT + 60,
        )


def read_memory_available(path: str = "/proc/meminfo") -> int | None:
    """Host MemAvailable in bytes. A container reads the host's figures here."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def gateway_twin_in_memory(url: str) -> bool | None:
    """True when /health reports the twin running in memory against intent.

    ``None`` when the gateway gave no answer: an unreachable gateway is the
    container health check's business, not this one's.
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 — fixed internal URL
            body = json.loads(resp.read())
    except Exception:  # noqa: BLE001 — any failure means "no answer"
        return None
    for component in body.get("components") or []:
        if component.get("name") == "twin_backend":
            return bool(component.get("status") == "degraded")
    return False


def _health(container: dict[str, Any]) -> str:
    """Docker's health status from a /containers/json row ("" when none)."""
    status = str(container.get("Status") or "")
    if "(unhealthy)" in status:
        return _UNHEALTHY
    if "(healthy)" in status:
        return "healthy"
    return ""


@dataclass
class Watchdog:
    config: WatchdogConfig
    docker: DockerClient
    metrics: MetricsCollector
    self_id: str = ""
    clock: Any = time.monotonic
    _unhealthy_streak: dict[str, int] = field(default_factory=dict)
    _twin_streak: int = 0
    _restarts: dict[str, deque[float]] = field(default_factory=dict)
    _suppressed: set[str] = field(default_factory=set)
    _memory_low: bool = False

    def _allowed(self, service: str) -> str | None:
        """Why a restart of *service* must wait, or None when it may go ahead."""
        now = self.clock()
        history = self._restarts.setdefault(service, deque())
        while history and now - history[0] > 3600:
            history.popleft()
        if history and now - history[-1] < self.config.cooldown:
            return "cooldown"
        if len(history) >= self.config.max_restarts_per_hour:
            return "hourly_cap"
        return None

    def _restart(self, container: dict[str, Any], service: str, reason: str) -> bool:
        blocked = self._allowed(service)
        if blocked is not None:
            # Once per episode: every pass of a 10-minute cooldown is the same news.
            if service not in self._suppressed:
                self._suppressed.add(service)
                logger.warning(
                    "watchdog_restart_suppressed", service=service, reason=reason, why=blocked
                )
                self.metrics.record_watchdog_restart(service, reason, "suppressed")
            return False
        self._suppressed.discard(service)
        with tracer.start_as_current_span("watchdog.restart") as span:
            span.set_attribute("watchdog.service", service)
            span.set_attribute("watchdog.reason", reason)
            try:
                self.docker.restart(container["Id"])
            except Exception as exc:  # noqa: BLE001 — reported, then the loop carries on
                span.record_exception(exc)
                logger.error(
                    "watchdog_restart_failed", service=service, reason=reason, error=str(exc)
                )
                self.metrics.record_watchdog_restart(service, reason, "failed")
                return False
        self._restarts[service].append(self.clock())
        logger.warning("watchdog_restarted", service=service, reason=reason)
        self.metrics.record_watchdog_restart(service, reason, "restarted")
        return True

    def check(self) -> None:
        """One pass over the stack."""
        with tracer.start_as_current_span("watchdog.check") as span:
            containers = [
                c
                for c in self.docker.containers(self.config.project)
                if c.get("Id") != self.self_id and c.get("State") == "running"
            ]
            by_service = {
                str(c.get("Labels", {}).get(_SERVICE_LABEL, c["Id"][:12])): c for c in containers
            }

            unhealthy = 0
            for service, container in by_service.items():
                if _health(container) != _UNHEALTHY:
                    self._unhealthy_streak.pop(service, None)
                    if service != self.config.gateway_service:
                        self._suppressed.discard(service)
                    continue
                unhealthy += 1
                streak = self._unhealthy_streak.get(service, 0) + 1
                self._unhealthy_streak[service] = streak
                if streak >= self.config.unhealthy_checks and self._restart(
                    container, service, _UNHEALTHY
                ):
                    self._unhealthy_streak.pop(service, None)

            in_memory = gateway_twin_in_memory(self.config.gateway_health_url)
            gateway = by_service.get(self.config.gateway_service)
            if in_memory and gateway is not None:
                self._twin_streak += 1
                if self._twin_streak >= self.config.twin_checks and self._restart(
                    gateway, self.config.gateway_service, _TWIN_IN_MEMORY
                ):
                    self._twin_streak = 0
            else:
                self._twin_streak = 0
                if gateway is not None and _health(gateway) != _UNHEALTHY:
                    self._suppressed.discard(self.config.gateway_service)

            memory = read_memory_available()
            low = memory is not None and memory < self.config.memory_warn_mb * 1024 * 1024
            if low and not self._memory_low:
                logger.warning(
                    "watchdog_memory_low",
                    available_mb=memory // (1024 * 1024) if memory is not None else None,
                    threshold_mb=self.config.memory_warn_mb,
                )
            elif self._memory_low and not low:
                logger.info(
                    "watchdog_memory_recovered", available_mb=(memory or 0) // (1024 * 1024)
                )
            self._memory_low = low

            span.set_attribute("watchdog.containers", len(containers))
            span.set_attribute("watchdog.unhealthy", unhealthy)
            self.metrics.record_watchdog_check(
                unhealthy=unhealthy, twin_in_memory=bool(in_memory), memory_available=memory
            )


def _discover_self(docker: DockerClient) -> tuple[str, str]:
    """This container's id and compose project, from its hostname."""
    try:
        me = docker.inspect(socket.gethostname())
    except Exception:  # noqa: BLE001 — not in a container, or no such id
        return "", ""
    return str(me.get("Id", "")), str(
        (me.get("Config", {}).get("Labels") or {}).get(_PROJECT_LABEL, "")
    )


def main() -> None:
    from observability.bootstrap import init_observability, shutdown_observability
    from observability.config import ObservabilityConfig, OtlpExporterConfig
    from observability.logging import configure_logging
    from observability.metrics import collector_for

    otel_config = ObservabilityConfig(
        service_name="metaforge-watchdog",
        environment=os.environ.get("METAFORGE_ENV", "development"),
        otlp=OtlpExporterConfig(
            endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        ),
    )
    configure_logging(otel_config)
    state = init_observability(otel_config)

    config = WatchdogConfig.from_env()
    docker = DockerClient(config.docker_socket)
    self_id, project = _discover_self(docker)
    if not config.project:
        if not project:
            raise SystemExit(
                "watchdog: no compose project. Run it as a compose service, or set "
                "METAFORGE_WATCHDOG_PROJECT."
            )
        config = WatchdogConfig(**{**config.__dict__, "project": project})

    watchdog = Watchdog(
        config=config, docker=docker, metrics=collector_for("watchdog"), self_id=self_id
    )
    logger.info("watchdog_started", **config.__dict__)

    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopping:
        try:
            watchdog.check()
        except Exception as exc:  # noqa: BLE001 — one bad pass must not end the watch
            logger.error("watchdog_check_failed", error=str(exc))
        deadline = time.monotonic() + config.interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(1)
    shutdown_observability(state)


if __name__ == "__main__":
    main()
