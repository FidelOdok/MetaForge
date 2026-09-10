"""Chat-side adapter for the ``ContextAssembler`` (MET-566).

Lights up the dormant context-engineering machinery in the live chat path:
per turn, the user's message is used as a semantic query against the
knowledge service, and the assembled fragments (with ``source_id``
attribution, staleness filtering, conflict detection, and budget
enforcement) are rendered into a text block the harness places in the
model's context.

The assembler itself lives in ``digital_twin.context`` (layer 2) and knows
nothing about chat. This module owns the layer-4 glue: singleton wiring
from the gateway lifespan (mirroring ``init_twin`` / ``init_mcp_bridge``)
and best-effort semantics — context retrieval must never break a turn.
"""

from __future__ import annotations

from typing import Any

import structlog

from digital_twin.context.assembler import ContextAssembler
from digital_twin.context.models import (
    ContextAssemblyRequest,
    ContextAssemblyResponse,
    ContextScope,
)

logger = structlog.get_logger(__name__)

_assembler: ContextAssembler | None = None

# Chat turns get a slice of the window for retrieved context — enough for a
# handful of knowledge hits without crowding out history and tool schemas.
_MIN_BUDGET = 1_000
_MAX_BUDGET = 6_000


def init_context_assembler(
    twin: Any,
    knowledge_service: Any,
    collector: Any | None = None,
) -> None:
    """Wire the chat context assembler from the gateway lifespan.

    ``knowledge_service`` may be ``None`` (LightRAG not configured) — chat
    then runs without retrieved context, exactly as before MET-566.
    """
    global _assembler  # noqa: PLW0603
    if knowledge_service is None:
        _assembler = None
        logger.info("chat_context_assembler_disabled", reason="no_knowledge_service")
        return
    _assembler = ContextAssembler(twin, knowledge_service, collector=collector)
    logger.info("chat_context_assembler_initialized")


def context_token_budget(window: int) -> int:
    """Fragment budget for one turn, scaled to the model's window."""
    return min(_MAX_BUDGET, max(_MIN_BUDGET, window // 20))


async def assemble_chat_context(
    query: str,
    *,
    window: int,
    agent_id: str = "chat",
) -> ContextAssemblyResponse | None:
    """Assemble knowledge context for one chat turn. Best-effort: ``None``
    when no assembler is wired, the query is empty, or assembly fails."""
    if _assembler is None or not query.strip():
        return None
    request = ContextAssemblyRequest(
        agent_id=agent_id,
        query=query,
        scope=[ContextScope.KNOWLEDGE],
        token_budget=context_token_budget(window),
    )
    try:
        response = await _assembler.assemble(request)
    except Exception as exc:  # noqa: BLE001 — context must never break the turn
        logger.warning("chat_context_assembly_failed", error=str(exc))
        return None
    if not response.fragments:
        return None
    return response


def render_context_block(response: ContextAssemblyResponse) -> str:
    """Render assembled fragments as an attributed text block for the prompt.

    Every fragment cites its ``source_id`` so the model can attribute claims;
    blocking conflicts are surfaced as an explicit warning the model must
    act on (refuse to assert contradictory facts as settled).
    """
    lines = ["Relevant project knowledge (retrieved for this message):"]
    for f in response.fragments:
        content = " ".join(f.content.split())
        lines.append(f"- [{f.source_id}] {content}")
    if response.has_blocking_conflict:
        lines.append(
            "WARNING: the sources above contain conflicting values for the "
            "same field. Do not present either value as settled — surface "
            "the conflict to the user."
        )
    elif response.conflicts:
        lines.append(
            f"Note: {len(response.conflicts)} minor conflict(s) detected between sources above."
        )
    if response.truncated:
        lines.append(
            f"({len(response.dropped_source_ids)} lower-relevance fragment(s) "
            "were dropped to fit the context budget.)"
        )
    return "\n".join(lines)
