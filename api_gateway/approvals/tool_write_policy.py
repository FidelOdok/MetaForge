"""A held tool write the project's approval policy decides (FORGE-780).

The per-project approval policy (:mod:`api_gateway.approvals.policy`) already
decides design-flow gates and, under ``auto``, phase claims. Held tool writes
were left out: a project set to ``auto`` still had every concept image held
for a person. This is the rule for those writes.

* ``off`` and ``advise``: a person decides. (``advise`` has no reviewer for a
  single tool call; nothing is recorded beyond the hold.)
* ``auto_clean``: additive and illustrative writes are approved (a concept
  sketch, image or mesh, a new record beside existing ones). Anything else
  is held.
* ``auto``: any write the guardrails hold is approved, until the policy's own
  expiry, after which the policy reads as ``advise``.

In every mode:

* ``METAFORGE_AUTO_APPROVE_MAX`` is the ceiling (``effective_mode``), and the
  policy is the project's own: a call is decided only by the policy of the
  project it lands in (its ``project_id`` argument, else the session's).
* Human-authority, destructive and administration tools are never approved
  (:func:`mcp_core.guardrails.never_auto_approved`).
* Paid tools have a spend cap per project per rolling hour, and
  ``cadquery.execute_script`` an hourly cap. Over it, the call is held for a
  person, and the hold says why.
* Every approval is written to the ledger with ``decided_by`` set to
  ``policy:<mode>:<project>``, the tool and the arguments hash.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from mcp_core.guardrails import never_auto_approved, policy_may_approve
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.tool_write_policy")

__all__ = [
    "RATE_CAPS",
    "SPEND_CAPS",
    "PolicyVerdict",
    "hourly_cap",
    "tool_write_covered_by_policy",
]

#: Paid tools, the variable that sets each one's hourly cap, and its default.
SPEND_CAPS: dict[str, tuple[str, int]] = {
    "twin.render_concept_image": ("METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR", 20),
    "twin.generate_concept_mesh": ("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", 3),
    # Concept parts: each is a paid mesh provider call, so the mesh cap.
    "twin.segment_concept_mesh": ("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", 3),
    "twin.regenerate_concept_part": ("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", 3),
    "twin.complete_concept_part": ("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", 3),
    # FORGE-794: a concept sketch is one image per variant, so the image cap.
    "twin.generate_sketch": ("METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR", 20),
    # CAD design loop: one run makes up to its budget of model calls (author and
    # vision critic), so it has its own, low cap.
    "twin.design_cad_loop": ("METAFORGE_AUTO_APPROVE_CAD_LOOP_PER_HOUR", 3),
}

#: Unpaid tools with an hourly cap all the same, the variable and its default.
#: ``cadquery.execute_script`` runs agent-written code. The OS isolates it (see
#: ``tool_registry/tools/cadquery/script_runner.py``); the cap bounds how much
#: of it a policy approves unattended.
RATE_CAPS: dict[str, tuple[str, int]] = {
    "cadquery.execute_script": ("METAFORGE_AUTO_APPROVE_SCRIPT_PER_HOUR", 60),
}

#: The rolling window the spend cap counts over.
CAP_WINDOW_SECONDS = 3600.0


@dataclass(frozen=True)
class PolicyVerdict:
    """What the project's policy says about one held write."""

    approved: bool
    mode: str
    reason: str
    #: ``policy:<mode>:<project>`` when approved.
    approver: str | None = None
    #: The policy would have approved it, but the spend cap was reached.
    cap_held: bool = False


def hourly_cap(tool: str) -> int | None:
    """The per-project hourly auto-approval cap for a capped tool, else ``None``."""
    entry = SPEND_CAPS.get(tool) or RATE_CAPS.get(tool)
    if entry is None:
        return None
    env, default = entry
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("tool_write_policy_cap_invalid", variable=env, value=raw[:16])
        return default
    return max(0, value)


async def tool_write_covered_by_policy(
    tool: str,
    project: str | None,
    *,
    approved_in_window: Callable[[str, str, float], int],
) -> PolicyVerdict:
    """The project's policy's verdict on a held ``tool`` write in ``project``.

    ``approved_in_window(project, tool, seconds)`` counts this project's policy
    approvals of ``tool`` in the last ``seconds``; the ledger answers it.
    """
    from api_gateway.approvals.policy import effective_mode, get_policy_store

    with tracer.start_as_current_span("tool_write_policy.check") as span:
        span.set_attribute("tool.id", tool)
        if never_auto_approved(tool):
            return _no(span, tool, "off", "a policy never approves this tool")
        if not project:
            return _no(span, tool, "off", "the call names no project")
        span.set_attribute("project.id", project)
        policy = await get_policy_store().get(project)
        mode = effective_mode(policy)
        span.set_attribute("policy.mode", mode)
        if not policy_may_approve(tool, mode):
            return _no(span, tool, mode, f"policy '{mode}' does not approve {tool}")
        cap = hourly_cap(tool)
        if cap is not None:
            used = approved_in_window(project, tool, CAP_WINDOW_SECONDS)
            if used >= cap:
                env = (SPEND_CAPS.get(tool) or RATE_CAPS[tool])[0]
                kind = "spend cap" if tool in SPEND_CAPS else "hourly cap"
                reason = (
                    f"The project's approval policy ({mode}) would approve it, but its "
                    f"{kind} is reached:{used} {tool} call{'s' if used != 1 else ''} "
                    f"auto-approved in project {project} in the last hour (cap {cap}, "
                    f"{env}), so a person decides this one."
                )
                logger.info(
                    "tool_approval_cap_held",
                    tool=tool,
                    project_id=project,
                    mode=mode,
                    used=used,
                    cap=cap,
                )
                span.set_attribute("tool_write_policy.cap_held", True)
                return PolicyVerdict(approved=False, mode=mode, reason=reason, cap_held=True)
        approver = f"policy:{mode}:{project}"
        span.set_attribute("tool_write_policy.covered", True)
        return PolicyVerdict(
            approved=True,
            mode=mode,
            reason=f"approved by the project's approval policy ({mode})",
            approver=approver,
        )


def _no(span: object, tool: str, mode: str, why: str) -> PolicyVerdict:
    logger.info("tool_write_policy_not_covered", tool=tool, mode=mode, why=why)
    setter = getattr(span, "set_attribute", None)
    if setter is not None:
        setter("tool_write_policy.covered", False)
    return PolicyVerdict(approved=False, mode=mode, reason=why)
