"""Experience-recording Protocol for domain agents (MET-454-followup).

Domain agents emit a record at the end of every task so the memory
layer (Tier-2 episodic / Tier-3.5 consolidated) has traffic to learn
from. The agent does NOT depend on a concrete store implementation —
it knows only this narrow Protocol. The gateway constructs a concrete
recorder (backed by ``digital_twin.memory.PgVectorExperienceStore``)
and injects it; tests and air-gapped runs pass ``None`` and the agent
silently skips recording.

Layer note: ``domain_agents/CLAUDE.md`` forbids imports from
``digital_twin``, so the recorder Protocol lives here and the
concrete implementation lives in ``digital_twin.memory``. The
gateway is the only place that knows about both.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class ExperienceRecorder(Protocol):
    """Write-side surface for agent experience records.

    The agent calls :meth:`record` once at the end of each task with a
    summary of what it did. The implementation is responsible for
    embedding ``result_summary`` and persisting the row — the agent
    stays oblivious to vector dimensions, embedding models, and the
    backing store.

    Implementations MUST swallow their own errors. A failed write
    should never break agent execution; log it and move on.
    """

    async def record(
        self,
        *,
        run_id: str,
        step_id: str,
        agent_code: str,
        task_type: str,
        success: bool,
        duration_seconds: float,
        result_summary: str,
        error: str | None = None,
        project_id: UUID | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one experience record. Implementations must not raise."""
        ...
