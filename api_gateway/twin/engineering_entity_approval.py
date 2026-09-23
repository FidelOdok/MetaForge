"""Engineering entity approval — advancing an EngineeringEntity's authority
lifecycle for real (FORGE-73, waiver/release model, epic FORGE-35).

``AuthorityState`` (FORGE-51, spec section 25) has always had ``reviewed``/
``approved`` members, but nothing ever set them: the only code path that
advances authority at all is ``twin_core.transactions.baseline.
create_baseline``, which only ever writes ``BASELINED``. A "waiver"
(``entity_type="waiver"``) or "release_approval" entity is meaningless
without a real approval step distinct from creation -- a raised-but-never-
approved waiver must FAIL the G8 gate, not silently pass just because a node
exists (see ``twin_core.consistency.gates``'s G8 section). This module is
that missing approval step, generic across every ``EngineeringEntityType``
(not waiver-specific) -- the same posture ``AuthorityState`` itself was
already designed for.

Builds the async ``approve(...)`` callable injected into the twin MCP
adapter as ``engineering_entity_approver``, same seam as
``engineering_entity_recorder``. Deliberately does NOT allow advancing to
``baselined`` (exclusively ``create_baseline``'s job -- it atomically builds
a real ``Baseline`` node alongside the bump) or back to ``proposed``/on to
``verified`` (no established meaning for either transition here yet).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import AuthorityState

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.engineering_entity_approval")

_VALID_TARGET_STATES = frozenset({"reviewed", "approved"})


def make_engineering_entity_approver(twin: Any) -> Any:
    """Return an async ``approve(...)`` that advances one EngineeringEntity's
    authority state."""

    async def approve(
        *,
        entity_id: str,
        target_state: str = "approved",
        approved_by: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if target_state not in _VALID_TARGET_STATES:
            raise ValueError(
                f"twin.approve_engineering_entity: 'target_state' must be one of "
                f"{sorted(_VALID_TARGET_STATES)}, got {target_state!r} "
                "('baselined' is only ever set by a real Baseline via "
                "twin.record_decision-style transaction machinery, not this tool)"
            )

        with tracer.start_as_current_span("twin.approve_engineering_entity") as span:
            node_id = UUID(entity_id)
            span.set_attribute("entity.id", entity_id)
            span.set_attribute("entity.target_state", target_state)

            current = await twin.get_engineering_entity(node_id)
            if current is None:
                raise ValueError(f"twin.approve_engineering_entity: entity {entity_id} not found")

            updates: dict[str, Any] = {"authority": AuthorityState(target_state)}
            if approved_by:
                metadata = dict(current.metadata)
                metadata["approved_by"] = approved_by
                updates["metadata"] = metadata

            updated = await twin.update_engineering_entity(
                node_id, updates, expected_revision=expected_revision
            )

            logger.info(
                "engineering_entity_approved",
                node_id=entity_id,
                entity_type=updated.entity_type,
                target_state=target_state,
                revision=updated.revision,
            )
            return {
                "node_id": str(updated.id),
                "entity_type": updated.entity_type,
                "authority": updated.authority.value,
                "revision": updated.revision,
            }

    return approve
