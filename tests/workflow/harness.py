"""A scripted run of an accepted workflow, for the lifecycle suite (FORGE-565).

It holds only *state*: which phases finished, which gate is open and whether
it found its phase ready, which twin items have been revised since they were
approved, the requirement matrix and the capability gaps. Every judgement
(execution, eligibility, validity, objective, completion) comes from the
real ``lifecycle_view``, the same function the gateway serves on
``GET /v1/runs/{id}/lifecycle``. An event is a change to that state; the
test then asserts what the real code makes of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.design_flow.lifecycle import LifecycleView, lifecycle_view
from tests.workflow.scenarios.bracket import REQUIREMENTS, Accepted


@dataclass
class Run:
    accepted: Accepted
    status: str = "running"
    completed: list[dict[str, str]] = field(default_factory=list)
    current_phase: str | None = None
    awaiting_gate: bool = False
    gate_ready: bool = True
    stale: set[str] = field(default_factory=set)
    requirements: dict[str, str] = field(
        default_factory=lambda: {r["id"]: "no_data" for r in REQUIREMENTS}
    )
    gaps: list[dict[str, Any]] = field(default_factory=list)

    # --- events ----------------------------------------------------------

    def succeed(self, *phase_ids: str) -> Run:
        """Phases ran and their gates approved them."""
        for pid in phase_ids:
            self.completed = [c for c in self.completed if c["phase"] != pid]
            self.completed.append({"phase": pid, "status": "completed"})
        self.current_phase, self.awaiting_gate, self.gate_ready = None, False, True
        return self

    def succeed_through(self, phase_id: str) -> Run:
        ids = self.accepted.phase_ids
        return self.succeed(*ids[: ids.index(phase_id) + 1])

    def gate_not_ready(self, phase_id: str) -> Run:
        """The phase ran to the end; its gate found the objective not met."""
        self.current_phase, self.awaiting_gate, self.gate_ready = phase_id, True, False
        return self

    def rework_to(self, phase_id: str) -> Run:
        """Send the run back: the target and everything after it are redone."""
        ids = self.accepted.phase_ids
        redo = set(ids[ids.index(phase_id) :])
        self.completed = [c for c in self.completed if c["phase"] not in redo]
        self.current_phase, self.awaiting_gate, self.gate_ready = phase_id, False, True
        return self

    def revise(self, item_key: str) -> Run:
        """A newer revision of an approved item landed in the twin."""
        self.stale.add(item_key)
        return self

    def evidence(self, req_id: str, status: str) -> Run:
        self.requirements[req_id] = status
        return self

    def finish(self) -> Run:
        self.status, self.current_phase = "completed", None
        return self

    # --- the real judgement ------------------------------------------------

    def view(self) -> LifecycleView:
        return lifecycle_view(
            self.accepted.definition.phases,
            {
                "status": self.status,
                "completed": list(self.completed),
                "current_phase": self.current_phase,
                "awaiting_gate": self.awaiting_gate,
                "gate_ready": self.gate_ready,
            },
            stale_item_keys=self.stale,
            requirements=[
                {"id": rid, "status": status, "mandatory": True}
                for rid, status in self.requirements.items()
            ],
            gaps=self.gaps,
        )
