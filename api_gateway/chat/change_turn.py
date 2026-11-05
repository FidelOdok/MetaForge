"""Classify a chat turn before the agent acts on it (spec section 5).

For a person's message in a project thread (the Create conversation and the
twin agent chat both use these threads):

1. :func:`~api_gateway.twin.change_classification.classify_change` decides
   what the request would change.
2. The turn's operation gets a ``classified`` milestone: the plain line
   first, then class, confidence and routing for the Engineering detail.
3. An appearance request that names a look is recorded at once in the
   appearance record (:mod:`api_gateway.twin.appearance`), so the look
   changes without any agent tool call and without touching CAD or the
   manufacturing context.
4. The result goes into the agent's context (:func:`agent_instruction`) and
   is held for the turn, so the tool layer refuses what it forbids.

Classification is best-effort: if it fails, the turn runs as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from api_gateway.operations import publisher as operations
from api_gateway.twin.change_classification import (
    ChangeClass,
    ChangeClassification,
    agent_instruction,
    clarification_answer,
    classify_change,
    operation_message,
    strip_client_suffixes,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.change_turn")

__all__ = ["ClassifiedTurn", "classify_turn"]

STAGE_CLASSIFIED = "classified"
STAGE_APPEARANCE = "appearance_updated"


@dataclass
class ClassifiedTurn:
    """What the turn was classified as, and what was already done about it."""

    classification: ChangeClassification
    #: Set when the appearance record was updated before the agent ran.
    applied: str | None = None

    def agent_block(self) -> str:
        return agent_instruction(self.classification, applied=self.applied)

    @property
    def needs_clarification(self) -> bool:
        """The look or the material, and the person has not said which yet."""
        result = self.classification
        return (
            result.change_class is ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL
            and result.stage != "person"
        )

    def clarification(self, content: str) -> tuple[str, list[dict[str, Any]]]:
        """The question asked instead of an agent turn: its text and a choice block.

        Each option's answer carries the original request, so the next turn
        is classified as the look or the material without asking again.
        """
        question = self.classification.message
        options = [
            {
                "label": "The look",
                "value": clarification_answer("The look", "appearance", content),
                "description": "Only how it looks. The engineering model stays the same.",
            },
            {
                "label": "The material",
                "value": clarification_answer("The material", "material", content),
                "description": "What it is made from. Dependent checks are re-run.",
            },
        ]
        return question, [{"type": "choice", "prompt": question, "options": options}]


def _publish(project_id: str, op: str, stage: str, title: str, message: str) -> None:
    operations.publish(
        project_id,
        op,
        "milestone",
        "running",
        kind="chat",
        title="Reply to your message",
        domain="general",
        stage_id=stage,
        stage_title=title,
        message=message[:500],
    )


async def _component_name(twin: Any, node_id: str | None) -> str | None:
    """The selected node's name, when the twin knows it. Best-effort."""
    if not node_id or twin is None:
        return None
    try:
        wp = await twin.get_work_product(UUID(node_id))
    except Exception:  # noqa: BLE001 - no name is fine
        return None
    name = getattr(wp, "name", None) if wp is not None else None
    return str(name) if name else None


async def classify_turn(
    project_id: str,
    operation_id: str | None,
    content: str,
    *,
    twin: Any = None,
    component_id: str | None = None,
    component_label: str | None = None,
) -> ClassifiedTurn | None:
    """Classify a person's message, report it, and record a named look. Never raises."""
    with tracer.start_as_current_span("chat.classify_turn") as span:
        span.set_attribute("project_id", project_id)
        try:
            name = component_label or await _component_name(twin, component_id)
            result = await classify_change(content, component=name, component_id=component_id)
        except Exception as exc:  # noqa: BLE001 - the turn runs unclassified
            span.record_exception(exc)
            logger.warning("chat_turn_classification_failed", error=str(exc))
            return None
        span.set_attribute("change.class", result.change_class.value)
        turn = ClassifiedTurn(classification=result)
        if operation_id:
            _publish(
                project_id,
                operation_id,
                STAGE_CLASSIFIED,
                "Change classified",
                operation_message(result),
            )
        values = result.appearance
        if (
            result.change_class is ChangeClass.APPEARANCE
            and values is not None
            and not values.is_empty()
            and twin is not None
        ):
            from api_gateway.twin.appearance import set_appearance

            target = result.target_component or "the product"
            try:
                stored = await set_appearance(
                    twin,
                    project_id,
                    values,
                    component_name=result.target_component,
                    component_id=result.target_component_id,
                    source="chat",
                    request=strip_client_suffixes(content),
                )
            except Exception as exc:  # noqa: BLE001 - the agent is told nothing was recorded
                span.record_exception(exc)
                logger.warning("chat_turn_appearance_failed", error=str(exc))
            else:
                look = ", ".join(
                    v
                    for v in (
                        values.colour_name or values.colour,
                        values.finish,
                        values.render_material,
                    )
                    if v
                )
                turn.applied = (
                    f"the appearance of {target} was recorded as {look} ({stored.ref}); "
                    "the viewer shows it. Confirm it to the person in one sentence."
                )
                if operation_id:
                    _publish(
                        project_id,
                        operation_id,
                        STAGE_APPEARANCE,
                        "Appearance updated",
                        f"Appearance of {target} set to {look} ({stored.ref}). "
                        "The engineering model is unchanged.",
                    )
        return turn
