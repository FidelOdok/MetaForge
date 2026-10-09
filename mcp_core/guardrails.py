"""Which tool calls a human has to authorise, and why (FORGE-359).

MetaForge already holds writes for approval — but only on the chat path.
``HarnessRuntime.call_tool`` pauses on ``ToolSpec.requires_approval``
(``orchestrator/harness/runtime.py``), and nothing on the MCP path consults
anything at all. So the same ``twin.commit_geometry`` is gated when a person
asks for it in the dashboard and ungated when Claude Code, Codex or ChatGPT
asks for it over MCP.

F1 is the rule that it should not matter who asked. This module is the
decision half of that: given a tool id and who is calling, say whether the
call is held. Actually holding it needs a store and a person to click a
button, which lives in the gateway layer — ``mcp_core`` stays free of
``api_gateway`` imports, the same way the twin adapter takes injected
recorders rather than importing upward.

The classification is not a second list. It reads
:mod:`mcp_core.annotations`, which already decided — deliberately, per tool,
with a safe default — whether each of the 97 tools modifies anything. A
second list would be a list that can disagree with the one the client is
shown, and the disagreement nobody notices is the one where the tool says
``readOnlyHint: true`` and the gate waves it through.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import structlog

from mcp_core.annotations import annotations_for
from mcp_core.cypher import is_read_only_cypher

logger = structlog.get_logger(__name__)


class Caller(StrEnum):
    """Where a tool call came from.

    The distinction that matters is not which product is calling but how much
    the server knows about who is behind it. A stdio connector on the
    engineer's own machine is that engineer; a token arriving over a tunnel
    is whoever holds the token.
    """

    #: stdio on the user's own machine — the engineer is present by construction.
    LOCAL = "local"
    #: An authenticated remote client (OAuth identity resolved).
    REMOTE = "remote"
    #: Reached us over a tunnel or relay, or we could not establish identity.
    UNTRUSTED = "untrusted"
    #: The design-flow worker, authenticated by a dedicated service key and
    #: bound to a running, approved run and its project (FORGE-487). Assigned
    #: per request by the HTTP transport, never as a server-wide default.
    SERVICE = "service"


@dataclass(frozen=True)
class Decision:
    """Whether a call proceeds, and the sentence a human will read."""

    tool_id: str
    requires_approval: bool
    reason: str
    #: The call may not run at all, held or not (FORGE-487). Only the service
    #: caller gets this: a hold has no person waiting in a server-driven run.
    refused: bool = False


#: Callers a deployment may exempt from holding.
#:
#: This went back and forth, so the reasoning is worth keeping. The chat path
#: already holds local writes (``HarnessRuntime`` pauses on
#: ``requires_approval`` whoever is chatting), which argues for holding local
#: MCP writes too — a stdio agent runs tool calls on its own, and being on
#: the same laptop is not the same as watching.
#:
#: But holding needs somewhere to answer. A stdio session has no approval
#: surface: no dashboard is necessarily open, and the MCP client cannot
#: render one until elicitation lands (F2, FORGE-360). Holding there with
#: nothing to hold against is not a guardrail, it is an outage — every write
#: refused, no way to allow it.
#:
#: So local is exempt *by default* and remote never is, which is also what
#: the spec asks for: requests arriving through a tunnel get read-only plus
#: approval-held writes. Once F2 gives stdio somewhere to answer, the default
#: should flip and this comment should be the thing that gets deleted.
_EXEMPTIBLE: frozenset[Caller] = frozenset({Caller.LOCAL})


#: Tools whose entire output is a record of a *human's* decision (FORGE-393).
#:
#: These are different in kind from an ordinary write. ``twin.commit_geometry``
#: writes geometry the model produced, and holding it for approval is about
#: consent. ``twin.attempt_promotion`` writes down *who authorised* a maturity
#: promotion — the human is not consenting to the write, the human **is** the
#: content. So the identity has to come from whoever answered the approval, and
#: never from an argument the model filled in.
#:
#: Two consequences, both enforced below and in the dispatcher:
#:
#: * the local-write exemption does not apply. A stdio session with no approval
#:   surface cannot promote, because there is no human in it to record. That is
#:   a refusal, not an outage: the alternative is a gate signed by nobody.
#: * an approval that arrives without an identified approver is not enough.
HUMAN_AUTHORITY_TOOLS: frozenset[str] = frozenset(
    {
        "twin.attempt_promotion",
        # FORGE-400 found these two while checking that no tool lets an agent
        # approve its own call. They had the identical FORGE-393 bug:
        # `twin.approve_design_loop` took `approved_by` as an argument and
        # `twin.approve_engineering_change` took `approver`, so the model
        # named the human whose approval was being recorded.
        #
        # FORGE-393 fixed promotion and guarded `ect.approve` against a
        # *blank* approver -- which is not the same as guarding it against a
        # supplied one, and the difference is the whole bug.
        "twin.approve_design_loop",
        "twin.approve_engineering_change",
    }
)


#: Tools that write, but write *bookkeeping about the agent's own work* —
#: not design state (FORGE-407).
#:
#: Holding these was a real outage rather than a guardrail. `session.start`
#: is how an external harness attributes its work at all (FORGE-366/G1), so
#: refusing it meant every remote plugin ran unattributed — the opposite of
#: what session capture is for. And there is nothing to protect: an agent
#: cannot damage a design by recording that it did something.
#:
#: They remain `readOnlyHint: false` in the annotations, because they do
#: write and telling a client otherwise would be a lie. The gate knows the
#: difference between "writes" and "writes something worth holding".
BOOKKEEPING: frozenset[str] = frozenset(
    {
        "session.start",
        "session.log_event",
        "session.complete",
    }
)


#: Tools that write, but whose write is itself held for a human, or is only
#: allowed by an approval a human already gave (FORGE-471).
#:
#: Holding the *call* on these asked the same person the same question twice,
#: and before anything useful could happen:
#:
#: * ``flow.propose`` writes a flow version in ``proposed`` and parks an
#:   approval for it. That approval is the review: nothing runs until a person
#:   answers it. Holding the call first meant a full proposal needed two
#:   approvals, and an intent-only call, whose answer is ``needs_input``
#:   questions and writes nothing at all (FORGE-463), timed out waiting for a
#:   person before the user could even be asked.
#: * ``flow.start_run`` starts a run on a flow version. ``POST /v1/runs``
#:   refuses any version that is not approved with 409, on every engine, so
#:   the version approval is the authorisation. A call-level hold added
#:   nothing: approving the call cannot make an unapproved version start, and
#:   for an approved one it only asked again.
#:
#: ``run.start_design_flow`` is deliberately not here. It starts a built-in
#: template, which has no per-run version approval behind it, so the call hold
#: is the only consent that run gets.
#:
#: Like ``BOOKKEEPING`` these keep ``readOnlyHint: false``: they do write.
DOWNSTREAM_APPROVED: frozenset[str] = frozenset(
    {
        "flow.propose",
        "flow.start_run",
        # FORGE-539: propose holds the patch for a person; apply is refused
        # until that person approved it. A call hold would ask twice.
        "flow.patch",
        # FORGE-582: the tool's whole job is to ask the person to decide a
        # gate, in the client's own prompt, and record their answer. Holding
        # the call first would ask that person twice for one decision. The
        # agent cannot answer the prompt, and a client that cannot show one
        # gets no decision at all.
        "flow.await_gate",
    }
)


#: Tools whose approval depends on the *call*, not the tool.
#:
#: `twin.query_cypher` is one tool that is either a read or a write depending
#: on its query. Classifying per tool meant that on a deployment with
#: `--allow-twin-mutations` — which is every deployment that can build a
#: digital thread — a plain `MATCH ... RETURN` was refused as "may overwrite
#: or remove data" (FORGE-407). The annotation still has to be per tool (the
#: MCP spec has no per-call hint), so the two deliberately disagree: the hint
#: is the cautious answer, the gate is the accurate one.
ARGUMENT_CLASSIFIED: frozenset[str] = frozenset({"twin.query_cypher"})


#: Tool families the design-flow service caller may never write through.
#: ``project.*`` creates, renames or deletes projects, ``flow.*`` and ``run.*``
#: propose flows and start runs. A phase works inside the project and run it
#: was given; reshaping either is an administrator's call (FORGE-487).
SERVICE_REFUSED_PREFIXES: tuple[str, ...] = ("project.", "flow.", "run.")


def _service_refusal(tool_id: str, *, destructive: bool) -> str | None:
    """Why the service caller may not make this write, or ``None``."""
    if tool_id.startswith(SERVICE_REFUSED_PREFIXES):
        return "administers projects, flows or runs"
    if requires_human_authority(tool_id):
        return "records a human decision, which a service cannot supply"
    if destructive:
        return "may overwrite or remove data"
    return None


def _call_is_read_only(tool_id: str, arguments: dict[str, Any] | None) -> bool:
    """Is *this call* a read, for a tool that can be either?"""
    if tool_id != "twin.query_cypher":
        return False
    if arguments is None:
        # No arguments to inspect means no evidence it is a read. Erring the
        # other way would let an unapproved write through on a call this
        # function simply could not see.
        return False
    return is_read_only_cypher(arguments.get("cypher") or arguments.get("query"))


#: Reserved argument key. The dispatcher uses it to hand a human-authority
#: tool the approver it must record.
#:
#: It is stripped from client-supplied arguments before anything else happens,
#: so a model cannot set it itself. Reserving a name only works if the name is
#: taken away first — otherwise it is a convention, and a convention is exactly
#: what the model is free to imitate.
APPROVED_BY_ARG = "__approved_by__"

#: Argument names through which a caller has historically named the deciding
#: human. Supplying one is now an error on a human-authority tool.
CALLER_SUPPLIED_APPROVER_ARGS: frozenset[str] = frozenset({"decided_by", "approver", "approved_by"})


def strip_reserved_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Remove dispatcher-reserved keys from whatever the client sent."""
    return {k: v for k, v in arguments.items() if k != APPROVED_BY_ARG}


def reject_caller_supplied_approver(tool_id: str, arguments: dict[str, Any]) -> None:
    """Refuse a call that tries to name its own approving human."""
    if not requires_human_authority(tool_id):
        return
    for field in sorted(CALLER_SUPPLIED_APPROVER_ARGS):
        if arguments.get(field) is not None:
            raise ApproverArgumentRejectedError(tool_id, field)


def requires_human_authority(tool_id: str) -> bool:
    """Does this tool record a named human's decision as its result?"""
    return tool_id in HUMAN_AUTHORITY_TOOLS


def decide(
    tool_id: str,
    *,
    caller: Caller,
    twin_mutations_enabled: bool = False,
    exempt_local_writes: bool = True,
    arguments: dict[str, Any] | None = None,
) -> Decision:
    """Say whether this call needs a human before it runs.

    ``exempt_local_writes`` skips the hold for stdio sessions on the
    engineer's own machine. On by default only because stdio has nowhere to
    answer an approval yet (see ``_EXEMPTIBLE``); set it False in any
    deployment that has a reviewer watching the dashboard. Remote callers
    are never exempt.
    """
    annotations = annotations_for(tool_id, twin_mutations_enabled=twin_mutations_enabled)

    if annotations["readOnlyHint"]:
        return Decision(tool_id, False, "reads only; nothing to authorise")

    if tool_id in ARGUMENT_CLASSIFIED and _call_is_read_only(tool_id, arguments):
        return Decision(
            tool_id,
            False,
            "this call reads only; the tool can write, this query does not",
        )

    if tool_id in BOOKKEEPING:
        return Decision(
            tool_id,
            False,
            "records the agent's own activity, not design state",
        )

    if caller is Caller.SERVICE:
        # FORGE-487. The authorisation is the approved flow version and the
        # gates, bound to a verified run before this is reached (see
        # ``UnifiedMcpServer._service_scope``), so an in-scope write is not
        # held call by call. What stays closed is the set a phase has no
        # business calling. Checked before the human-authority branch so the
        # refusal says "a service cannot", not "held for a human".
        refusal = _service_refusal(tool_id, destructive=bool(annotations["destructiveHint"]))
        if refusal is not None:
            return Decision(
                tool_id, False, f"refused for the service caller: {refusal}", refused=True
            )
        return Decision(
            tool_id,
            False,
            "design-flow service caller inside a running, approved run and its project",
        )

    if tool_id in DOWNSTREAM_APPROVED:
        return Decision(
            tool_id,
            False,
            "its write is approved on the flow version it proposes or starts, not on the call",
        )

    if requires_human_authority(tool_id):
        # Never exempt. The result of this call is a human's name; running it
        # with nobody watching would record an authority that does not exist.
        return Decision(
            tool_id,
            True,
            "records a human decision; the approver's identity comes from the "
            f"approval, not from the request ({caller.value} caller)",
        )

    if exempt_local_writes and caller in _EXEMPTIBLE:
        return Decision(
            tool_id,
            False,
            "local stdio session, and this deployment exempts local writes",
        )

    destructive = annotations["destructiveHint"]
    kind = "may overwrite or remove data" if destructive else "writes"
    return Decision(tool_id, True, f"{kind}; held for approval ({caller.value} caller)")


# ── The seam the gateway fills ────────────────────────────────────────────
#
# Parking a call until a human clicks approve needs a store, a REST surface
# and the dashboard — all gateway-layer things. `mcp_core` does not import
# upward, so the MCP server takes an injected gate, the same shape the twin
# adapter uses for its recorders.


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    #: Nobody answered inside the window. Distinct from REJECTED on purpose:
    #: "no one was looking" and "a person said no" call for different words
    #: to the agent, and conflating them teaches it to retry a refusal.
    TIMED_OUT = "timed_out"
    #: The hold was closed before anyone answered, by something other than
    #: its own window: the ledger entry was cancelled (FORGE-465). Not a
    #: refusal either; the agent should say the hold ended, not that a
    #: person said no.
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Approver:
    """The human an approval is attributable to (FORGE-393).

    Constructed only by the code that *observed* the decision — the gateway
    route that took the click, or an elicitation response. Never from a tool
    argument, because the model fills those.
    """

    #: ``<kind>:<name>`` actor string, e.g. ``user:<uuid>`` for a verified
    #: principal or ``local:dashboard`` for a click on an unauthenticated
    #: local gateway.
    actor_id: str

    #: True only when a signature was checked. A local gateway runs with
    #: ``METAFORGE_AUTH_MODE=off`` and has no identities to verify, so a real
    #: human click there is ``verified=False``. That is an honest record of
    #: what is known — unlike a model-supplied name, which records something
    #: that was never known at all.
    verified: bool = False

    #: Human-readable, for the audit line. Display only.
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_id or not self.actor_id.strip():
            raise ValueError("Approver.actor_id must be a non-empty identity")

    @property
    def label(self) -> str:
        """What gets written down as the deciding authority."""
        return self.display_name or self.actor_id


@dataclass(frozen=True)
class ApprovalResolution:
    """How an approval ended, and who ended it."""

    outcome: ApprovalOutcome

    #: Who decided. ``None`` means the gate answered without saying — which is
    #: fine for an ordinary write and disqualifying for a human-authority tool.
    approver: Approver | None = None

    #: The ledger entry this approval lives in, e.g. ``run_b42aa3ea023f46c0``
    #: (FORGE-417). Carried back to the caller so a claim about the approval
    #: can be checked against the gateway rather than taken on trust: an
    #: agent that says "this was held and approved" should be citing an id
    #: somebody can look up. ``None`` for a gate with no durable record,
    #: such as an inline elicitation answered in the client.
    approval_id: str | None = None


def resolve_approval(raw: ApprovalOutcome | ApprovalResolution) -> ApprovalResolution:
    """Normalise what a gate returned.

    Gates predating FORGE-393 return a bare :class:`ApprovalOutcome`. Those
    still work for ordinary writes; they simply carry no approver, and a
    human-authority tool then refuses rather than inventing one. Accepting the
    older shape is not a silent fallback: the missing identity is visible at
    the point it matters, and it fails there.
    """
    if isinstance(raw, ApprovalResolution):
        return raw
    return ApprovalResolution(outcome=raw)


class HumanAuthorityRequiredError(RuntimeError):
    """A tool that records a human's decision had no identified human.

    Raised instead of running the call. The stored gate would otherwise name
    whoever the model decided to name, which is the bug this class exists to
    make impossible (FORGE-393).
    """

    def __init__(self, tool_id: str, detail: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"{tool_id} records a human decision and none was established: {detail}. "
            "The approver's identity comes from whoever answers the approval, never "
            "from a tool argument."
        )


class ApproverArgumentRejectedError(ValueError):
    """The caller tried to supply the approver's identity itself.

    Ignoring it quietly would be defensible; refusing is better. A model that
    passes ``decided_by`` believes it is setting the authority, and a silent
    drop leaves it believing that.
    """

    def __init__(self, tool_id: str, field: str) -> None:
        self.tool_id = tool_id
        self.field = field
        super().__init__(
            f"{tool_id}: '{field}' cannot be supplied by the caller. The deciding "
            "human is taken from the approval record. Remove the argument and ask "
            "a person to approve the call."
        )


@dataclass(frozen=True)
class ApprovalAsk:
    """What a reviewer needs to see to answer."""

    tool_id: str
    arguments: dict[str, Any]
    caller: Caller
    reason: str
    session_id: str | None = None
    #: The project this call will actually land in, when it comes from the
    #: session scope rather than the arguments. FORGE-335 made that the
    #: normal case -- ``project.open`` sets it once and later calls carry no
    #: project at all -- so without this a reviewer approving
    #: ``twin.commit_geometry`` is told everything except which project it
    #: writes to.
    project: str | None = None
    #: How long the server wants this hold to wait (FORGE-465). The server
    #: picks it from whether the client is being sent progress, because
    #: without progress the client's own tool timeout is the real limit.
    #: ``None`` leaves the gate's own default. The gate records it on the
    #: hold, so the ledger's deadline matches what the caller was told.
    timeout_seconds: float | None = None
    #: Called once with the ledger id as soon as the hold exists, so the
    #: server can tell the client which approval it is waiting on rather
    #: than staying silent until the window closes (FORGE-465). Best-effort:
    #: a gate must not fail a hold because this raised.
    on_held: Callable[[str], Awaitable[None]] | None = None
    #: The MCP client that made the call, e.g. ``claude-code 2.1.287``
    #: (FORGE-473). Recorded on the ledger entry so a reviewer and an auditor
    #: can tell which harness asked. The model behind that client is not
    #: visible over MCP, so it is not recorded.
    client: str | None = None


def effective_hold_window(gate_timeout: float | None, ask: ApprovalAsk, default: float) -> float:
    """The window a gate waits for (FORGE-465).

    A gate built with an explicit timeout caps it (tests, and any deployment
    that pins one). Otherwise the server's choice on the ask wins, and the
    gate's default applies only when neither said.
    """
    asked = ask.timeout_seconds
    if gate_timeout is None:
        return asked if asked is not None else default
    return min(gate_timeout, asked) if asked is not None else gate_timeout


async def notify_held(ask: ApprovalAsk, approval_id: str) -> None:
    """Run ``ask.on_held`` without letting it break the hold."""
    if ask.on_held is None:
        return
    try:
        await ask.on_held(approval_id)
    except Exception as exc:  # noqa: BLE001 - a notice must not fail the hold
        logger.warning(
            "approval_hold_notice_failed",
            tool_id=ask.tool_id,
            approval_id=approval_id,
            error=str(exc) or type(exc).__name__,
        )


class ApprovalGate(Protocol):
    """Holds a call until a human decides. Injected by the gateway.

    May answer with a bare :class:`ApprovalOutcome` or, to name the deciding
    human, an :class:`ApprovalResolution`. Run the answer through
    :func:`resolve_approval` rather than branching on the type at each site.
    """

    def __call__(self, ask: ApprovalAsk) -> Awaitable[ApprovalOutcome | ApprovalResolution]: ...


class ApprovalNotConfiguredError(RuntimeError):
    """A write needed approval and there was no way to ask for one.

    The call is refused rather than run. A server told to hold writes but
    given nothing to hold them with is misconfigured, and running the write
    anyway would make the guardrail look present while doing nothing — which
    is worse than not having it, because nobody goes looking for a control
    they believe is on.
    """

    def __init__(self, tool_id: str, reason: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"{tool_id} needs approval ({reason}) but no approval gate is configured. "
            "Refusing rather than running it. Configure an approval gate, or set "
            "exempt_local_writes if this is a local stdio deployment."
        )


class ApprovalRejectedError(RuntimeError):
    """A held call ended without approval.

    The message names the outcome, the approval id and where the call was
    waiting (FORGE-465). A bare "was not run" let the agent tell the user
    MetaForge never asked; this lets it say "held for approval <id>, nobody
    approved within N s" instead.
    """

    def __init__(
        self,
        tool_id: str,
        outcome: ApprovalOutcome,
        *,
        approval_id: str | None = None,
        route: str | None = None,
        where: str | None = None,
        held_seconds: float | None = None,
        window_seconds: float | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.outcome = outcome
        self.approval_id = approval_id
        self.route = route
        self.where = where
        self.held_seconds = held_seconds
        self.window_seconds = window_seconds
        if outcome is ApprovalOutcome.TIMED_OUT:
            within = f" ({window_seconds:.0f}s)" if window_seconds else ""
            detail = f"no one answered before the approval window closed{within}"
        elif outcome is ApprovalOutcome.CANCELLED:
            detail = "the hold was cancelled before anyone answered"
        else:
            detail = "a reviewer rejected it"
        ident = f" approval {approval_id}" if approval_id else ""
        place = f" on {where}" if where else (f" via {route}" if route else "")
        held = f"held for{ident}{place}, and " if (ident or place) else ""
        super().__init__(f"{tool_id} was not run ({outcome.value}): {held}{detail}.")

    def as_data(self) -> dict[str, Any]:
        """The same facts, structured, for the JSON-RPC error's ``data``."""
        out: dict[str, Any] = {"outcome": self.outcome.value}
        if self.approval_id:
            out["approval_id"] = self.approval_id
        if self.route:
            out["route"] = self.route
        if self.where:
            out["where"] = self.where
        if self.held_seconds is not None:
            out["held_seconds"] = round(self.held_seconds, 3)
        if self.window_seconds is not None:
            out["window_seconds"] = self.window_seconds
        return out


class ApprovalLedgerUnavailableError(RuntimeError):
    """A held call could not be written to the approval ledger (FORGE-473).

    Raised, not turned into an outcome, so the write is refused with the real
    reason. An inline answer with no ledger entry is the bug this exists to
    prevent: the call would run with no approval id, no recorded approver and
    nothing for an auditor to find.
    """

    def __init__(self, tool_id: str, detail: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"{tool_id} was not run: the approval could not be recorded in the "
            f"MetaForge approval ledger ({detail}). Writes are refused while the "
            "ledger is unreachable. Check the gateway and ask for the call again."
        )


class ApprovalLedger(Protocol):
    """Where an inline (elicitation) hold is written down (FORGE-473).

    One ledger, two ways to answer: a call answered in the client's own
    prompt gets the same entry, id and approver record as one answered on the
    dashboard.
    """

    async def open_hold(self, ask: ApprovalAsk, *, route: str) -> str:
        """Create the entry before the question is asked. Returns its id.

        Raises :class:`ApprovalLedgerUnavailableError` when it cannot.
        """
        ...

    async def close_hold(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        *,
        route: str,
        approver: Approver | None,
        reason: str | None = None,
    ) -> ApprovalOutcome:
        """Resolve the entry with how the question ended.

        Returns the outcome the ledger holds, which differs from ``outcome``
        only when the entry was already decided elsewhere (a dashboard click
        that landed first), so the caller acts on what is on record.
        """
        ...


ApprovalGateFn = Callable[[ApprovalAsk], Awaitable[ApprovalOutcome | ApprovalResolution]]
