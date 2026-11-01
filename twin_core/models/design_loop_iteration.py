"""DesignLoopIteration -- one candidate evaluated during a closed design
loop (FORGE-287, gap G-G1, target lifecycle spec's "dual state machine:
propose -> constraint engine -> commit/reject -> next iteration").

The loop mechanism this ticket adds does not reimplement the search itself:
``api_gateway.twin.design_loop.make_design_loop_starter`` composes the
already-shipped, already-live-validated ``api_gateway.twin.optimizer.
make_wall_thickness_optimizer`` (FORGE-320) -- which already runs a real
propose -> evaluate -> revise -> repeat bisection to convergence or proven
infeasibility, with an iteration budget (``max_iterations``). What FORGE-320
never did is persist that trace as anything but one opaque Evidence
``result`` blob: the *loop itself* (a "closed loop" that produces a real,
queryable iteration timeline a caller/dashboard can list, and a real
approval gate a human can act on afterward) is this ticket's own scope.

Each optimizer candidate becomes one ``DesignLoopIteration`` node, linked
to the prior iteration via ``EdgeType.SUPERSEDES`` (the same edge FORGE-321's
revalidation flow already uses for "this is the newer replacement of that")
and to the requirement(s) it was evaluated against via
``EdgeType.CONSTRAINED_BY``. Deliberately generic field names
(``parameter_name``/``parameter_value``/``metric``/``objective_value``/
``constraints_status``), not wall-thickness-specific -- so generalising the
search itself to other parameters (FORGE-288, out of this ticket's scope)
reuses this same node type rather than a schema migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType

#: Real distinguishable outcomes the underlying bisection search
#: (``twin_core.prediction.optimizer.optimize_wall_thickness``) actually
#: produces -- "candidate" for every non-terminal step, "converged" for a
#: feasible winner (whether found via the initial bounds-check or a full
#: bisection search), "infeasible" when no value in the search range
#: satisfies both constraints. No "exhausted" status: the optimizer
#: silently accepts its best candidate within ``max_iterations`` rather
#: than distinguishing "ran out of budget" from "converged" -- inventing
#: that distinction here would claim precision the algorithm doesn't have.
DESIGN_LOOP_STATUSES = frozenset({"candidate", "converged", "infeasible"})


class DesignLoopIteration(NodeBase):
    """One candidate value evaluated during one closed design loop run."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.DESIGN_LOOP_ITERATION
    loop_id: UUID
    iteration_number: int
    work_product_id: UUID
    # The scalar parameter being searched, e.g. "wall_thickness_mm".
    parameter_name: str
    parameter_value: float
    # The objective being minimised, e.g. "mass_kg".
    metric: str
    objective_value: float
    # Per-constraint margins at this candidate, e.g.
    # {"deflection_margin_mm": 0.12, "sf_margin": 0.4} -- positive means
    # satisfied, matching twin_core.prediction.optimizer's own convention.
    constraints_status: dict[str, float] = Field(default_factory=dict)
    feasible: bool
    status: str = "candidate"
    is_winner: bool = False
    approved: bool = False
    approved_by: str | None = None
    # FORGE-507: how the approval was made, persisted with approved_by.
    approver_verified: bool = False
    approval_surface: str | None = None
    approval_on_behalf_of: str | None = None
    approval_agent: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # FORGE-291 (gap G-G5): sha256 of {work_product_id, **optimize_kwargs}
    # (sorted-key JSON, record_decision excluded), stamped on iteration 0 of
    # every loop run. Lets make_design_loop_starter detect "this exact loop
    # already ran" before spending a bisection + a batch of graph writes on
    # a re-submission -- same MET-506 content-hash precedent
    # decision_recorder.py already uses for an analogous "identical inputs
    # = the same real-world thing" problem, applied here instead of there
    # since a design loop has no single work-product node of its own to
    # hash against.
    loop_inputs_hash: str | None = None
