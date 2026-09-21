"""Declarative engineering policy engine (FORGE-52, Phase 2 of epic FORGE-35).

Distinct from ``orchestrator.harness.policy.Policy``/``ModelPolicy`` (an
unrelated concept: the ReAct loop's "what tool should I call next" decision
strategy). This package is the spec's section 52 Policy Engine: declarative
precondition rules gating whether an *engineering* action (recording a
requirement change, generating detailed CAD, ...) is allowed to proceed at
all, independent of what any model decided to attempt.
"""

from twin_core.policy.engine import EngineeringPolicyViolation, PolicyEngine
from twin_core.policy.models import Policy, PolicyOnFail, PolicyRequirementFailure, PolicyResult

__all__ = [
    "EngineeringPolicyViolation",
    "PolicyEngine",
    "Policy",
    "PolicyOnFail",
    "PolicyRequirementFailure",
    "PolicyResult",
]
