"""Questions returned inside results: Multi Round-Trip Requests (FORGE-601).

MCP 2026-07-28 forbids a server from sending its own requests to the client,
which is how this server used to ask a person to approve a held write or
decide a design-flow gate (``elicitation/create`` sent mid-call). Instead the
call returns early with ``resultType: "input_required"``: the questions in
``inputRequests`` and an opaque ``requestState``. The client asks the person,
then retries the same call with their answers in ``inputResponses`` and the
state echoed back. The retry runs the call again from the start; when it
reaches the same question, the answer is already there.

This module holds the per-request pieces:

* :class:`MrtrContext`, the answers this retry brought and the questions this
  pass could not answer, in a ContextVar for one request.
* :func:`mrtr_elicitor`, an ``Elicitor`` that answers from the retry or
  records the question. Anything that asks through an elicitor (the
  design-flow ``flow.await_gate``) works unchanged.
* :func:`mrtr_gate`, the approval gate for held writes. The ledger entry is
  opened on the first pass and carried in ``requestState``, so the retry
  closes the same entry rather than opening a second, and a decision made in
  the dashboard meanwhile wins.
* :class:`StateSigner`, which signs ``requestState``. The spec requires it
  be treated as attacker-controlled: it is HMAC-signed and bound to the
  caller, the call (method and a digest of its arguments) and an expiry.
  An approval is single use: a state whose hold was already closed through
  this path is refused, so replaying an approved state cannot run the write
  twice.

Layer-1 module: stdlib only, plus siblings in ``mcp_core``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from mcp_core.elicitation import (
    ElicitAction,
    Elicitor,
    ElicitResult,
    approval_request,
    inline_approver,
    result_from_payload,
)
from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalGateFn,
    ApprovalLedger,
    ApprovalLedgerUnavailableError,
    ApprovalOutcome,
    ApprovalResolution,
)

__all__ = [
    "MRTR_METHODS",
    "MRTR_ROUTE",
    "STATE_TTL_SECONDS",
    "InputRequiredError",
    "InvalidRequestStateError",
    "MrtrContext",
    "StateSigner",
    "call_digest",
    "current_mrtr",
    "input_required_result",
    "mrtr_elicitor",
    "mrtr_gate",
    "with_mrtr",
]

#: The only requests a server may answer with ``input_required``.
MRTR_METHODS = frozenset({"tools/call", "prompts/get", "resources/read"})

#: The ledger route for a hold answered through a returned question.
MRTR_ROUTE = "elicitation"

#: How long a returned question stays answerable. The ledger entry has its
#: own window; whichever ends first wins (the ledger records a lapse).
STATE_TTL_SECONDS = 900.0

#: Fields of a retry that are not part of the call itself.
_RETRY_FIELDS = ("inputResponses", "requestState", "_meta")


class InvalidRequestStateError(ValueError):
    """``requestState`` failed verification, or was already used."""


class InputRequiredError(Exception):
    """Raised where a question cannot be answered yet.

    The question is also recorded on the request's :class:`MrtrContext`, so
    it is not lost when something between here and the server turns the
    exception into an ordinary error (a tool adapter does).
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"input required: {key}")


@dataclass
class MrtrContext:
    """One handshake-free request that may ask, or answer, a question."""

    #: The caller the state is bound to (the verified actor, else a label).
    principal: str
    #: ``method`` and a digest of the call's own parameters.
    method: str
    digest: str
    #: Answers this retry brought, by question key.
    responses: dict[str, Any] = field(default_factory=dict)
    #: Ledger entries opened on an earlier pass, by question key.
    holds: dict[str, str] = field(default_factory=dict)
    #: Questions this pass could not answer, by question key.
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Whether the client declared form elicitation on this request.
    can_ask: bool = False
    _counter: int = 0

    def next_key(self) -> str:
        """Question keys follow the order questions are asked in.

        A retry runs the same call again, so the n-th question of the retry
        is the n-th question of the first pass.
        """
        self._counter += 1
        return f"q{self._counter}"

    def ask(self, key: str, message: str, schema: dict[str, Any]) -> InputRequiredError:
        self.pending[key] = {
            "method": "elicitation/create",
            "params": {"mode": "form", "message": message, "requestedSchema": schema},
        }
        return InputRequiredError(key)


_CURRENT: ContextVar[MrtrContext | None] = ContextVar("mcp_mrtr", default=None)


def current_mrtr() -> MrtrContext | None:
    return _CURRENT.get()


@contextmanager
def with_mrtr(ctx: MrtrContext) -> Iterator[MrtrContext]:
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT.reset(token)


def call_digest(method: str, params: dict[str, Any]) -> str:
    """A digest of the call itself, without the retry's own fields."""
    core = {k: v for k, v in params.items() if k not in _RETRY_FIELDS}
    blob = json.dumps({"method": method, "params": core}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class StateSigner:
    """Signs and checks ``requestState``; remembers which holds were used.

    The key is the operator's ``METAFORGE_MCP_STATE_KEY`` when set, so a
    question survives a restart; otherwise a random per-process key, and a
    restart makes outstanding questions unanswerable (the call is simply asked
    again). Single use is enforced here, in this process.
    """

    def __init__(self, key: bytes | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._key = key or secrets.token_bytes(32)
        self._clock = clock
        self._used: dict[str, float] = {}

    def sign(self, ctx: MrtrContext) -> str:
        payload = {
            "p": ctx.principal,
            "m": ctx.method,
            "d": ctx.digest,
            "h": ctx.holds,
            "x": self._clock() + STATE_TTL_SECONDS,
        }
        body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        mac = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{mac}"

    def verify(self, token: str, *, principal: str, method: str, digest: str) -> dict[str, str]:
        """The holds a valid state carries; raises when it is not valid."""
        try:
            body, mac = token.rsplit(".", 1)
        except (AttributeError, ValueError):
            raise InvalidRequestStateError("requestState is malformed") from None
        expected = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            raise InvalidRequestStateError("requestState failed verification")
        try:
            payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        except (ValueError, UnicodeDecodeError):
            raise InvalidRequestStateError("requestState is malformed") from None
        if payload.get("x", 0) < self._clock():
            raise InvalidRequestStateError("requestState has expired; ask again")
        if payload.get("p") != principal:
            raise InvalidRequestStateError("requestState belongs to a different caller")
        if payload.get("m") != method or payload.get("d") != digest:
            raise InvalidRequestStateError("requestState belongs to a different request")
        holds = payload.get("h") or {}
        if not isinstance(holds, dict):
            raise InvalidRequestStateError("requestState is malformed")
        return {str(k): str(v) for k, v in holds.items()}

    def consume(self, approval_id: str) -> None:
        """Mark a hold used; raises if it already was (a replay)."""
        now = self._clock()
        for done, at in list(self._used.items()):
            if at < now - STATE_TTL_SECONDS:
                del self._used[done]
        if approval_id in self._used:
            raise InvalidRequestStateError("this answer has already been used")
        self._used[approval_id] = now


def input_required_result(ctx: MrtrContext, signer: StateSigner) -> dict[str, Any]:
    """The ``InputRequiredResult`` for the questions this pass left open."""
    return {
        "resultType": "input_required",
        "inputRequests": dict(ctx.pending),
        "requestState": signer.sign(ctx),
    }


def _answer(payload: Any) -> ElicitResult:
    try:
        # An ``inputResponses`` entry is the bare ElicitResult; the shared
        # parser takes the JSON-RPC response that carried one.
        return result_from_payload({"result": payload})
    except Exception:  # noqa: BLE001 - a malformed answer is no answer
        return ElicitResult(ElicitAction.CANCEL, {})


def mrtr_elicitor(ctx: MrtrContext) -> Elicitor:
    """An elicitor that answers from the retry, or records the question."""

    async def ask(message: str, requested_schema: dict[str, Any]) -> ElicitResult:
        key = ctx.next_key()
        if key in ctx.responses:
            return _answer(ctx.responses[key])
        raise ctx.ask(key, message, requested_schema)

    return ask


def _outcome(result: ElicitResult) -> ApprovalOutcome:
    """Same mapping as an inline prompt (``elicitation_gate``)."""
    if result.action is ElicitAction.CANCEL:
        return ApprovalOutcome.TIMED_OUT
    if result.action is ElicitAction.DECLINE:
        return ApprovalOutcome.REJECTED
    return (
        ApprovalOutcome.APPROVED
        if result.content.get("approve") is True
        else ApprovalOutcome.REJECTED
    )


def mrtr_gate(ctx: MrtrContext, signer: StateSigner, ledger: ApprovalLedger) -> ApprovalGateFn:
    """The approval gate for a held write on a handshake-free request."""

    async def gate(ask: ApprovalAsk) -> ApprovalResolution:
        key = ctx.next_key()
        approval_id = ctx.holds.get(key)
        if approval_id is None:
            # FORGE-473 still holds: the entry exists before the question.
            approval_id = await ledger.open_hold(ask, route=MRTR_ROUTE)
            ctx.holds[key] = approval_id
        if key not in ctx.responses:
            message, schema = approval_request(ask)
            raise ctx.ask(key, message, schema)
        signer.consume(approval_id)
        outcome = _outcome(_answer(ctx.responses[key]))
        answered = outcome in (ApprovalOutcome.APPROVED, ApprovalOutcome.REJECTED)
        approver = inline_approver() if answered else None
        try:
            recorded = await ledger.close_hold(
                approval_id, outcome, route=MRTR_ROUTE, approver=approver
            )
        except Exception as exc:  # noqa: BLE001 - an approval we cannot record must not run
            if outcome is ApprovalOutcome.APPROVED:
                raise ApprovalLedgerUnavailableError(
                    ask.tool_id, f"approval {approval_id} could not be closed: {exc}"
                ) from exc
            recorded = outcome
        if recorded is not outcome:
            # Decided elsewhere first (the dashboard, or the hold lapsed).
            outcome = recorded
            approver = inline_approver() if outcome is ApprovalOutcome.APPROVED else None
        return ApprovalResolution(outcome=outcome, approver=approver, approval_id=approval_id)

    return gate
