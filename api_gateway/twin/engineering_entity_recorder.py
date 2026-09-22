"""Engineering entity recorder — Intent & Requirements Harness intake into
the twin (FORGE-45, epic FORGE-35).

Builds the async ``record(...)`` callable injected into the twin MCP adapter
as ``engineering_entity_recorder`` (same seam as ``constraint_recorder``):
one call persists one :class:`~twin_core.models.engineering_entity.EngineeringEntity`
node, optionally linked to one or more parents it derives_from / satisfies /
motivates / etc. via a real graph edge (``EdgeType(relation)``), resolved
through the shared exact-name-or-UUID resolver (``_ref_resolver.py``).

FORGE-48 fix: also links the created entity into the project via
``ProjectBackend.link_work_product`` (same call every other recorder in this
package makes -- ``wp_type`` there is a plain string, not a real
``WorkProductType``, so lending it ``entity_type`` is exactly the mechanism's
existing generic contract, not a workaround). Without this, a design-flow
gate's ``required_deliverables`` check -- which reads ``project.work_products``
via ``ProjectGateEvaluator`` -- could never see a recorded ``intent``/
``stakeholder_need`` entity, so G0/G1 (``orchestrator/design_flow/spec.py``)
could never pass no matter how many were recorded.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin._ref_resolver import resolve_refs
from observability.tracing import get_tracer
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.engineering_entity_recorder")

# Mirrors EngineeringEntityType (twin_core/models/engineering_entity.py) --
# duplicated as a plain set here so this module can validate before
# constructing the Pydantic model, and raise the tool's own clear error
# message rather than a raw ValidationError.
_ENTITY_TYPES = frozenset(
    {
        "intent",
        "stakeholder_need",
        "objective",
        "assumption",
        "question",
        "risk",
        "verification_case",
        "evidence",
        "budget",
        "invariant",
    }
)

_DEFAULT_RELATION = "derives_from"


def make_engineering_entity_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that persists one EngineeringEntity."""

    async def record(
        *,
        entity_type: str,
        statement: str,
        title: str | None = None,
        extra: dict[str, Any] | None = None,
        parent_refs: list[str] | None = None,
        relation: str = _DEFAULT_RELATION,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if entity_type not in _ENTITY_TYPES:
            raise ValueError(
                f"engineering entity recorder: 'entity_type' must be one of "
                f"{sorted(_ENTITY_TYPES)}, got {entity_type!r}"
            )
        if not statement or not isinstance(statement, str):
            raise ValueError(
                "engineering entity recorder: 'statement' is required (non-empty string)"
            )
        try:
            relation_edge = EdgeType(relation)
        except ValueError as exc:
            raise ValueError(
                f"engineering entity recorder: 'relation' must be a valid EdgeType, "
                f"got {relation!r}"
            ) from exc

        with tracer.start_as_current_span("twin.record_engineering_entity") as span:
            span.set_attribute("entity.type", entity_type)
            span.set_attribute("entity.parent_count", len(parent_refs or []))

            # Resolve BEFORE constructing the node so parent_refs lands in the
            # entity's own field at creation time -- no post-create mutation
            # that could silently fail to persist depending on backend.
            resolved_parent_ids: list[UUID] = []
            if parent_refs:
                resolved_parent_ids = await resolve_refs(twin, parent_refs, project_id=project_id)

            metadata = dict(extra or {})
            if session_id:
                metadata.setdefault("session_id", session_id)

            entity = EngineeringEntity(
                entity_type=entity_type,  # type: ignore[arg-type]
                statement=statement,
                title=title,
                project_id=UUID(project_id) if project_id else None,
                parent_refs=[str(p) for p in resolved_parent_ids],
                metadata=metadata,
            )
            created = await twin.create_engineering_entity(entity)

            for parent_id in resolved_parent_ids:
                await twin.add_edge(
                    created.id,
                    parent_id,
                    relation_edge,
                    metadata={"kind": "engineering_trace"},
                )

            project_linked = False
            if project_backend is not None and project_id:
                await project_backend.link_work_product(
                    project_id, str(created.id), title or statement[:60], entity_type
                )
                project_linked = True

            logger.info(
                "engineering_entity_recorded",
                entity_type=entity_type,
                node_id=str(created.id),
                parents=len(resolved_parent_ids),
                relation=relation,
                project_id=project_id,
            )
            return {
                "node_id": str(created.id),
                "entity_type": entity_type,
                "parent_ids": [str(p) for p in resolved_parent_ids],
                "project_linked": project_linked,
            }

    return record
