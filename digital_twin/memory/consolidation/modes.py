"""Consolidation modes — how the orchestrator behaves per invocation.

The spec calls out four modes; this module pins each one's semantics
explicitly so the workflow / scheduler layer can route by mode without
re-deriving the contract.

* ``BACKGROUND`` — the default scheduled pass (every 30 min in prod).
  Fetches the last window, synthesizes new insights, persists them.
* ``ON_DEMAND`` — manual invocation. Same path as BACKGROUND but the
  importance floor is relaxed so callers can force-process events
  that wouldn't normally qualify (e.g. retroactive triage).
* ``PROACTIVE`` — focused pass for a specific project or theme,
  triggered by an upstream signal (a new datasheet revision, a
  failed deployment, etc.). Caller must supply ``project_id``.
* ``JANITOR`` — cleanup pass. Skips synthesis entirely; re-validates
  the already-persisted insights against current thresholds and
  reports which ones no longer pass so an engineer can review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from digital_twin.memory.consolidation.themes import ConsolidationTheme


class ConsolidationMode(StrEnum):
    """How the orchestrator behaves on a given pass."""

    BACKGROUND = "background"
    ON_DEMAND = "on_demand"
    PROACTIVE = "proactive"
    JANITOR = "janitor"


# Spec: PROACTIVE bypasses the importance floor on the caller's say-so.
ON_DEMAND_IMPORTANCE_FLOOR = 0.0
"""ON_DEMAND mode runs with no importance threshold so manual triage
passes can re-process events the scheduled pass already skipped."""


class ConsolidationModeError(ValueError):
    """Raised when a mode's contract preconditions aren't met."""


@dataclass(frozen=True)
class ConsolidationRunRequest:
    """One consolidation invocation's parameters.

    Centralises the per-run knobs so callers (the Temporal workflow,
    the on-demand CLI command, etc.) hand the orchestrator a single
    object instead of a sprawling kwargs surface. Validation lives in
    ``__post_init__`` so an invalid request never reaches the
    orchestrator.
    """

    mode: ConsolidationMode = ConsolidationMode.BACKGROUND
    since: datetime | None = None
    until: datetime | None = None
    project_id: UUID | None = None
    theme: ConsolidationTheme | None = None
    min_importance: float | None = None
    fetch_limit: int | None = None

    def __post_init__(self) -> None:
        if self.mode == ConsolidationMode.PROACTIVE and self.project_id is None:
            raise ConsolidationModeError(
                "PROACTIVE mode requires a project_id — pass project_id=<UUID>"
            )
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ConsolidationModeError("ConsolidationRunRequest.since must be <= until")

    @property
    def effective_min_importance(self) -> float | None:
        """Importance floor to apply for this request, accounting for mode.

        Returns ``None`` when the caller didn't override and the mode
        also has no opinion — the orchestrator falls back to its own
        ``DEFAULT_MIN_IMPORTANCE`` in that case. ON_DEMAND deliberately
        pins to zero so the caller can re-run dropped events.
        """
        if self.min_importance is not None:
            return self.min_importance
        if self.mode == ConsolidationMode.ON_DEMAND:
            return ON_DEMAND_IMPORTANCE_FLOOR
        return None
