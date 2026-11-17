"""Approval prompts that stay open until the approval is decided.

A held call asks the person in their client for a short while and then
returns, because a client gives up on a tool call long before a person may get
round to an approval (Claude Code: 120 s). Its prompt used to be withdrawn at
that point, 20 seconds in, and from then on only the dashboard or a manual
``approvals.decide`` could decide the approval.

This keeps the prompt on the client instead. It owns every approval prompt of
a session that has outlived the call that raised it:

* a held call's own prompt, once the call has returned, and
* a prompt the server put to the person by itself about an approval this
  session had not heard of (the cases the pending-approvals notice covers).

Each one stays open until the person answers it, the approval is decided on
another surface (the dashboard, ``forge approvals``, another client), or it
expires, capped at :data:`MAX_PROMPT_SECONDS`. Decided elsewhere or expired,
the prompt is withdrawn (``notifications/cancelled``). An answer is recorded
by the caller's ``on_answer``, under the same rules as an answer given inside
the call. A client that disconnects answers ``cancel`` through its transport,
which decides nothing, and the prompt is simply gone.

Only the person's response to ``elicitation/create`` reaches ``on_answer``.
Nothing the agent sends can, so the agent still cannot decide anything here.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from mcp_core.elicitation import UNATTENDED_ANSWER_SECONDS, ElicitAction, ElicitResult
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("metaforge.mcp.standing_prompts")

__all__ = [
    "MAX_PROMPT_SECONDS",
    "POLL_SECONDS",
    "StandingPrompts",
]

#: The longest any prompt stays open, whatever the approval's own expiry.
MAX_PROMPT_SECONDS = 3600.0
#: How often an open prompt checks whether its approval was decided elsewhere.
POLL_SECONDS = 5.0
#: Sessions whose "already asked" sets are remembered.
_MAX_SESSIONS = 4096
#: Items remembered as asked, per session.
_MAX_ASKED_PER_SESSION = 1024

#: ``() -> True while the approval is still waiting for a decision``.
StillPending = Callable[[], Awaitable[bool]]
#: Records one answer the person gave; returns a short outcome word for logs.
OnAnswer = Callable[[ElicitResult], Awaitable[str]]


@dataclass
class _OpenPrompt:
    session: str
    approval_id: str
    kind: str
    question: asyncio.Task[ElicitResult]
    opened: float = field(default_factory=time.monotonic)
    watcher: asyncio.Task[None] | None = None


class StandingPrompts:
    """The approval prompts each session has open past their call."""

    def __init__(self, *, metrics: Any = None, poll_seconds: float | None = None) -> None:
        self._metrics = metrics
        #: None reads :data:`POLL_SECONDS` at each check.
        self._poll = poll_seconds
        self._open: dict[str, dict[str, _OpenPrompt]] = {}
        #: Session -> approval ids already put to its person, so nothing is
        #: asked twice in one session.
        self._asked: OrderedDict[str, set[str]] = OrderedDict()

    # -- bookkeeping ----------------------------------------------------

    def asked(self, session: str, approval_id: str) -> bool:
        """Whether this session's person was already asked about the approval."""
        return approval_id in self._asked.get(session, ())

    def mark_asked(self, session: str, approval_id: str) -> None:
        """Remember that this session's person was asked about the approval."""
        asked = self._asked.setdefault(session, set())
        self._asked.move_to_end(session)
        if len(asked) >= _MAX_ASKED_PER_SESSION:
            asked.pop()
        asked.add(approval_id)
        while len(self._asked) > _MAX_SESSIONS:
            self._asked.popitem(last=False)

    def open_count(self, session: str) -> int:
        """How many prompts this session has open past their call."""
        return len(self._open.get(session, {}))

    def is_open(self, session: str, approval_id: str) -> bool:
        return approval_id in self._open.get(session, {})

    # -- keeping one open -------------------------------------------------

    def keep_open(
        self,
        *,
        session: str,
        approval_id: str,
        kind: str,
        question: asyncio.Task[ElicitResult],
        lifetime_seconds: float,
        still_pending: StillPending,
        on_answer: OnAnswer,
        on_unattended: Callable[[], None] | None = None,
    ) -> None:
        """Keep ``question`` open until it is answered, decided elsewhere or expires.

        ``question`` is the task already waiting on the client's answer. The
        transport must have been told it may wait ``lifetime_seconds`` (see
        :func:`mcp_core.elicitation.prompt_lifetime`); this caps it at
        :data:`MAX_PROMPT_SECONDS` and withdraws it then at the latest.
        """
        self.mark_asked(session, approval_id)
        lifetime = max(0.0, min(lifetime_seconds, MAX_PROMPT_SECONDS))
        entry = _OpenPrompt(session=session, approval_id=approval_id, kind=kind, question=question)
        prompts = self._open.setdefault(session, {})
        previous = prompts.pop(approval_id, None)
        if previous is not None and not previous.question.done():
            previous.question.cancel("asked again")
        prompts[approval_id] = entry
        entry.watcher = asyncio.ensure_future(
            self._watch(entry, time.monotonic() + lifetime, still_pending, on_answer, on_unattended)
        )
        self._count(kind, "opened")
        logger.info(
            "mcp_standing_prompt_opened",
            session_id=session,
            approval_id=approval_id,
            kind=kind,
            lifetime_seconds=round(lifetime),
        )

    async def _watch(
        self,
        entry: _OpenPrompt,
        deadline: float,
        still_pending: StillPending,
        on_answer: OnAnswer,
        on_unattended: Callable[[], None] | None,
    ) -> None:
        question = entry.question
        with tracer.start_as_current_span("mcp.standing_prompt") as span:
            span.set_attribute("approval.id", entry.approval_id)
            span.set_attribute("approval.prompt_kind", entry.kind)
            outcome = "dropped"
            try:
                withdraw: str | None = None
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        withdraw = "approval expired"
                        break
                    poll = self._poll if self._poll is not None else POLL_SECONDS
                    done, _ = await asyncio.wait({question}, timeout=min(poll, left))
                    if done:
                        break
                    try:
                        pending = await still_pending()
                    except Exception as exc:  # noqa: BLE001 - cannot tell: leave it open
                        logger.debug(
                            "mcp_standing_prompt_check_failed",
                            approval_id=entry.approval_id,
                            error=str(exc) or type(exc).__name__,
                        )
                        pending = True
                    if not pending:
                        withdraw = "approval decided elsewhere"
                        break
                if withdraw is not None:
                    # The hub (or stdio) withdraws the question with this
                    # reason as the notifications/cancelled reason.
                    question.cancel(withdraw)
                    try:
                        await question
                    except asyncio.CancelledError:
                        pass
                    outcome = "expired" if withdraw == "approval expired" else "decided_elsewhere"
                    return
                try:
                    result = question.result()
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.info(
                        "mcp_standing_prompt_ended_without_answer",
                        approval_id=entry.approval_id,
                        error=str(exc) or type(exc).__name__,
                    )
                    return
                waited = time.monotonic() - entry.opened
                if result.action is ElicitAction.CANCEL:
                    # Dismissed, or the client went away: nobody decided.
                    if waited < UNATTENDED_ANSWER_SECONDS and on_unattended is not None:
                        on_unattended()
                    return
                if (
                    result.action is not ElicitAction.ACCEPT
                    and waited < UNATTENDED_ANSWER_SECONDS
                    and on_unattended is not None
                ):
                    # Refused faster than anyone could read it: the client
                    # answered for the person. Recorded as nothing.
                    on_unattended()
                    return
                try:
                    outcome = await on_answer(result)
                except Exception as exc:  # noqa: BLE001 - logged and counted, never raised
                    span.record_exception(exc)
                    logger.warning(
                        "mcp_standing_prompt_answer_not_recorded",
                        approval_id=entry.approval_id,
                        error=str(exc) or type(exc).__name__,
                    )
                    outcome = "record_failed"
            except asyncio.CancelledError:
                if not question.done():
                    question.cancel("server shutting down")
                outcome = "dropped"
                raise
            finally:
                span.set_attribute("approval.prompt_outcome", outcome)
                prompts = self._open.get(entry.session)
                if prompts is not None and prompts.get(entry.approval_id) is entry:
                    prompts.pop(entry.approval_id, None)
                    if not prompts:
                        self._open.pop(entry.session, None)
                self._count(entry.kind, outcome)
                logger.info(
                    "mcp_standing_prompt_closed",
                    session_id=entry.session,
                    approval_id=entry.approval_id,
                    kind=entry.kind,
                    outcome=outcome,
                    open_seconds=round(time.monotonic() - entry.opened, 1),
                )

    def _count(self, kind: str, outcome: str) -> None:
        record = getattr(self._metrics, "record_mcp_standing_prompt", None)
        if record is None:
            return
        try:
            record(kind, outcome)
        except Exception as exc:  # noqa: BLE001 - metrics never fail a prompt
            logger.debug("mcp_standing_prompt_metric_failed", error=str(exc))

    async def aclose(self) -> None:
        """Withdraw every open prompt (server shutdown). Decides nothing."""
        watchers = [
            p.watcher for prompts in self._open.values() for p in prompts.values() if p.watcher
        ]
        for watcher in watchers:
            watcher.cancel()
        for watcher in watchers:
            try:
                await watcher
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
