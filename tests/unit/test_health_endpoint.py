"""Unit tests for MET-103: Enhanced /health endpoint.

Tests cover the ``HealthChecker``, ``HealthResponse`` model, aggregation
rules, uptime tracking, and the FastAPI ``/health`` route via TestClient.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.health import (
    ComponentHealth,
    DependencyStatus,
    HealthChecker,
    HealthResponse,
    health_router,
    set_health_checker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _healthy_check() -> ComponentHealth:
    return ComponentHealth(
        name="database",
        status=DependencyStatus.HEALTHY,
        latency_ms=1.5,
    )


async def _degraded_check() -> ComponentHealth:
    return ComponentHealth(
        name="cache",
        status=DependencyStatus.DEGRADED,
        latency_ms=250.0,
        message="high latency",
    )


async def _unhealthy_check() -> ComponentHealth:
    return ComponentHealth(
        name="message_broker",
        status=DependencyStatus.UNHEALTHY,
        message="connection refused",
    )


async def _exploding_check() -> ComponentHealth:
    raise ConnectionError("timeout talking to service")


# ===================================================================
# Model tests
# ===================================================================


class TestModels:
    def test_dependency_status_values(self) -> None:
        assert DependencyStatus.HEALTHY == "healthy"
        assert DependencyStatus.DEGRADED == "degraded"
        assert DependencyStatus.UNHEALTHY == "unhealthy"

    def test_component_health_minimal(self) -> None:
        c = ComponentHealth(name="db", status=DependencyStatus.HEALTHY)
        assert c.latency_ms is None
        assert c.message is None

    def test_component_health_full(self) -> None:
        c = ComponentHealth(
            name="redis",
            status=DependencyStatus.DEGRADED,
            latency_ms=42.0,
            message="slow",
        )
        assert c.latency_ms == 42.0
        assert c.message == "slow"

    def test_health_response_defaults(self) -> None:
        from datetime import UTC, datetime

        resp = HealthResponse(
            status=DependencyStatus.HEALTHY,
            uptime_seconds=10.0,
            timestamp=datetime.now(UTC),
        )
        assert resp.version == "0.1.0"
        assert resp.components == []


# ===================================================================
# HealthChecker — aggregation logic
# ===================================================================


class TestHealthChecker:
    @pytest.mark.asyncio
    async def test_no_registered_checks_returns_healthy(self) -> None:
        checker = HealthChecker()
        result = await checker.check_all()
        assert result.status == DependencyStatus.HEALTHY
        assert result.components == []

    @pytest.mark.asyncio
    async def test_all_healthy(self) -> None:
        checker = HealthChecker()
        checker.register_check("db", _healthy_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.HEALTHY
        assert len(result.components) == 1
        assert result.components[0].name == "database"

    @pytest.mark.asyncio
    async def test_one_unhealthy_degrades_overall(self) -> None:
        checker = HealthChecker()
        checker.register_check("db", _healthy_check)
        checker.register_check("broker", _unhealthy_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_all_unhealthy_returns_unhealthy(self) -> None:
        checker = HealthChecker()
        checker.register_check("broker", _unhealthy_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_multiple_all_unhealthy(self) -> None:
        checker = HealthChecker()
        checker.register_check("a", _unhealthy_check)
        checker.register_check("b", _unhealthy_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_degraded_component_degrades_overall(self) -> None:
        checker = HealthChecker()
        checker.register_check("cache", _degraded_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_exception_in_check_becomes_unhealthy(self) -> None:
        checker = HealthChecker()
        checker.register_check("service", _exploding_check)
        result = await checker.check_all()
        assert result.status == DependencyStatus.UNHEALTHY
        assert result.components[0].status == DependencyStatus.UNHEALTHY
        assert "timeout talking to service" in (result.components[0].message or "")

    @pytest.mark.asyncio
    async def test_uptime_is_positive(self) -> None:
        checker = HealthChecker()
        # Tiny sleep to ensure measurable uptime
        result = await checker.check_all()
        assert result.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_uptime_increases_over_time(self) -> None:
        checker = HealthChecker()
        r1 = await checker.check_all()
        time.sleep(0.01)
        r2 = await checker.check_all()
        assert r2.uptime_seconds >= r1.uptime_seconds

    @pytest.mark.asyncio
    async def test_timestamp_present(self) -> None:
        checker = HealthChecker()
        result = await checker.check_all()
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_version_default(self) -> None:
        checker = HealthChecker()
        result = await checker.check_all()
        assert result.version == "0.1.0"


# ===================================================================
# FastAPI integration via TestClient
# ===================================================================


class TestHealthEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(health_router)
        checker = HealthChecker()
        set_health_checker(checker)
        return TestClient(app)

    def test_health_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_structure(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "status" in data
        assert "version" in data
        assert "uptime_seconds" in data
        assert "components" in data
        assert "timestamp" in data

    def test_health_status_is_healthy_by_default(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert data["status"] == "healthy"

    def test_health_with_registered_check(self) -> None:
        app = FastAPI()
        app.include_router(health_router)
        checker = HealthChecker()
        checker.register_check("db", _healthy_check)
        set_health_checker(checker)

        tc = TestClient(app)
        data = tc.get("/health").json()
        assert data["status"] == "healthy"
        assert len(data["components"]) == 1
        assert data["components"][0]["name"] == "database"

    def test_health_with_unhealthy_check(self) -> None:
        app = FastAPI()
        app.include_router(health_router)
        checker = HealthChecker()
        checker.register_check("db", _healthy_check)
        checker.register_check("broker", _unhealthy_check)
        set_health_checker(checker)

        tc = TestClient(app)
        data = tc.get("/health").json()
        assert data["status"] == "degraded"
        assert len(data["components"]) == 2
