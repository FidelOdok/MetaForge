"""PolicyEngine (FORGE-52, spec sections 14 Harness Execution Contract step 4,
52 Policy Engine).

Default-allow: an action with no registered Policy, or one whose ``when``
doesn't match the current context, proceeds untouched -- this engine gates
specific, explicitly-declared actions, it is not a blanket allow-list for
every tool call in the harness (that would block everything Phase 1 already
built, which has no policies registered for it at all). Only an action with
at least one *applicable* Policy (action matches AND ``when`` holds) whose
``require`` conditions aren't fully met is blocked.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.policy.models import Policy, PolicyRequirementFailure, PolicyResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.policy.engine")


def _lookup(context: dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
    """Resolve a dotted path (``"gate.G2"``) against a nested dict.

    Returns ``(found, value)`` -- ``found`` is False when any segment of the
    path is missing, distinguished from a present-but-falsy value (e.g.
    ``blocking_tbd_count: 0`` is a real, matchable value, not "missing").
    """
    node: Any = context
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


class EngineeringPolicyViolation(Exception):
    """Raised by ``PolicyEngine.evaluate_preconditions`` when an action is
    blocked. Carries the full ``PolicyResult`` so a caller can inspect
    exactly which policy/key failed rather than parsing a message string."""

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        parts = []
        for f in result.failures:
            actual_display = repr(f.actual) if f.found else "<missing>"
            parts.append(f"[{f.policy_id}] {f.key}: expected {f.expected!r}, got {actual_display}")
        detail = "; ".join(parts)
        super().__init__(f"Policy blocked action {result.action!r}: {detail}")


class PolicyEngine:
    """Evaluates declarative Policy rules against an attempted action."""

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies: list[Policy] = list(policies or [])

    def register(self, policy: Policy) -> None:
        self._policies.append(policy)

    async def evaluate(
        self, action: str, actor: dict[str, Any], state: dict[str, Any]
    ) -> PolicyResult:
        """Core interface (spec section 52): never raises. The caller decides
        what an ``allowed=False`` result means -- see ``evaluate_preconditions``
        for the raise-on-block convenience wrapper the Harness Execution
        Contract's step 4 uses."""
        with tracer.start_as_current_span("twin.policy.evaluate") as span:
            span.set_attribute("policy.action", action)
            # actor is merged under a reserved "actor" key in the evaluation
            # context, taking precedence over any state-provided "actor" --
            # the caller-supplied actor is the authoritative identity, never
            # something a policy's own state dict should be able to spoof.
            context = {**state, "actor": actor}

            applicable = [
                p for p in self._policies if p.action == action and self._when_matches(p, context)
            ]
            failures: list[PolicyRequirementFailure] = []
            for policy in applicable:
                failures.extend(self._check_requirements(policy, context))

            result = PolicyResult(
                action=action,
                allowed=not failures,
                applied_policy_ids=[p.id for p in applicable],
                failures=failures,
            )
            span.set_attribute("policy.allowed", result.allowed)
            span.set_attribute("policy.applied_count", len(applicable))
            logger.info(
                "policy_evaluated",
                action=action,
                allowed=result.allowed,
                applied_policy_ids=result.applied_policy_ids,
                failure_count=len(failures),
            )
            return result

    async def evaluate_preconditions(
        self, action: str, actor: dict[str, Any], state: dict[str, Any]
    ) -> PolicyResult:
        """Harness Execution Contract step 4 (spec section 14): evaluate, then
        raise ``EngineeringPolicyViolation`` if blocked. Callers that need to
        run this before every agent/skill/tool invocation call this, not
        ``evaluate`` directly."""
        result = await self.evaluate(action, actor, state)
        if not result.allowed:
            raise EngineeringPolicyViolation(result)
        return result

    @staticmethod
    def _when_matches(policy: Policy, context: dict[str, Any]) -> bool:
        for key, expected in policy.when.items():
            found, actual = _lookup(context, key)
            if not found or actual != expected:
                return False
        return True

    @staticmethod
    def _check_requirements(
        policy: Policy, context: dict[str, Any]
    ) -> list[PolicyRequirementFailure]:
        out: list[PolicyRequirementFailure] = []
        for key, expected in policy.require.items():
            found, actual = _lookup(context, key)
            if not found or actual != expected:
                out.append(
                    PolicyRequirementFailure(
                        policy_id=policy.id, key=key, expected=expected, actual=actual, found=found
                    )
                )
        return out
