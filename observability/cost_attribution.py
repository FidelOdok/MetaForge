"""LLM cost attribution per project and team for MetaForge.

Tracks per-call costs in-memory, supports filtering by project/team,
daily/weekly/monthly aggregation, and budget threshold checking.

MET-125
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Cost record model
# ---------------------------------------------------------------------------


class CostRecord(BaseModel):
    """A single LLM cost event."""

    agent_code: str
    provider: str
    model: str
    cost_usd: float
    project_id: str
    team_id: str
    timestamp: datetime

    @field_validator("cost_usd")
    @classmethod
    def _validate_cost(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"cost_usd must be non-negative, got {v}")
        return v

    @field_validator("agent_code", "provider", "model", "project_id", "team_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# Cost attribution tracker
# ---------------------------------------------------------------------------


class CostAttributionTracker:
    """In-memory tracker for LLM cost attribution."""

    def __init__(self) -> None:
        self._records: list[CostRecord] = []

    # ── Write ──────────────────────────────────────────────────────────

    def record_cost(self, record: CostRecord) -> None:
        """Store a cost record."""
        self._records.append(record)

    def clear(self) -> None:
        """Reset all stored records (useful for testing)."""
        self._records.clear()

    # ── Queries ────────────────────────────────────────────────────────

    def get_costs_by_project(
        self,
        project_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CostRecord]:
        """Return records filtered by *project_id* and optional time range."""
        return [
            r
            for r in self._records
            if r.project_id == project_id
            and (start is None or r.timestamp >= start)
            and (end is None or r.timestamp <= end)
        ]

    def get_costs_by_team(
        self,
        team_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CostRecord]:
        """Return records filtered by *team_id* and optional time range."""
        return [
            r
            for r in self._records
            if r.team_id == team_id
            and (start is None or r.timestamp >= start)
            and (end is None or r.timestamp <= end)
        ]

    # ── Aggregation ────────────────────────────────────────────────────

    def get_daily_totals(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, float]:
        """Return daily USD totals as ``{YYYY-MM-DD: total}``."""
        totals: dict[str, float] = defaultdict(float)
        for r in self._records:
            if (start is not None and r.timestamp < start) or (
                end is not None and r.timestamp > end
            ):
                continue
            key = r.timestamp.strftime("%Y-%m-%d")
            totals[key] += r.cost_usd
        return dict(totals)

    def get_weekly_totals(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, float]:
        """Return weekly USD totals as ``{YYYY-WNN: total}``."""
        totals: dict[str, float] = defaultdict(float)
        for r in self._records:
            if (start is not None and r.timestamp < start) or (
                end is not None and r.timestamp > end
            ):
                continue
            iso = r.timestamp.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            totals[key] += r.cost_usd
        return dict(totals)

    def get_monthly_totals(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, float]:
        """Return monthly USD totals as ``{YYYY-MM: total}``."""
        totals: dict[str, float] = defaultdict(float)
        for r in self._records:
            if (start is not None and r.timestamp < start) or (
                end is not None and r.timestamp > end
            ):
                continue
            key = r.timestamp.strftime("%Y-%m")
            totals[key] += r.cost_usd
        return dict(totals)

    # ── Budget checking ────────────────────────────────────────────────

    def check_budget_threshold(self, project_id: str, budget_usd: float) -> tuple[bool, float]:
        """Check whether *project_id* has exceeded *budget_usd*.

        Returns ``(exceeded, current_total)``.
        """
        current = sum(r.cost_usd for r in self._records if r.project_id == project_id)
        return (current >= budget_usd, current)
