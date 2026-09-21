"""Policy/PolicyResult models (FORGE-52, spec section 52).

A ``Policy`` is deliberately simple: ``when``/``require`` are flat maps of
dotted-path -> expected-value, checked by equality only (e.g.
``{"gate.G2": "passed", "blocking_tbd_count": 0}``). This mirrors exactly
the spec's own examples and stops short of a general expression language --
``Constraint.expression`` (twin_core/constraint_engine) already owns
arbitrary boolean engineering expressions; a Policy's job is coarser-grained
gating of whether an *action* may proceed, not evaluating engineering
correctness. If richer conditions (ranges, "in", inequality) turn out to be
needed, that belongs in a follow-up, not guessed into this pass.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PolicyOnFail(BaseModel):
    """What happens when a Policy's ``require`` conditions aren't met.

    Only ``"block"`` is implemented by ``PolicyEngine`` this pass -- other
    values (e.g. a future ``"require_approval"`` routed through the HITL
    engine, FORGE-53) are the natural extension point once that engine
    exists to route to.
    """

    action: Literal["block"] = "block"


class Policy(BaseModel):
    """A declarative precondition rule for one action."""

    id: str
    action: str
    when: dict[str, Any] = Field(default_factory=dict)
    require: dict[str, Any] = Field(default_factory=dict)
    on_fail: PolicyOnFail = Field(default_factory=PolicyOnFail)


class PolicyRequirementFailure(BaseModel):
    """One unmet ``require`` condition from one applicable Policy."""

    policy_id: str
    key: str
    expected: Any
    actual: Any
    found: bool  # False when `key` wasn't present in the evaluation context at all


class PolicyResult(BaseModel):
    """Outcome of ``PolicyEngine.evaluate(action, actor, state)``."""

    action: str
    allowed: bool
    applied_policy_ids: list[str] = Field(default_factory=list)
    failures: list[PolicyRequirementFailure] = Field(default_factory=list)
