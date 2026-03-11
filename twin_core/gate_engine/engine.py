"""Gate engine — manages EVT/DVT/PVT gate definitions and transitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import UUID

import structlog

from twin_core.gate_engine.models import (
    GateDefinition,
    GatePhase,
    GateSnapshot,
    GateTransitionResult,
    ReadinessScore,
)
from twin_core.gate_engine.scoring import compute_readiness_score

logger = structlog.get_logger(__name__)

# Allowed phase transitions (sequential only)
_PHASE_ORDER = [GatePhase.EVT, GatePhase.DVT, GatePhase.PVT]


class GateEngine(ABC):
    """Abstract interface for gate readiness evaluation."""

    @abstractmethod
    async def define_gate(self, gate: GateDefinition) -> GateDefinition:
        """Register a gate definition."""
        ...

    @abstractmethod
    async def get_gate(self, gate_id: UUID) -> GateDefinition | None:
        """Retrieve a gate by ID."""
        ...

    @abstractmethod
    async def list_gates(self, phase: GatePhase | None = None) -> list[GateDefinition]:
        """List all gates, optionally filtered by phase."""
        ...

    @abstractmethod
    async def update_criterion(
        self, gate_id: UUID, criterion_name: str, score: float, evidence: list[str] | None = None
    ) -> GateDefinition:
        """Update the score for a specific criterion within a gate."""
        ...

    @abstractmethod
    async def evaluate(self, gate_id: UUID) -> ReadinessScore:
        """Evaluate the readiness of a gate."""
        ...

    @abstractmethod
    async def attempt_transition(self, gate_id: UUID) -> GateTransitionResult:
        """Attempt to transition through a gate.

        Only succeeds if the readiness score meets the threshold
        and all required criteria are satisfied.
        """
        ...

    @abstractmethod
    async def get_snapshots(self, gate_id: UUID) -> list[GateSnapshot]:
        """Retrieve historical score snapshots for a gate."""
        ...


class InMemoryGateEngine(GateEngine):
    """In-memory gate engine for development and testing."""

    def __init__(self) -> None:
        self._gates: dict[UUID, GateDefinition] = {}
        self._snapshots: dict[UUID, list[GateSnapshot]] = {}
        self._current_phase: GatePhase | None = None

    async def define_gate(self, gate: GateDefinition) -> GateDefinition:
        if gate.id in self._gates:
            raise ValueError(f"Gate {gate.id} already defined")
        self._gates[gate.id] = gate
        self._snapshots[gate.id] = []
        logger.info("Gate defined", gate_id=str(gate.id), phase=gate.phase, name=gate.name)
        return gate

    async def get_gate(self, gate_id: UUID) -> GateDefinition | None:
        return self._gates.get(gate_id)

    async def list_gates(self, phase: GatePhase | None = None) -> list[GateDefinition]:
        gates = list(self._gates.values())
        if phase is not None:
            gates = [g for g in gates if g.phase == phase]
        return gates

    async def update_criterion(
        self, gate_id: UUID, criterion_name: str, score: float, evidence: list[str] | None = None
    ) -> GateDefinition:
        gate = self._gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate {gate_id} not found")

        found = False
        for criterion in gate.criteria:
            if criterion.name == criterion_name:
                criterion.score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
                if evidence:
                    criterion.evidence.extend(evidence)
                found = True
                break

        if not found:
            raise KeyError(f"Criterion '{criterion_name}' not found in gate {gate_id}")

        return gate

    async def evaluate(self, gate_id: UUID) -> ReadinessScore:
        gate = self._gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate {gate_id} not found")

        score = compute_readiness_score(gate)

        # Record snapshot
        snapshot = GateSnapshot(
            gate_id=gate_id,
            phase=gate.phase,
            score=score,
            recorded_at=datetime.now(UTC),
        )
        self._snapshots[gate_id].append(snapshot)

        logger.info(
            "Gate evaluated",
            gate_id=str(gate_id),
            phase=gate.phase,
            score=score.weighted_score,
            passed=score.passed,
        )
        return score

    async def attempt_transition(self, gate_id: UUID) -> GateTransitionResult:
        gate = self._gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate {gate_id} not found")

        score = await self.evaluate(gate_id)

        if not score.passed:
            return GateTransitionResult(
                allowed=False,
                from_phase=self._current_phase,
                to_phase=gate.phase,
                readiness=score,
                message=f"Gate '{gate.name}' not ready: {'; '.join(score.blockers)}",
            )

        # Verify phase ordering
        if self._current_phase is not None:
            current_idx = _PHASE_ORDER.index(self._current_phase)
            target_idx = _PHASE_ORDER.index(gate.phase)
            if target_idx != current_idx + 1:
                return GateTransitionResult(
                    allowed=False,
                    from_phase=self._current_phase,
                    to_phase=gate.phase,
                    readiness=score,
                    message=(
                        f"Cannot transition from {self._current_phase} to {gate.phase}; "
                        f"phases must be sequential"
                    ),
                )

        old_phase = self._current_phase
        self._current_phase = gate.phase

        logger.info(
            "Gate transition succeeded",
            from_phase=str(old_phase),
            to_phase=gate.phase,
            gate_name=gate.name,
        )

        return GateTransitionResult(
            allowed=True,
            from_phase=old_phase,
            to_phase=gate.phase,
            readiness=score,
            message=f"Successfully transitioned to {gate.phase.value.upper()}",
        )

    async def get_snapshots(self, gate_id: UUID) -> list[GateSnapshot]:
        if gate_id not in self._snapshots:
            raise KeyError(f"Gate {gate_id} not found")
        return list(self._snapshots[gate_id])
