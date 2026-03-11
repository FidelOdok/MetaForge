"""MET-103: Enhanced health endpoint for the MetaForge Gateway.

Provides a ``/health`` endpoint that returns structured JSON describing the
overall gateway status, individual component health, uptime, and version.

Usage::

    from api_gateway.health import health_router, HealthChecker

    checker = HealthChecker()
    checker.register_check("database", my_db_check)

    app = FastAPI()
    app.include_router(health_router)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DependencyStatus(StrEnum):
    """Health status for a single component or the overall system."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """Health snapshot for one downstream dependency."""

    name: str
    status: DependencyStatus
    latency_ms: float | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    """Top-level response from ``GET /health``."""

    status: DependencyStatus
    version: str = "0.1.0"
    uptime_seconds: float
    components: list[ComponentHealth] = []
    timestamp: datetime


# ---------------------------------------------------------------------------
# Health checker
# ---------------------------------------------------------------------------

# Type alias for an async health-check function
HealthCheckFn = Callable[[], Coroutine[Any, Any, ComponentHealth]]


class HealthChecker:
    """Runs registered health checks and aggregates the results.

    Component checks are async callables that return a ``ComponentHealth``.
    If any component is unhealthy the overall status is ``degraded``.  If
    *all* components are unhealthy the overall status is ``unhealthy``.
    """

    def __init__(self) -> None:
        self._start_time: float = time.monotonic()
        self._checks: list[tuple[str, HealthCheckFn]] = []

    def register_check(self, name: str, check_fn: HealthCheckFn) -> None:
        """Register an async health-check callable under *name*."""
        self._checks.append((name, check_fn))

    async def check_all(self) -> HealthResponse:
        """Execute every registered check and return an aggregated response."""
        components: list[ComponentHealth] = []

        for name, fn in self._checks:
            try:
                result = await fn()
                components.append(result)
            except Exception as exc:  # noqa: BLE001
                components.append(
                    ComponentHealth(
                        name=name,
                        status=DependencyStatus.UNHEALTHY,
                        message=str(exc),
                    )
                )

        # Aggregate: any unhealthy -> degraded; ALL unhealthy -> unhealthy
        overall = DependencyStatus.HEALTHY
        if components:
            unhealthy_count = sum(1 for c in components if c.status == DependencyStatus.UNHEALTHY)
            degraded_count = sum(1 for c in components if c.status == DependencyStatus.DEGRADED)
            if unhealthy_count == len(components):
                overall = DependencyStatus.UNHEALTHY
            elif unhealthy_count > 0 or degraded_count > 0:
                overall = DependencyStatus.DEGRADED

        uptime = time.monotonic() - self._start_time

        return HealthResponse(
            status=overall,
            uptime_seconds=round(uptime, 3),
            components=components,
            timestamp=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Module-level singleton and FastAPI dependency
# ---------------------------------------------------------------------------

_health_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    """FastAPI dependency that returns the module-level ``HealthChecker``."""
    global _health_checker  # noqa: PLW0603
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


def set_health_checker(checker: HealthChecker) -> None:
    """Replace the module-level health checker (useful for tests)."""
    global _health_checker  # noqa: PLW0603
    _health_checker = checker


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

health_router = APIRouter(tags=["health"])


@health_router.get("/health", response_model=HealthResponse)
async def health_check(
    checker: HealthChecker = Depends(get_health_checker),
) -> HealthResponse:
    """Return the aggregated health of the gateway and its dependencies."""
    return await checker.check_all()
