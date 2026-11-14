"""Spend budgets for model calls: the seam (FORGE-873).

Open core records every model call (``usage.py``) but does not decide who may
spend what. A hosted deployment does: each account has a monthly budget, and a
call that would spend past it must be refused *before* it is made, not noticed
on the invoice.

So, like authentication, this is a seam. A budget implementation registers on
the ``metaforge.budget`` entry-point group and ``METAFORGE_BUDGET_MODE`` names
it. It is asked two things:

- :meth:`BudgetPolicy.check` before every model call, with the attribution of
  the call (project, subject, run, role). Raising :class:`BudgetExceeded`
  refuses the call. The provider pipeline does not retry it on another model:
  a budget is not a provider failure.
- :meth:`BudgetPolicy.record` after every call, with the recorded
  :class:`~orchestrator.harness.providers.usage.UsageEvent`, so it can keep its
  own totals (a hosted deployment keeps them in its database, shared by every
  process, rather than in the local SQLite file).

Unset, or ``off``, means no budget: a laptop is not metered. A mode that is
named but cannot be loaded stops the process, the same property the gateway
keeps for authentication: a deployment that asked for budgets must never run
without them.
"""

from __future__ import annotations

import os
import threading
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from orchestrator.harness.providers.usage import UsageContext, UsageEvent

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.harness.providers.budget")

__all__ = [
    "BUDGET_MODE_ENV",
    "ENTRY_POINT_GROUP",
    "BudgetConfigurationError",
    "BudgetExceeded",
    "BudgetPolicy",
    "BudgetReporter",
    "BudgetUnavailable",
    "budget_status",
    "active_policy",
    "check_budget",
    "configure_budget_policy",
    "record_spend",
]

#: Entry-point group a budget implementation registers on.
ENTRY_POINT_GROUP = "metaforge.budget"
#: Names the implementation; unset or ``off`` means no budget.
BUDGET_MODE_ENV = "METAFORGE_BUDGET_MODE"
_OFF = {"", "off", "none"}


class BudgetExceeded(RuntimeError):
    """The call would spend past a budget. Shown to the person as it stands."""

    def __init__(self, message: str, *, scope: str | None = None) -> None:
        super().__init__(message)
        #: What ran out, e.g. ``account`` or ``run``; for logs and metrics.
        self.scope = scope


class BudgetUnavailable(BudgetExceeded):
    """The budget could not be checked, so the call is refused.

    A subclass of :class:`BudgetExceeded` on purpose: an unreachable budget
    store must not quietly turn into unlimited spend.
    """


class BudgetConfigurationError(RuntimeError):
    """A budget mode is named but cannot be loaded or constructed."""


class BudgetPolicy(Protocol):
    """Decides whether a model call may be made, and hears what it cost."""

    #: The mode name this policy answers to.
    name: str

    async def check(self, ctx: UsageContext) -> None:
        """Raise :class:`BudgetExceeded` to refuse the call ``ctx`` describes."""
        ...

    async def record(self, event: UsageEvent) -> None:
        """Account a finished call. Must not raise; it is best-effort."""
        ...


class BudgetReporter(Protocol):
    """Optional: a policy that can say where a budget stands, for a person to see."""

    async def status(self, ctx: UsageContext) -> dict[str, object] | None:
        """The budget that applies to ``ctx`` (amounts, spend, period), or ``None``."""
        ...


_lock = threading.Lock()
_policy: BudgetPolicy | None = None
_loaded = False


def _load(mode: str) -> BudgetPolicy:
    matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == mode]
    if not matches:
        raise BudgetConfigurationError(
            f"{BUDGET_MODE_ENV}={mode!r} names no installed budget implementation "
            f"(entry-point group {ENTRY_POINT_GROUP!r}). Install it, or set {BUDGET_MODE_ENV}=off."
        )
    try:
        factory = matches[0].load()
        policy = factory()
    except Exception as exc:  # noqa: BLE001 - any failure to build it is a config error
        raise BudgetConfigurationError(f"budget mode {mode!r} failed to load: {exc}") from exc
    logger.info("llm_budget_policy_loaded", mode=mode)
    return policy  # type: ignore[no-any-return]


def active_policy() -> BudgetPolicy | None:
    """The configured policy, loaded once; ``None`` when budgets are off.

    Raises :class:`BudgetConfigurationError` when a mode is named but cannot be
    loaded. Call it at startup so that surfaces before any request.
    """
    global _policy, _loaded  # noqa: PLW0603
    with _lock:
        if not _loaded:
            mode = os.environ.get(BUDGET_MODE_ENV, "").strip().lower()
            _policy = None if mode in _OFF else _load(mode)
            _loaded = True
        return _policy


def configure_budget_policy(policy: BudgetPolicy | None) -> None:
    """Install ``policy`` directly (tests, embedders); ``None`` turns budgets off."""
    global _policy, _loaded  # noqa: PLW0603
    with _lock:
        _policy = policy
        _loaded = True


def reset_budget_policy() -> None:
    """Forget the loaded policy so the next call reads the environment again."""
    global _policy, _loaded  # noqa: PLW0603
    with _lock:
        _policy = None
        _loaded = False


async def check_budget(role: str) -> None:
    """Refuse the next model call if the current attribution is over budget.

    No-op without a policy. Any failure inside the policy other than
    :class:`BudgetExceeded` becomes :class:`BudgetUnavailable`: fail closed.
    """
    policy = active_policy()
    if policy is None:
        return
    from orchestrator.harness.providers.usage import current_usage_context

    ctx = current_usage_context()
    with tracer.start_as_current_span("llm.budget_check") as span:
        span.set_attribute("budget.policy", policy.name)
        span.set_attribute("budget.role", role)
        if ctx.project_id:
            span.set_attribute("budget.project_id", ctx.project_id)
        try:
            await policy.check(ctx)
        except BudgetExceeded as exc:
            span.set_attribute("budget.refused", True)
            logger.warning(
                "llm_budget_refused",
                policy=policy.name,
                scope=exc.scope,
                project_id=ctx.project_id,
                subject=ctx.subject,
                run_id=ctx.run_id,
                role=role,
                reason=str(exc),
            )
            raise
        except Exception as exc:
            span.record_exception(exc)
            logger.error("llm_budget_unavailable", policy=policy.name, error=str(exc))
            raise BudgetUnavailable(
                "The AI budget could not be checked, so the request was not sent. "
                "Try again shortly.",
                scope="unavailable",
            ) from exc


async def record_spend(event: UsageEvent | None) -> None:
    """Hand a recorded call to the policy. Never raises."""
    policy = active_policy() if event is not None else None
    if policy is None or event is None:
        return
    try:
        await policy.record(event)
    except Exception as exc:  # noqa: BLE001 - accounting must never break a model call
        logger.warning("llm_budget_record_failed", policy=policy.name, error=str(exc))


async def budget_status(ctx: UsageContext) -> dict[str, object]:
    """Where the budget for ``ctx`` stands, for the dashboard (FORGE-873).

    ``{"enabled": false}`` without a policy; ``{"enabled": true}`` plus
    whatever the policy reports otherwise. ``available: false`` when the
    policy could not answer, so a view can say so rather than show zero.
    """
    policy = active_policy()
    if policy is None:
        return {"enabled": False}
    report = getattr(policy, "status", None)
    if report is None:
        return {"enabled": True, "available": False}
    try:
        detail = await report(ctx)
    except Exception as exc:  # noqa: BLE001 - a view, not a gate
        logger.warning("llm_budget_status_failed", policy=policy.name, error=str(exc))
        return {"enabled": True, "available": False}
    if detail is None:
        return {"enabled": True, "available": False}
    return {"enabled": True, "available": True, **detail}
