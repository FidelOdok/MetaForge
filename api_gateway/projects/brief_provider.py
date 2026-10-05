"""Build the ``metaforge://twin/*`` provider once, for both servers (FORGE-337).

``api_gateway/projects/brief.py`` renders the brief. This wires it to a twin
and a project backend and returns the callable the twin adapter takes as
``brief_provider``.

It exists because the gateway had this as a local closure and the standalone
MCP sidecar had nothing — so ``resources/list`` on the sidecar was **empty**,
while ``/metaforge:use``, ``/metaforge:status`` and ``/metaforge:new`` all
tell the agent to read ``metaforge://twin/brief/<project_id>``. The
registration is conditional on a provider being injected, so the absence was
silent: the resources did not fail, they were never there, and an agent
following its own instructions got "unknown resource" and carried on
unbriefed.

The twin adapter still takes it by injection rather than importing it:
``tool_registry`` does not import ``api_gateway``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

BriefProvider = Callable[[str, str], Awaitable[str | None]]


def make_brief_provider(twin: Any, project_backend: Any) -> BriefProvider:
    """Render one of the project resources as markdown, or None.

    ``None`` means "no such project", which the adapter turns into a
    resource-not-found. A project that exists and is empty has a brief —
    saying so is the point of MET-584.
    """

    async def brief_provider(kind: str, project_id: str) -> str | None:
        from api_gateway.chat.routes import _brief_doc_excerpt
        from api_gateway.projects.brief import (
            build_project_brief,
            render_entities,
            render_hierarchy,
            render_requirements,
        )

        project = await project_backend.get_project(project_id)
        if project is None:
            return None

        if kind == "brief":
            return await build_project_brief(
                project, doc_excerpt=_brief_doc_excerpt, full=True, twin=twin
            )

        try:
            pid = UUID(str(project_id))
        except ValueError:
            return None

        if kind == "hierarchy":
            return render_hierarchy(list(await twin.list_hierarchy_nodes(project_id=pid)))
        if kind == "requirements":
            from api_gateway.requirement_intelligence.matrix import build_requirement_matrix

            return render_requirements(await build_requirement_matrix(twin, pid))
        if kind == "decisions":
            from api_gateway.projects.baseline_brief import is_phase_summary

            # Decisions are work products of type design_decision, not
            # EngineeringEntity rows — list_engineering_entities does not
            # accept that type and would have returned an empty list without
            # complaining, which reads as "no decisions recorded".
            decisions = [
                wp
                for wp in project.work_products
                if str(getattr(wp.type, "value", wp.type)) == "design_decision"
                # FORGE-529: phase summaries are run summaries, not decisions.
                and not is_phase_summary(str(wp.name), getattr(wp, "metadata", None))
            ]
            return render_entities(decisions, kind="decision", title="Design decisions")
        if kind == "risks":
            risks = await twin.list_engineering_entities(project_id=pid, entity_type="risk")
            return render_entities(list(risks), kind="risk", title="Risks")
        return None

    return brief_provider
