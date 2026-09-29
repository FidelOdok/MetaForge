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

from mcp_core.annotations import annotations_for


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


@dataclass(frozen=True)
class Decision:
    """Whether a call proceeds, and the sentence a human will read."""

    tool_id: str
    requires_approval: bool
    reason: str


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


def decide(
    tool_id: str,
    *,
    caller: Caller,
    twin_mutations_enabled: bool = False,
    exempt_local_writes: bool = True,
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


@dataclass(frozen=True)
class ApprovalAsk:
    """What a reviewer needs to see to answer."""

    tool_id: str
    arguments: dict[str, Any]
    caller: Caller
    reason: str
    session_id: str | None = None


class ApprovalGate(Protocol):
    """Holds a call until a human decides. Injected by the gateway."""

    def __call__(self, ask: ApprovalAsk) -> Awaitable[ApprovalOutcome]: ...


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
    """A human said no."""

    def __init__(self, tool_id: str, outcome: ApprovalOutcome) -> None:
        self.tool_id = tool_id
        self.outcome = outcome
        detail = (
            "no one answered before the approval window closed"
            if outcome is ApprovalOutcome.TIMED_OUT
            else "a reviewer rejected it"
        )
        super().__init__(f"{tool_id} was not run: {detail}.")


ApprovalGateFn = Callable[[ApprovalAsk], Awaitable[ApprovalOutcome]]
