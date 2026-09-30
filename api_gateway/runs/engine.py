"""Which engine actually runs a design flow (FORGE-401).

``/v1/runs`` started design flows as ``asyncio.create_task`` in the gateway
process. That is not an engine, it is a coroutine: a restart lost every
in-flight run, and a run parked at a gate — the one with a human already
committed to it — lost the most.

This module chooses between the real engine and the in-process executor, and
the choice is **explicit**. ``METAFORGE_FLOW_ENGINE`` takes:

``temporal``
    The default. Runs go to Temporal (ADR-001). If Temporal is unreachable,
    starting a run fails loudly and creates nothing.

``in_process``
    The old executor. Kept because the unit tests need a double that runs
    without a server, and because a contributor with no Docker should still
    be able to exercise the flow logic. It logs a warning on every start, so
    a deployment that ends up here by accident says so continuously rather
    than looking healthy.

There is deliberately no automatic fall-through from the first to the second.
That fallback would work, which is the problem: runs keep starting, the
dashboard keeps filling, and nobody finds out the engine is not durable until
a restart eats a day's work. This codebase has produced that shape enough
times to name it.
"""

from __future__ import annotations

import os
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_TEMPORAL_HOST",
    "FLOW_ENGINE_ENV",
    "FlowEngine",
    "resolve_flow_engine",
    "temporal_target",
]

FLOW_ENGINE_ENV = "METAFORGE_FLOW_ENGINE"
DEFAULT_TEMPORAL_HOST = "localhost:7233"


class FlowEngine(StrEnum):
    TEMPORAL = "temporal"
    IN_PROCESS = "in_process"


def resolve_flow_engine() -> FlowEngine:
    """Read the configured engine, refusing anything unrecognised.

    An unknown value is an error rather than a quiet default. "I set the env
    var and nothing changed" is precisely how a deployment ends up running
    the engine it thought it had turned off.
    """
    raw = (os.environ.get(FLOW_ENGINE_ENV) or FlowEngine.TEMPORAL.value).strip().lower()
    try:
        engine = FlowEngine(raw)
    except ValueError:
        raise ValueError(
            f"{FLOW_ENGINE_ENV}={raw!r} is not a valid engine. "
            f"Use one of: {', '.join(e.value for e in FlowEngine)}."
        ) from None
    if engine is FlowEngine.IN_PROCESS:
        logger.warning(
            "design_flow_engine_in_process",
            detail=(
                "design-flow runs are NOT durable: a gateway restart loses every "
                "in-flight run, including runs waiting at a gate. This is the test "
                f"double. Unset {FLOW_ENGINE_ENV} for the real engine."
            ),
        )
    return engine


def temporal_target() -> str:
    """Where Temporal is, as compose already spells it (``TEMPORAL_HOST``)."""
    return (os.environ.get("TEMPORAL_HOST") or DEFAULT_TEMPORAL_HOST).strip()


#: The metrics live on :class:`~observability.metrics.MetricsRegistry` with
#: every other MetaForge metric (``DESIGN_FLOW_ENGINE_UNAVAILABLE``,
#: ``DESIGN_FLOW_RUN_STARTED``, ``DESIGN_FLOW_GATE_TOTAL``), and the alert on
#: the first is in ``observability/alerting/rules.yaml``.
#:
#: An unreachable engine is counted rather than only logged, because this is
#: the one failure whose entire point is that somebody finds out. A log line
#: nobody greps for is the same as no signal at all.
