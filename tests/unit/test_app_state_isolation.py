"""One app's state must not become the next app's (FORGE-391).

`tests/integration/test_gateway_smoke.py` passed alone and failed after
`tests/unit` in the same process: `/health` reported `degraded` and
`/v1/assistant/proposals` returned a proposal the test never created.
Reproduced on `origin/main` before any of this, so it was not new.

Two process-globals outliving the app that filled them:

* the health checker, whose checks are registered while ``create_app``
  wires up its backends and close over that app's connections, and
* the approval workflow, which holds pending proposals.

Neither is only a test problem. Anything that builds the app more than
once -- a reloader, an embedding host, a test suite -- inherits the
previous one's dependencies and proposals.
"""

from __future__ import annotations

import asyncio

import pytest

from api_gateway.health import (
    ComponentHealth,
    DependencyStatus,
    HealthChecker,
    get_health_checker,
    reset_health_checker,
)


async def _unhealthy() -> ComponentHealth:
    return ComponentHealth(name="postgres", status=DependencyStatus.UNHEALTHY, message="down")


async def _healthy() -> ComponentHealth:
    return ComponentHealth(name="postgres", status=DependencyStatus.HEALTHY)


# ---------------------------------------------------------------------------
# Registering the same check twice
# ---------------------------------------------------------------------------


def test_registering_a_check_twice_keeps_one() -> None:
    """Appending looked harmless and was not. ``create_app`` registers
    `postgres`, `neo4j` and the rest at startup, so a process that builds
    the app twice probed each dependency twice."""
    checker = HealthChecker()
    checker.register_check("postgres", _healthy)
    checker.register_check("postgres", _healthy)
    assert [name for name, _ in checker._checks] == ["postgres"]


def test_the_later_registration_wins() -> None:
    """A second app's check closes over a different connection; keeping
    the first would probe the dead one."""
    checker = HealthChecker()
    checker.register_check("postgres", _unhealthy)
    checker.register_check("postgres", _healthy)
    assert asyncio.run(checker.check_all()).status is DependencyStatus.HEALTHY


def test_a_duplicate_could_flip_degraded_to_unhealthy() -> None:
    """The consequence in production, not just in tests. One unreachable
    database registered twice satisfies `unhealthy_count == len(...)`,
    which reports the whole gateway unhealthy rather than degraded."""
    checker = HealthChecker()
    checker.register_check("postgres", _unhealthy)
    checker.register_check("neo4j", _healthy)
    assert asyncio.run(checker.check_all()).status is DependencyStatus.DEGRADED

    checker.register_check("postgres", _unhealthy)  # would have been a third entry
    assert asyncio.run(checker.check_all()).status is DependencyStatus.DEGRADED


# ---------------------------------------------------------------------------
# A fresh app starts clean
# ---------------------------------------------------------------------------


def test_reset_gives_a_new_checker() -> None:
    first = get_health_checker()
    first.register_check("postgres", _unhealthy)
    second = reset_health_checker()
    assert second is not first
    assert second._checks == []
    assert get_health_checker() is second


def test_create_app_starts_from_a_clean_checker() -> None:
    """The actual failure: an app inheriting a check for a database it
    never configured."""
    from api_gateway.server import create_app

    get_health_checker().register_check("ghost", _unhealthy)
    create_app()
    assert "ghost" not in [name for name, _ in get_health_checker()._checks]


# ---------------------------------------------------------------------------
# Proposals do not cross apps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_app_lists_no_proposals_from_the_last_one() -> None:
    from uuid import uuid4

    from api_gateway.assistant.routes import workflow
    from api_gateway.server import create_app

    await workflow.propose_change(
        agent_code="human",
        description="left over",
        diff={"before": "a", "after": "b"},
        work_products=[uuid4()],
        session_id=uuid4(),
    )
    assert workflow.get_pending_proposals()

    create_app()
    assert workflow.get_pending_proposals() == []


def test_reset_clears_in_place_rather_than_rebinding() -> None:
    """server.py binds this instance by name at import. Rebinding the
    module global would leave that reference pointing at the old object,
    so the reset has to mutate."""
    from api_gateway.assistant.routes import workflow

    before = id(workflow)
    workflow.reset()
    from api_gateway.assistant.routes import workflow as again

    assert id(again) == before
