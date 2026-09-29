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

from dataclasses import dataclass
from enum import StrEnum

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


#: Callers exempt from holding, when a deployment opts into exempting them.
#: Nothing is exempt by default. An earlier draft of this module let LOCAL
#: through on the reasoning that the engineer is at the machine — but the
#: chat path already holds local writes today (``HarnessRuntime`` pauses on
#: ``requires_approval`` whoever is chatting), so exempting local MCP calls
#: would have made the newer, less supervised path the more permissive one.
#: A stdio agent runs tool calls on its own; being on the same laptop is not
#: the same as watching.
_EXEMPTIBLE: frozenset[Caller] = frozenset({Caller.LOCAL})


def decide(
    tool_id: str,
    *,
    caller: Caller,
    twin_mutations_enabled: bool = False,
    exempt_local_writes: bool = False,
) -> Decision:
    """Say whether this call needs a human before it runs.

    ``exempt_local_writes`` lets a deployment skip the hold for stdio
    sessions on the engineer's own machine. Opt-in, and named for exactly
    what it turns off: F1 is "regardless of which client asks", and an
    exemption that ships on by default is the rule not being there.
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
