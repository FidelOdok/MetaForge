"""Live inputs for the workflow lifecycle views (FORGE-539).

``orchestrator.design_flow`` holds the pure lifecycle logic: the intent
compiler, capability coverage and the lifecycle view with its completion
verdict. None of it reads anything. This module is where the gateway
supplies what it reads from: which tools produce which deliverables, which
tools are registered and answering, which items a run approved have since
been superseded, and the requirement matrix.

Each reader degrades by *saying so*, never by pretending. A registry that is
not wired, a twin that is not reachable or a matrix that fails to build
comes back as a stated limit on the result, so "nothing was found" and
"nothing was looked at" never read the same.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from orchestrator.design_flow.capabilities import CapabilityReport, assess_capabilities

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.design_flows.lifecycle_service")

__all__ = [
    "capability_report",
    "deliverable_producers",
    "requirement_rows",
    "set_tool_registry",
    "stale_items_for_run",
]

#: Set at gateway start-up (``server.py``). ``None`` in a process that never
#: bootstrapped a registry, which the capability report states as a limit.
_tool_registry: Any = None


def set_tool_registry(registry: Any) -> None:
    global _tool_registry  # noqa: PLW0603 - one process-wide registry, set once at start-up
    _tool_registry = registry


def deliverable_producers() -> dict[str, frozenset[str]]:
    """Deliverable type -> the MCP tools that produce it (``mcp_core.profiles``)."""
    from mcp_core.profiles import DELIVERABLE_TOOLS

    return dict(DELIVERABLE_TOOLS)


async def _registered_and_reachable(registry: Any) -> tuple[set[str], set[str] | None]:
    """Tool ids the registry holds, and the subset whose adapter is healthy."""
    registered = {m.tool_id for m in registry.list_tools()}
    try:
        health = await registry.check_all_health()
    except Exception as exc:  # noqa: BLE001 - health is advisory; say it was not checked
        logger.info("capability_health_unavailable", error=str(exc))
        return registered, None
    healthy = {
        adapter_id
        for adapter_id, status in health.items()
        if str(getattr(status, "status", status)).lower() in {"healthy", "degraded", "ok", "up"}
    }
    reachable: set[str] = set()
    for tool_id in registered:
        adapter = registry.get_adapter_for_tool(tool_id)
        # A tool with no remote adapter (in-process twin/flow tools) is
        # reachable whenever the gateway is.
        if adapter is None or adapter in healthy or adapter not in health:
            reachable.add(tool_id)
    return registered, reachable


async def capability_report(
    phases: Sequence[Any], *, served: set[str] | None = None
) -> CapabilityReport:
    """Coverage and gaps for ``phases`` against this gateway's live tools."""
    with tracer.start_as_current_span("design_flows.capability_report") as span:
        producers = deliverable_producers()
        if _tool_registry is None:
            report = assess_capabilities(
                phases, producers=producers, registered=set(), reachable=None, served=served
            )
            span.set_attribute("capabilities.registry", "missing")
            return CapabilityReport(
                nodes=report.nodes,
                gaps=(),
                limits=(
                    "no tool registry is wired in this process, so tool coverage was not "
                    "assessed (this is not a finding that tools are missing)",
                ),
            )
        registered, reachable = await _registered_and_reachable(_tool_registry)
        report = assess_capabilities(
            phases,
            producers=producers,
            registered=registered,
            reachable=reachable,
            served=served,
        )
        span.set_attribute("capabilities.status", report.status)
        span.set_attribute("capabilities.gaps", len(report.gaps))
        logger.info(
            "design_flow_capabilities_assessed",
            status=report.status,
            gaps=len(report.gaps),
            blocking=len(report.blocking_gaps),
        )
        return report


async def stale_items_for_run(
    twin: Any, run_id: str, project_id: str | None
) -> tuple[list[str], str | None]:
    """Item keys this run approved whose head has since moved past that revision.

    Returns ``(keys, limit)``; ``limit`` says why nothing could be checked.
    A head that moved after the run approved its revision means a later
    change superseded what the run produced: that phase's result is stale.
    """
    if twin is None:
        return [], "the twin is not available, so staleness was not checked"
    try:
        from twin_core.items.service import item_history, list_items, supports_items

        if not supports_items(twin):
            return [], "this twin does not track item revisions"
        stale: list[str] = []
        for item in await list_items(twin, project_id):
            approved_here = [
                r.revision
                for r in await item_history(twin, item)
                if r.change_set == run_id and r.status in ("approved", "committed")
            ]
            if approved_here and item.head_revision > max(approved_here):
                stale.append(item.key)
        return stale, None
    except Exception as exc:  # noqa: BLE001 - reported as a limit, never swallowed
        logger.warning("run_staleness_unavailable", run_id=run_id, error=str(exc))
        return [], f"staleness could not be read ({exc})"


async def requirement_rows(
    twin: Any, project_id: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """The project's requirement matrix as plain rows, and a limit if unread."""
    if not project_id:
        return [], "the run has no project, so no requirements could be read"
    if twin is None:
        return [], "the twin is not available, so requirements were not read"
    try:
        from api_gateway.requirement_intelligence.matrix import build_requirement_matrix

        rows = await build_requirement_matrix(twin, UUID(str(project_id)))
    except Exception as exc:  # noqa: BLE001 - reported as a limit
        logger.warning("run_requirements_unavailable", project_id=project_id, error=str(exc))
        return [], f"the requirement matrix could not be built ({exc})"
    return [
        {"id": r.requirementId, "name": r.requirementName, "status": r.status} for r in rows
    ], None
