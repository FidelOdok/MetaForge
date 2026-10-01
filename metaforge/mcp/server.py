"""``UnifiedMcpServer`` — aggregates every adapter into one MCP process (MET-337).

Each adapter under ``tool_registry/tools/`` already extends
``McpToolServer`` with its own ``register_tool(...)`` calls and tool
handlers. This module composes them: one process holds the full set,
serves a single ``tool/list`` (across every adapter), and routes
``tool/call`` to the right handler by tool-id prefix
(``knowledge.*`` → ``KnowledgeServer``, ``cadquery.*`` →
``CadqueryServer``, etc.).

The class is transport-agnostic — feed it raw JSON-RPC text and get
back JSON-RPC text. ``__main__.py`` wraps it with a stdio reader/writer
loop or a FastAPI HTTP/SSE app.

Why we don't subclass ``McpToolServer``: that class carries a single
``adapter_id`` and version, and its ``health/check`` reports per-adapter
state. The unified server roll-up needs different shapes for both, so
composition (this class holds a list of adapter servers) is cleaner
than inheritance.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from mcp_core.annotations import annotations_for
from mcp_core.auth import UNKNOWN_AUTH, AuthPosture
from mcp_core.deeplinks import DeepLinkBuilder, links_for
from mcp_core.elicitation import ELICITATION_PROTOCOL_VERSION, Elicitor, elicitation_gate
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    ApprovalAsk,
    ApprovalGateFn,
    ApprovalNotConfiguredError,
    ApprovalOutcome,
    ApprovalRejectedError,
    Approver,
    ApproverArgumentRejectedError,
    Caller,
    HumanAuthorityRequiredError,
    decide,
    reject_caller_supplied_approver,
    requires_human_authority,
    resolve_approval,
    strip_reserved_arguments,
)
from mcp_core.profiles import PROFILES, UnknownProfileError, tools_for_profile
from mcp_core.protocol import (
    AUTH_DENIED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    RESOURCE_NOT_FOUND,
    TOOL_EXECUTION_ERROR,
)
from mcp_core.resources import ResourceUriError, parse_resource_uri
from mcp_core.workflows import WORKFLOWS, prompt_body, prompt_manifest
from metaforge.mcp.capture import SessionCapture
from observability.tracing import get_tracer
from skill_registry.geometry_stash import GeometryStash
from tool_registry.bootstrap import bootstrap_tool_registry
from tool_registry.mcp_server.handlers import (
    ResourceNotFoundError,
    ResourceReadError,
    ToolHandlerError,
    ToolNotFoundError,
    make_error,
    make_success,
)
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.registry import ToolRegistry

logger = structlog.get_logger(__name__)
tracer = get_tracer("metaforge.mcp.server")


# JSON-RPC error codes (mirrors ``tool_registry.mcp_server.server`` so
# clients see consistent codes regardless of which entry point they hit).
# FORGE-388: one error-code table, imported rather than restated. These
# used to be redefined here, and `_AUTH_DENIED = -32002` collided with
# both `mcp_core.protocol.TOOL_TIMEOUT` and the code the MCP spec assigns
# to "Resource not found". A second copy of a table is a copy that can
# disagree, and this one did.
_INVALID_REQUEST = INVALID_REQUEST
_METHOD_NOT_FOUND = METHOD_NOT_FOUND
_TOOL_EXECUTION_ERROR = TOOL_EXECUTION_ERROR
_RESOURCE_NOT_FOUND = RESOURCE_NOT_FOUND
_AUTH_DENIED = AUTH_DENIED


# MET-503: returned in the ``initialize`` handshake so any MCP client learns
# how to drive MetaForge on connect — the conventions that aren't discoverable
# from tool schemas alone. Kept self-contained (it's the only channel every
# client is guaranteed to receive; a remote/other-repo client cannot open repo
# files), with deeper docs linked by public URL rather than filesystem path.
_SERVER_INSTRUCTIONS = """\
MetaForge is a local-first control plane for hardware design. You drive these \
tools to turn engineer intent into reviewable, manufacturable deliverables \
(schematics, BOMs, CAD/FEA, design decisions) recorded in a versioned Digital Twin.

Operating rules:
- Read-only by default. Reads (twin.get_node, twin.thread_for, knowledge.search, \
*.get_properties) are safe. Writes — mutating the twin, creating work products, \
recording decisions — need explicit user intent; don't perform them unprompted.
- Each tool result is an envelope: check `status` ("ok"/"error") and read the \
`data` field for the payload. Adapter tools (cadquery, calculix, freecad, kicad) \
may return error code -32001 when their containers are down — report it, don't \
retry blindly.
- Scope work to a project: create or fetch one with project.create / project.get \
/ project.list, and pass its `project_id` when ingesting knowledge or recording \
decisions.

Your work is captured for review:
- Every tool call you make is auto-captured server-side as an action.
- BEFORE working on a project, set the active project so your actions attribute \
correctly: run `metaforge-capture use <project_id>` (CLI) or call session.start \
with the project_id. If you don't know which project, ask the user. With no \
active project, capture stays unbound.
- session.start also scopes your later tool calls to that project. It tells \
you whether that took effect: if it returns `project_scope_bound: false`, \
the scope did NOT stick and you must keep passing project_id explicitly on \
every call that takes one. Most clients get a sticky scope automatically \
(stdio, or any HTTP client echoing the Mcp-Session-Id this server issues at \
initialize); a client doing neither gets `false`.
- Record design choices with twin.record_decision (title, rationale, alternatives) \
so they persist as typed, reviewable decisions.
- Capturing your reasoning (not just actions) is an optional client-side add-on \
(Claude Code hooks or a transcript tailer): \
https://github.com/FidelOdok/MetaForge/blob/main/docs/session-capture.md

Full tool catalog and Phase-1 limits: \
https://github.com/FidelOdok/MetaForge/blob/main/docs/capability-matrix.md"""


class PromptNotFoundError(Exception):
    """``prompts/get`` named a workflow that does not exist (FORGE-340)."""


@dataclass(frozen=True)
class ApprovalRecord:
    """That a call was held, and how it ended (FORGE-417).

    A held-then-approved write returned a plain success envelope, identical
    to one that was never held. The agent read that and told the user "writes
    from this external harness are not being held for approval" -- false, and
    contradicted by the gateway ledger entry sitting right there. The
    guardrail worked; the client had no way to know, so the model filled the
    gap with a guess.

    This is what the result needs to carry for that claim to be checkable.
    """

    outcome: str
    route: str
    held_seconds: float
    approval_id: str | None = None
    approver: str | None = None
    approver_verified: bool = False

    def as_meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "held": True,
            "outcome": self.outcome,
            "route": self.route,
            "heldSeconds": round(self.held_seconds, 3),
        }
        if self.approval_id:
            out["approvalId"] = self.approval_id
        if self.approver:
            out["approvedBy"] = self.approver
            out["approverVerified"] = self.approver_verified
        return out

    def as_sentence(self) -> str:
        """One line the model will actually read.

        `_meta` is the right home for structured facts, but a model reads the
        text content. Putting it only in `_meta` is how this stayed invisible:
        the information was technically present and never looked at.
        """
        who = f" by {self.approver}" if self.approver else ""
        ident = f" (approval {self.approval_id})" if self.approval_id else ""
        unverified = "" if self.approver_verified or not self.approver else ", identity unverified"
        return (
            f"This write was held for human approval and was {self.outcome}"
            f"{who}{ident} after {self.held_seconds:.1f}s"
            f" via {self.route}{unverified}."
        )


def _tool_error_text(exc: ToolHandlerError) -> str:
    """What the model should read when a tool refuses (FORGE-419).

    The details are the actionable part and often the whole fix, so they lead.
    The tool id is named because a transcript shows several calls and an
    unattributed error is a guess about which one failed.
    """
    details = (exc.details or "").strip()
    if not details:
        # Better than an empty string, and says what to do about it rather
        # than leaving the agent to infer a cause that was never recorded.
        return (
            f"{exc.tool_id} failed, and the handler gave no reason. "
            "This is a server-side gap, not something to fix by changing the "
            "arguments -- retrying the same call will fail the same way."
        )
    return f"{exc.tool_id} failed: {details}"


class UnifiedMcpServer:
    """Holds a set of ``McpToolServer`` adapters and dispatches across them.

    Construction is decoupled from boot: build the server with already-
    initialised adapters (see ``build_unified_server``). That keeps unit
    tests fast — they can supply lightweight stub adapters without
    touching the real tool_registry bootstrap path.
    """

    def __init__(
        self,
        adapters: list[McpToolServer],
        version: str = "0.1.0",
        session_capture: SessionCapture | None = None,
        tool_registry: ToolRegistry | None = None,
        profile: str | None = None,
        reloads: bool = False,
        caller: Caller = Caller.UNTRUSTED,
        approval_gate: ApprovalGateFn | None = None,
        exempt_local_writes: bool = True,
        auth_posture: AuthPosture | None = None,
        elicitor: Elicitor | None = None,
        dashboard_url: str | None = None,
        metrics: Any = None,
    ) -> None:
        self._adapters = list(adapters)
        # FORGE-339: when set, ``tools/list`` serves only this profile's
        # tools. Validated here rather than on first request — a typo in a
        # start-up flag should stop the server, not quietly serve a profile
        # nobody asked for or, worse, serve everything.
        if profile is not None:
            tools_for_profile(profile)
        self._profile = profile
        # FORGE-359: who is on the other end, and how a write gets authorised.
        # `caller` is set per transport: stdio is the engineer at the
        # machine, anything remote is not.
        #
        # FORGE-387: the default was LOCAL and **no transport ever set it**,
        # so every HTTP caller was treated as the engineer at the keyboard
        # and `exempt_local_writes` waved their writes straight through. The
        # approval table F1 documents never fired over HTTP at all. A
        # default that is the most permissive value is how that happens
        # quietly, so the default is now the most conservative one and the
        # transports declare what they actually are.
        self._caller = caller
        self._approval_gate = approval_gate
        # FORGE-417: call id -> how its approval ended, read once by the
        # MCP envelope and popped. Bounded by that pop; see _remember_hold.
        self._approval_records: dict[str, ApprovalRecord] = {}
        self._exempt_local_writes = exempt_local_writes
        self._version = version
        # FORGE-332: what the transport in front of us enforces. Only the
        # transport knows, so it tells us; None means nobody said, and the
        # health report says exactly that rather than guessing "open".
        self._auth_posture = auth_posture
        # FORGE-360: the transport's channel back to the client, if it has
        # one. Held rather than used -- whether we may actually ask depends
        # on what the client said at initialize.
        self._elicitor = elicitor
        # FORGE-371: where the dashboard is served from, so a result can
        # carry a link to the view that shows it. None means no links at
        # all -- a guessed localhost URL is worse than none, because the
        # agent states it with the same confidence either way.
        self._deeplinks = DeepLinkBuilder(dashboard_url)
        # FORGE-379: MetricsCollector, or None. The MCP surface is where
        # every external harness meets MetaForge and it had no metrics at
        # all -- "which plugin tool is failing, for whom" was a question
        # you answered by reading Loki by hand.
        self._metrics = metrics
        # Whoever last completed the `initialize` handshake. Reported by
        # health/check so a version-skew question has an answer other than
        # "ask the user what they are running".
        self._client_info: dict[str, Any] | None = None
        self._client_protocol: str | None = None
        self._client_capabilities: dict[str, Any] = {}
        self._negotiated_protocol = self._DEFAULT_PROTOCOL_VERSION
        self._start_time = datetime.now(UTC)
        # Held only so the process shutdown path can call close_all() and
        # release remote adapters' aiohttp ClientSessions -- unused by
        # dispatch, which goes through _tool_index/_adapters below.
        self.tool_registry = tool_registry
        # MET-496: when set, every tool call is recorded into the agent
        # session store as an action/error event. None = capture off.
        self._capture = session_capture
        # Commit-by-reference stash: remembers freecad.export_model STEP output so
        # twin.commit_geometry can be called with just (session_id, obj_id).
        self._geom_stash = GeometryStash()
        # FORGE-409: health was reachable only as a JSON-RPC method, so the
        # doctor command told the agent to call something a harness cannot
        # call. This adapter exposes the same report as a `health.check` tool
        # and a `metaforge://health` resource. Appended here rather than
        # supplied by a caller because the report is about *this server* --
        # which adapters answered, what protocol was negotiated -- and no
        # caller is in a position to assemble it.
        from metaforge.mcp.health_adapter import HealthServer

        # FORGE-411: the gateway runs under uvicorn's reloader and picks the
        # mounted source up; this server calls uvicorn programmatically and
        # does not. That difference is the whole staleness question, so it is
        # recorded rather than guessed.
        self._reloads = reloads

        self._health_adapter = HealthServer(self._health_check)
        self._adapters = [*self._adapters, self._health_adapter]

        # tool_id → adapter (built once at construction; tool sets are
        # static after each adapter's ``__init__``).
        self._tool_index: dict[str, McpToolServer] = {}
        for adapter in self._adapters:
            for tool_id in adapter.tool_ids:
                if tool_id in self._tool_index:
                    raise ValueError(
                        f"Tool id collision: {tool_id!r} registered by "
                        f"both {self._tool_index[tool_id].adapter_id!r} "
                        f"and {adapter.adapter_id!r}"
                    )
                self._tool_index[tool_id] = adapter
        logger.info(
            "unified_mcp_initialised",
            adapter_count=len(self._adapters),
            tool_count=len(self._tool_index),
            adapter_ids=[a.adapter_id for a in self._adapters],
        )

    def _domain_adapters(self) -> list[McpToolServer]:
        """Every adapter except the server's own health reporter."""
        return [a for a in self._adapters if a is not getattr(self, "_health_adapter", None)]

    def attach_elicitor(self, elicitor: Elicitor) -> None:
        """Give this server a way to put a question to the connected client.

        Set by the transport for the same reason the auth posture is: only
        the transport has a channel back. A transport with no
        server-to-client direction never calls this, and ``can_elicit``
        stays False -- which is the honest state, not a degraded one.
        """
        self._elicitor = elicitor

    def declare_caller(self, caller: Caller) -> None:
        """Say who is on the other end of this transport (FORGE-387).

        Set alongside the auth posture, and for the same reason: only the
        transport knows. Not calling it leaves the conservative default,
        which holds writes rather than running them.
        """
        self._caller = caller
        logger.info("unified_mcp_caller", caller=caller.value)

    def declare_auth_posture(self, posture: AuthPosture) -> None:
        """Record what the transport in front of this server enforces.

        The transport is built after the server (both stdio and HTTP
        resolve their credentials at serve time, not at bootstrap), so
        this is a setter rather than a constructor-only argument. Calling
        it is what keeps ``health/check`` from reporting ``unknown``;
        ``tests/unit/test_mcp_health_auth.py`` asserts both entrypoints do.
        """
        self._auth_posture = posture
        logger.info(
            "unified_mcp_auth_posture",
            mode=posture.mode,
            transport=posture.transport,
            identifies_caller=posture.oauth,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def can_elicit(self) -> bool:
        """Whether this connection can put a question to the human (FORGE-360).

        Three things all have to be true, and each has bitten something:
        the transport has a way back to the client, the client said it
        supports elicitation, and the revision we negotiated is one where
        ``elicitation/create`` exists. A client that declares the capability
        while negotiating an older revision is not listening for it.
        """
        return (
            self._elicitor is not None
            and "elicitation" in self._client_capabilities
            and self._negotiated_protocol >= ELICITATION_PROTOCOL_VERSION
        )

    @property
    def adapters(self) -> list[McpToolServer]:
        return list(self._adapters)

    @property
    def tool_ids(self) -> list[str]:
        return list(self._tool_index)

    # ------------------------------------------------------------------
    # JSON-RPC entry point
    # ------------------------------------------------------------------

    async def handle_request(self, raw_message: str) -> str:
        """Parse JSON-RPC request, dispatch, return JSON-RPC response.

        Same contract as ``McpToolServer.handle_request``: pure
        text-in / text-out so transports can wrap it freely.
        """
        try:
            data: dict[str, Any] = json.loads(raw_message)
        except json.JSONDecodeError:
            return json.dumps(make_error("null", _INVALID_REQUEST, "Invalid JSON"))

        request_id: str = data.get("id", "null")
        method: str = data.get("method", "")
        params: dict[str, Any] = data.get("params", {})
        is_notification = "id" not in data

        if data.get("jsonrpc") != "2.0":
            return json.dumps(
                make_error(request_id, _INVALID_REQUEST, "Not a valid JSON-RPC 2.0 message")
            )

        # JSON-RPC notifications carry no ``id`` and MUST NOT receive a
        # response. The MCP spec sends ``notifications/initialized`` (and
        # cancellation notifications) this way; replying to them breaks
        # the client's framing. Return an empty string to signal "no
        # response" — the stdio loop only writes a line when the body
        # is non-empty.
        if is_notification:
            return ""

        with tracer.start_as_current_span("unified_mcp.handle_request") as span:
            span.set_attribute("rpc.method", method)
            try:
                # Standard MCP protocol methods (what Claude Code speaks).
                if method == "initialize":
                    result = self._initialize(params)
                elif method == "tools/list":
                    result = await self._mcp_tools_list(params)
                elif method == "tools/call":
                    result = await self._mcp_tools_call(params)
                elif method == "prompts/list":
                    result = self._prompts_list()
                elif method == "prompts/get":
                    result = self._prompts_get(params)
                elif method == "resources/list":
                    result = await self._resources_list(params)
                elif method == "resources/templates/list":
                    result = await self._resources_templates_list(params)
                elif method == "resources/read":
                    result = await self._resources_read(params)
                # Legacy MetaForge dialect (kept for backward compat with
                # internal callers and existing integration tests).
                elif method == "tool/list":
                    result = await self._tool_list(params)
                elif method == "tool/call":
                    result = await self._tool_call(params)
                elif method == "health/check":
                    result = await self._health_check()
                else:
                    return json.dumps(
                        make_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
                    )
            except ToolNotFoundError as exc:
                self._mark_failed(span, exc)
                return json.dumps(
                    make_error(
                        request_id,
                        _METHOD_NOT_FOUND,
                        str(exc),
                        # The suggestions ride in ``data`` as well as the
                        # message: a client that renders only the message
                        # still shows them, and one that parses the envelope
                        # can offer them as choices.
                        {
                            "tool_id": exc.tool_id,
                            "did_you_mean": exc.did_you_mean,
                            "tool_count": len(self._tool_index),
                        },
                    )
                )
            except PromptNotFoundError as exc:
                self._mark_failed(span, exc)
                return json.dumps(make_error(request_id, _METHOD_NOT_FOUND, str(exc)))
            except (ResourceNotFoundError, ResourceReadError) as exc:
                self._mark_failed(span, exc)
                # Same reason the approval errors are caught below: an
                # exception escaping handle_request reaches the client as a
                # dropped connection, which says nothing about what went
                # wrong. A missing resource is an ordinary answer.
                missing = isinstance(exc, ResourceNotFoundError)
                return json.dumps(
                    make_error(
                        request_id,
                        _RESOURCE_NOT_FOUND if missing else _TOOL_EXECUTION_ERROR,
                        str(exc),
                        {"uri": getattr(exc, "uri", None), "retryable": False},
                    )
                )
            except UnknownProfileError as exc:
                self._mark_failed(span, exc)
                # FORGE-410: a per-connection profile comes off the URL, so a
                # typo is a client mistake and has to come back as something
                # the client can read. Letting it escape gives a dropped
                # connection, and serving everything instead would hand a
                # client that asked for 30 tools the 108 it was avoiding.
                return json.dumps(
                    make_error(
                        request_id,
                        INVALID_PARAMS,
                        str(exc),
                        {
                            "code": "unknown_profile",
                            "available": sorted(PROFILES),
                            "retryable": False,
                        },
                    )
                )
            except (ApproverArgumentRejectedError, HumanAuthorityRequiredError) as exc:
                self._mark_failed(span, exc)
                # FORGE-393. Same reasoning as the approval errors below: the
                # agent has to be told *why*, or it will try again with the
                # same argument. These two say different things and get
                # different codes -- "you must not name the approver" is a
                # fixable mistake, "nobody approved" needs a person.
                supplied = isinstance(exc, ApproverArgumentRejectedError)
                return json.dumps(
                    make_error(
                        request_id,
                        _TOOL_EXECUTION_ERROR,
                        str(exc),
                        {
                            "tool_id": exc.tool_id,
                            "code": (
                                "approver_not_caller_supplied"
                                if supplied
                                else "human_authority_required"
                            ),
                            "field": getattr(exc, "field", None),
                            # Retrying changes nothing until the argument is
                            # dropped or a human answers.
                            "retryable": False,
                        },
                    )
                )
            except (ApprovalNotConfiguredError, ApprovalRejectedError) as exc:
                self._mark_failed(span, exc)
                # A refused write is a normal outcome, not a crash. It has to
                # reach the client as a JSON-RPC error it can read out to the
                # user -- letting it escape gives a broken connection, which
                # tells the agent nothing about why the tool did not run and
                # invites it to retry.
                # Narrowed inline: a stored isinstance result does not
                # narrow the union at the use site.
                outcome = (
                    exc.outcome.value
                    if isinstance(exc, ApprovalRejectedError)
                    else "not_configured"
                )
                return json.dumps(
                    make_error(
                        request_id,
                        _TOOL_EXECUTION_ERROR,
                        str(exc),
                        {
                            "tool_id": exc.tool_id,
                            "code": "approval_required",
                            "outcome": outcome,
                            # Nothing here is worth retrying without a human
                            # doing something first.
                            "retryable": False,
                        },
                    )
                )
            except ToolHandlerError as exc:
                self._mark_failed(span, exc)
                logger.error(
                    "unified_mcp_tool_failed",
                    tool_id=exc.tool_id,
                    duration_ms=round(exc.duration_ms, 2),
                    details=exc.details,
                )
                if method == "tools/call":
                    # FORGE-419: a tool that ran and refused is a *result*
                    # with isError, per the spec's own recommendation -- not
                    # a protocol error. The refusal usually says exactly how
                    # to fix the call (`twin.query_cypher`: "add WHERE
                    # n.project_id = $project_id and pass {...}"), and that
                    # text lived in `error.data.details`, where Claude Code
                    # shows only `error.message`: "Tool execution failed".
                    # The agent retried blind, failed the same way, gave up,
                    # and reported the tool as broken.
                    #
                    # The legacy `tool/call` dialect keeps the JSON-RPC error
                    # below: its callers are internal and already read
                    # `data.details`.
                    call_id = str(getattr(exc, "call_id", "") or "")
                    error_content: list[dict[str, Any]] = [
                        {"type": "text", "text": _tool_error_text(exc)}
                    ]
                    error_meta: dict[str, Any] = {
                        "callId": call_id,
                        "error": {
                            "toolId": exc.tool_id,
                            "details": exc.details,
                            "durationMs": round(exc.duration_ms, 2),
                        },
                    }
                    # A call can be held, approved, and *then* fail. The hold
                    # still happened, and FORGE-417's reasoning applies to an
                    # error result exactly as to a success: the agent should
                    # not have to guess whether a human saw this. Taking the
                    # record also drains it, which the success path does by
                    # popping it.
                    held = self.take_approval_record(call_id) if call_id else None
                    if held is not None:
                        error_meta["approval"] = held.as_meta()
                        error_content.append({"type": "text", "text": held.as_sentence()})
                    return json.dumps(
                        make_success(
                            request_id,
                            {
                                "content": error_content,
                                "isError": True,
                                "_meta": error_meta,
                            },
                        )
                    )
                return json.dumps(
                    make_error(
                        request_id,
                        _TOOL_EXECUTION_ERROR,
                        "Tool execution failed",
                        {
                            "error_type": "TOOL_EXECUTION_ERROR",
                            "tool_id": exc.tool_id,
                            "details": exc.details,
                            "duration_ms": exc.duration_ms,
                        },
                    )
                )

            return json.dumps(make_success(request_id, result))

    # ------------------------------------------------------------------
    # Method handlers — standard MCP protocol (Claude Code, Cursor, etc.)
    # ------------------------------------------------------------------

    #: The oldest revision we speak -- our floor, and the pre-handshake
    #: value of ``_negotiated_protocol``.
    #:
    #: FORGE-416 removed a second constant, ``_MCP_PROTOCOL_VERSION``, that
    #: held the same string beside this one and was described as "the protocol
    #: version we negotiate with". That stopped being true once
    #: ``_SUPPORTED_PROTOCOL_VERSIONS`` made this an allow-list, and the
    #: duplication was not harmless: a test asserting the server answers "our
    #: oldest" compared against a constant named "default" and passed, which
    #: is part of why the downgrade survived. The newest revision is
    #: ``max(_SUPPORTED_PROTOCOL_VERSIONS)``, never a hand-maintained copy.
    _DEFAULT_PROTOCOL_VERSION = "2024-11-05"

    #: Revisions this server will negotiate up to when a client asks for
    #: one. FORGE-360: elicitation exists only from 2025-06-18, and a
    #: server that pinned an older revision has no business sending
    #: ``elicitation/create`` -- a correct client is not listening for it.
    #: So the pin became an allow-list rather than a constant.
    #:
    #: What we implement of 2025-06-18: elicitation, ``_meta`` on results,
    #: tool annotations, and no JSON-RPC batching (which that revision
    #: removes and we never had). Structured tool output stays optional in
    #: that revision, so not emitting ``outputSchema`` is conformant.
    #: Anything outside that list is a reason not to add a revision here.
    _SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-06-18")

    @classmethod
    def _negotiate_protocol(cls, requested: str | None) -> str:
        """Which revision to answer an ``initialize`` with (FORGE-416).

        The spec: answer the requested revision when supported, otherwise
        "another protocol version it supports", which SHOULD be the latest.
        This returned ``_DEFAULT_PROTOCOL_VERSION`` -- the *oldest* -- for
        anything unsupported, so Claude Code 2.1.286 asking for 2025-11-25
        was answered 2024-11-05. Elicitation exists only from 2025-06-18, so
        ``can_elicit`` was false and every held write went to the dashboard
        queue instead of being answered inline. The feature was built
        (FORGE-360) and unreachable for the client most likely to use it.

        Three cases, because "latest supported" alone is not safe for a
        client that pinned an older revision:

        ``supported``
            echo it.
        ``newer than anything we speak``
            our newest. This is the spec's recommended answer and the case
            that was broken: a client ahead of us is asking us to do our
            best, not to fall back to our floor.
        ``older than our newest, and not one we speak``
            the newest revision we support that is **not newer than** what
            was asked for. A client that pinned 2025-03-26 gets 2024-11-05
            and keeps working; answering 2025-06-18 would invite it to
            disconnect, which the spec permits but nobody wants. This is a
            deliberate, narrow deviation from a SHOULD, and only in the
            direction of not breaking an older client.

        Revisions are ISO dates, so string ordering is version ordering.
        """
        supported = sorted(cls._SUPPORTED_PROTOCOL_VERSIONS)
        newest = supported[-1]
        if not requested:
            # No version asked for at all: nothing to be compatible with,
            # so offer the most capable thing we have.
            return newest
        if requested in cls._SUPPORTED_PROTOCOL_VERSIONS:
            return requested
        if requested > newest:
            return newest
        older = [v for v in supported if v <= requested]
        return older[-1] if older else supported[0]

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Standard MCP handshake — return server capabilities.

        Claude Code (and any spec-compliant client) sends ``initialize``
        as the first request after spawning the stdio process. The
        response advertises the protocol version we understand and the
        feature set we expose. Echoing the client's protocolVersion when
        compatible is the spec-recommended path; we pin to our known
        version to keep the contract stable across client upgrades.

        FORGE-332: ``params`` used to be discarded whole. It carries the
        only two facts about the other end this server ever learns — who
        connected and which protocol revision they asked for — and without
        them ``health/check`` could not answer a version-skew question, so
        /metaforge:doctor had to either omit it or make it up. A client
        asking for a revision we do not speak is also worth a line in the
        log: pinning is the right behaviour, pinning silently is not.
        """
        client = params.get("clientInfo")
        self._client_info = client if isinstance(client, dict) else None
        requested = params.get("protocolVersion")
        self._client_protocol = requested if isinstance(requested, str) else None
        caps = params.get("capabilities")
        self._client_capabilities = caps if isinstance(caps, dict) else {}

        # FORGE-360: meet the client on its own revision when we speak it.
        # The spec's rule is to echo the requested version if supported and
        # otherwise answer with our own; pinning unconditionally meant a
        # client asking for 2025-06-18 was told 2024-11-05 and then, quite
        # correctly, stopped expecting anything that revision added.
        self._negotiated_protocol = self._negotiate_protocol(self._client_protocol)
        if self._client_protocol and self._client_protocol not in self._SUPPORTED_PROTOCOL_VERSIONS:
            logger.warning(
                "mcp_protocol_skew",
                requested=self._client_protocol,
                negotiated=self._negotiated_protocol,
                client=(self._client_info or {}).get("name"),
            )
        logger.info(
            "mcp_initialize",
            client=(self._client_info or {}).get("name"),
            protocol=self._negotiated_protocol,
            can_elicit=self.can_elicit,
        )
        return {
            "protocolVersion": self._negotiated_protocol,
            "capabilities": {
                # ``listChanged`` would let us push notifications when
                # the tool set mutates at runtime. Our adapters are
                # static after bootstrap, so we don't advertise it.
                "tools": {},
                # FORGE-355: adapters have been registering resources since
                # MET-384 (the knowledge adapter publishes several), but the
                # unified server never routed resources/* and never said it
                # could — so a spec-compliant client had no way to reach
                # them, and no way to find out they existed.
                "resources": {},
                # FORGE-340: the curated workflows, available to every
                # spec-compliant client rather than only to harnesses with
                # their own command surface.
                "prompts": {},
            },
            "serverInfo": {
                "name": "metaforge-mcp",
                "version": self._version,
            },
            # MET-503: usage guidance every client receives on connect.
            "instructions": _SERVER_INSTRUCTIONS,
        }

    async def _mcp_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Standard MCP ``tools/list`` — wraps the legacy aggregate.

        Translation rules (legacy ``tool/list`` → MCP ``tools/list``):
        - ``tool_id`` → ``name`` (MCP uses ``name`` as the unique id)
        - ``input_schema`` → ``inputSchema`` (camelCase per MCP spec)
        - drop fields the client doesn't consume (``adapter_id``,
          ``capability``, ``output_schema``, ``phase``, ``resource_limits``)
        """
        legacy = await self._tool_list(params)
        mutations_on = self._twin_mutations_enabled()
        profile = self._requested_profile()
        allowed = set(tools_for_profile(profile)) if profile else None
        mcp_tools: list[dict[str, Any]] = []
        for entry in legacy.get("tools", []):
            tool_id = entry.get("tool_id") or entry.get("name", "")
            mcp_tool: dict[str, Any] = {
                "name": tool_id,
                "description": entry.get("description", ""),
            }
            schema = entry.get("input_schema") or entry.get("inputSchema")
            if schema:
                mcp_tool["inputSchema"] = schema
            # FORGE-343: hints the client uses to decide whether a call needs
            # a human. Unclassified tools inherit the destructive default, so
            # a new adapter is over-guarded rather than silently waved through.
            mcp_tool["annotations"] = annotations_for(
                tool_id,
                title=entry.get("name") or None,
                twin_mutations_enabled=mutations_on,
            )
            if allowed is not None and tool_id not in allowed:
                continue
            mcp_tools.append(mcp_tool)

        result: dict[str, Any] = {"tools": mcp_tools}
        # Whatever the client cannot see, it should at least be able to ask
        # about. `_meta` is the spec's own extension point and clients that
        # do not read it are no worse off than before.
        meta: dict[str, Any] = {}
        if unavailable := legacy.get("unavailable_adapters"):
            meta["unavailableAdapters"] = unavailable
        if allowed is not None:
            served = {t["name"] for t in mcp_tools}
            meta["profile"] = {
                "name": profile,
                "toolCount": len(mcp_tools),
                # A profile naming a tool no loaded adapter registers is a
                # configuration mistake, not a smaller profile. Say so.
                "missing": sorted(allowed - served),
            }
        if meta:
            result["_meta"] = meta
        return result

    def _requested_profile(self) -> str | None:
        """Which profile to serve this call, per connection (FORGE-410).

        The connection's own choice wins over the deployment's ``--profile``:
        one sidecar then serves a 30-tool set to a plugin and everything to
        the dashboard, instead of a single flag shrinking the list for every
        consumer at once. That mattered because the deployment plugins connect
        to runs with no ``--profile`` at all, so C1's cap never reached them --
        and a harness with a hard tool limit silently truncates.

        An unrecognised name raises rather than falling back to "serve
        everything". A client that asked for a 30-tool set and got 108 has
        been given the exact failure profiles exist to prevent.
        """
        try:
            from mcp_core.context import current_context

            requested = current_context().profile
        except Exception:  # noqa: BLE001 — no context is not an error here
            requested = None
        chosen = requested or self._profile
        if chosen:
            tools_for_profile(chosen)  # raises UnknownProfileError
        return chosen

    def _twin_mutations_enabled(self) -> bool:
        """Whether the twin adapter currently accepts mutating Cypher.

        Read off the adapter rather than stored here. A second copy of this
        flag would be a copy that can disagree with the one actually
        enforcing, and the direction it would disagree in is telling a
        client that a mutating tool is read-only.
        """
        adapter = self._tool_index.get("twin.query_cypher")
        return bool(getattr(adapter, "_allow_mutations", False))

    async def _mcp_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Standard MCP ``tools/call`` — wraps the legacy aggregate.

        The MCP spec uses ``{name, arguments}`` for the call shape and
        ``{content: [...], isError: bool}`` for the response. Translate
        both directions so the existing ``_tool_call`` logic (and every
        adapter handler underneath) stays untouched.
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        # FORGE-362: every call gets a reference the reply can cite. The same
        # id goes into the session record, so "I committed the geometry" can
        # be checked against something that actually happened rather than
        # taken on the model's word.
        call_id = uuid4().hex[:16]
        try:
            # Forward under ``arguments`` (not ``parameters``) — that's
            # the key ``tool_registry.mcp_server.handlers.handle_tool_call``
            # reads. Sending ``parameters`` silently dropped every arg
            # and broke every spec-compliant client (Claude Code etc.).
            # FORGE-366: `_meta` rides along. It is the spec's extension
            # point and the only place a client can tell us which model is
            # driving -- dropping it in translation meant session capture
            # could never record one.
            legacy: dict[str, Any] = {
                "tool_id": tool_name,
                "arguments": arguments,
                "_call_id": call_id,
            }
            if isinstance(params.get("_meta"), dict):
                legacy["_meta"] = params["_meta"]
            result = await self._tool_call(legacy)
        except ToolNotFoundError:
            raise
        except ToolHandlerError as exc:
            # Propagates. `handle_request` is where the span is marked and the
            # dialect is known, so FORGE-419's isError conversion happens
            # there -- returning early from here would have quietly stopped
            # `_mark_failed` running, trading one invisible failure for
            # another.
            #
            # The call id rides along because a failed call still has to be
            # citable (FORGE-362): "I tried this and it refused" is exactly
            # as worth checking as a claimed success.
            exc.call_id = call_id  # type: ignore[attr-defined]
            raise
        meta: dict[str, Any] = {"callId": call_id}
        links = links_for(self._deeplinks, tool_name, result)
        if links:
            meta["links"] = links
        # FORGE-417: say that this call was held. Without it a held-then-
        # approved write is byte-identical to one that was never held, and an
        # agent reading the result told the user writes "are not being held
        # for approval" -- false, with the ledger entry sitting right there.
        held = self.take_approval_record(call_id)
        if held is not None:
            meta["approval"] = held.as_meta()
        # Body is wrapped in MCP's ``content`` array. We use ``text``
        # type with a JSON-serialised payload — the adapter outputs are
        # structured dicts, and clients can json.parse the text. If we
        # add binary outputs later we'll branch here on result shape.
        content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(result)}]
        if held is not None:
            # A second block rather than text merged into the first: the first
            # is the tool's own output and belongs to its schema. But `_meta`
            # alone was not enough -- a model reads content, and information
            # that is technically present and never looked at is how this
            # stayed invisible in the first place.
            content.append({"type": "text", "text": held.as_sentence()})
        return {
            "content": content,
            "isError": False,
            # The reference lives in `_meta` rather than inside the text
            # payload: the text is the tool's own output and belongs to the
            # tool, and burying a protocol-level id in it would make every
            # adapter's schema wrong. Deep links ride here for the same
            # reason (FORGE-371).
            "_meta": meta,
        }

    # ------------------------------------------------------------------
    # Method handlers — legacy MetaForge dialect
    # ------------------------------------------------------------------

    async def _tool_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Aggregate ``tool/list`` across every registered adapter.

        Delegates to each adapter's ``handle_request`` so remote-adapter
        shims (which don't expose a ``_tools`` dict but DO speak JSON-RPC)
        participate alongside in-process ``McpToolServer``s — see MET-373.
        Honours the ``capability`` filter at the per-adapter layer.
        """
        sub_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unified-list",
                "method": "tool/list",
                "params": params,
            }
        )
        manifests: list[dict[str, Any]] = []
        # FORGE-339: an adapter that cannot list its tools used to vanish
        # from the aggregate -- a warning for the raising case, and nothing
        # at all for a malformed response. The client saw a shorter list with
        # no indication anything was missing, and a model reads a missing
        # tool as a capability the system does not have.
        #
        # Adapters being down is normal here (the CAD/FEA containers answer
        # -32001 routinely), so this does not fail the call. It reports the
        # gap in-band instead: never silent is not the same as always fatal.
        unavailable: list[dict[str, str]] = []
        for adapter in self._adapters:
            adapter_id = str(getattr(adapter, "adapter_id", "?"))
            try:
                sub_response_text = await adapter.handle_request(sub_request)
            except Exception as exc:
                logger.error(
                    "unified_mcp_tool_list_adapter_unavailable",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                unavailable.append({"adapter_id": adapter_id, "error": str(exc)})
                continue
            try:
                sub_response = json.loads(sub_response_text)
            except json.JSONDecodeError as exc:
                logger.error(
                    "unified_mcp_tool_list_adapter_malformed",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                unavailable.append(
                    {"adapter_id": adapter_id, "error": f"malformed tool/list response: {exc}"}
                )
                continue
            adapter_tools = sub_response.get("result", {}).get("tools", [])
            manifests.extend(adapter_tools)
        result: dict[str, Any] = {"tools": manifests}
        if unavailable:
            result["unavailable_adapters"] = unavailable
        return result

    async def _tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route + capture. Single funnel for ``tools/call`` and ``tool/call``.

        Wraps :meth:`_dispatch_tool_call` with MET-496 auto-capture: records
        an ``action`` event on success / ``error`` event on a handler failure
        into the bound agent session. Capture is best-effort and never alters
        the tool result or masks an error. ``ToolNotFoundError`` is not
        captured (no such tool ran).

        FORGE-421: this used to return early when no session capture was
        configured -- and ``_record_call`` lives *below* that return, so every
        ``metaforge_mcp_*`` metric recorded only on a deployment that happened
        to run with ``--capture-sessions``. Two unrelated concerns tangled by
        one early return, and it quietly undid FORGE-413 the same day: the
        collector was wired, the alerts still could not fire. Metrics are now
        unconditional and capture is the optional part.
        """
        tool_id = params.get("tool_id", "")
        arguments = params.get("arguments", {}) or {}
        call_id = params.get("_call_id") or uuid4().hex[:16]
        t0 = time.monotonic()
        try:
            result = await self._dispatch_tool_call(params)
        except ToolHandlerError as exc:
            elapsed = time.monotonic() - t0
            self._record_call(tool_id, "error", elapsed, exc)
            if self._capture is not None:
                await self._capture.on_tool_call(
                    tool_id,
                    arguments,
                    status="error",
                    duration_ms=elapsed * 1000,
                    error=exc.details,
                    call_id=call_id,
                    attribution=self.attribution(params),
                )
            raise
        elapsed = time.monotonic() - t0
        self._record_call(tool_id, "ok", elapsed)
        if self._capture is not None:
            await self._capture.on_tool_call(
                tool_id,
                arguments,
                status="ok",
                duration_ms=elapsed * 1000,
                result=result,
                call_id=call_id,
                attribution=self.attribution(params),
            )
        return result

    @staticmethod
    def _mark_failed(span: Any, exc: BaseException) -> None:
        """Put the failure on the trace (FORGE-379).

        There were no ``record_exception`` calls anywhere in this module,
        which CLAUDE.md asks for and which is the difference between a
        Tempo span that shows a failure and one that shows a request that
        happened to return. Also sets ``mcp.error_class``, so a trace can
        be filtered by the same taxonomy the metric counts.
        """
        try:
            span.record_exception(exc)
            span.set_attribute("mcp.error_class", error_class(exc))
        except Exception:  # noqa: BLE001 — tracing must not fail the call
            pass

    def _record_call(
        self, tool_id: str, status: str, duration: float, exc: BaseException | None = None
    ) -> None:
        """Publish one tool call to the metrics stack. Never raises.

        Telemetry that can fail a tool call is worse than no telemetry --
        the same contract session capture already keeps.
        """
        if self._metrics is None:
            return
        client = str((self._client_info or {}).get("name") or "unknown")
        try:
            self._metrics.record_mcp_tool_call(tool_id, status, duration, client)
            if exc is not None:
                self._metrics.record_mcp_error(tool_id, error_class(exc), client)
        except Exception as metric_exc:  # noqa: BLE001 — never fail a call
            logger.warning("mcp_metrics_record_failed", error=str(metric_exc))

    def attribution(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Who, with what, is doing this (FORGE-366).

        Every captured action used to be filed under ``agent_code: "mcp"``
        with nothing else. A reviewer reading /sessions could see what was
        done and not who did it, from which client, or with which model --
        which is most of the value of having the timeline at all.

        Two of the three are things the server knows for itself. The third
        is not, and is labelled accordingly:

        * ``actor`` comes from the call context. ``verified`` is true only
          when an OAuth token established it; a shared API key authorises
          without identifying, and a client-supplied header is a claim.
          FORGE-330 fixed the case where the claim outranked the token, and
          flattening the distinction here would give that back.
        * ``client`` comes from the ``initialize`` handshake.
        * ``model`` cannot be known server-side -- nothing on the wire
          carries it. A client may state it in ``_meta.model``; it is
          recorded under ``claimed`` so nobody later mistakes it for
          something the server checked.
        """
        out: dict[str, Any] = {}
        try:
            from mcp_core.context import current_context

            ctx = current_context()
            actor = ctx.actor_id
            attributable = ctx.actor_is_attributable
        except Exception:  # noqa: BLE001 — attribution must not break capture
            actor = None
            attributable = False
        if actor:
            out["actor"] = actor
            # FORGE-330: this used to be `actor != "system:unattributed"`,
            # i.e. "the field is not the default" -- so a client that set
            # X-MetaForge-Actor to anything at all was recorded as verified.
            # The flag has to mean the server established it, or an audit
            # trail cannot tell a proven identity from a typed-in one.
            out["actor_verified"] = attributable
        if self._client_info:
            name = self._client_info.get("name")
            version = self._client_info.get("version")
            client: dict[str, Any] = {}
            if isinstance(name, str):
                client["name"] = name
            if isinstance(version, str):
                client["version"] = version
            if client:
                out["client"] = client
        meta = (params or {}).get("_meta")
        model = meta.get("model") if isinstance(meta, dict) else None
        if isinstance(model, str) and model:
            out["claimed"] = {"model": model}
        return out

    def client_agent_code(self, default: str = "mcp") -> str:
        """What to file a session under. The client's name when it gave one.

        ``agent_code`` is an existing column, so this is the one part of the
        stamp that shows up in /sessions without a schema change -- and a
        list where every row says "mcp" is a list that cannot be filtered by
        who produced it.
        """
        name = (self._client_info or {}).get("name")
        return name if isinstance(name, str) and name.strip() else default

    # ── Prompts (FORGE-340) ───────────────────────────────────────────────

    def _prompts_list(self) -> dict[str, Any]:
        return {"prompts": prompt_manifest()}

    def _prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return one workflow's instructions.

        An unknown name names the ones that exist, for the same reason
        ToolNotFoundError does (FORGE-343): a bare rejection leaves the caller
        unable to tell a typo from a server that has no prompts at all.
        """
        name = params.get("name")
        try:
            body = prompt_body(str(name))
        except KeyError:
            available = ", ".join(w for w in WORKFLOWS)
            raise PromptNotFoundError(f"Unknown prompt: {name!r}. Available: {available}") from None
        return {
            "description": WORKFLOWS[str(name)][0],
            "messages": [
                {"role": "user", "content": {"type": "text", "text": body}},
            ],
        }

    # ── Resources (FORGE-355) ─────────────────────────────────────────────

    async def _resources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Aggregate ``resources/list`` across every adapter.

        Same shape as ``_tool_list``, and the same rule: an adapter that
        cannot answer is reported, never quietly dropped. A short resource
        list reads to a model as "that context does not exist", which is the
        failure this mirrors from FORGE-339.
        """
        sub_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unified-resources-list",
                "method": "resources/list",
                "params": params,
            }
        )
        resources: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for adapter in self._adapters:
            adapter_id = str(getattr(adapter, "adapter_id", "?"))
            try:
                raw = await adapter.handle_request(sub_request)
                payload = json.loads(raw)
            except Exception as exc:
                logger.error(
                    "unified_mcp_resources_list_adapter_unavailable",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                unavailable.append({"adapter_id": adapter_id, "error": str(exc)})
                continue
            resources.extend(payload.get("result", {}).get("resources", []))

        # FORGE-337: everything MetaForge publishes is parameterised by
        # project, so all of it is a *template* and belongs in
        # ``resources/templates/list``. Entries were being returned here
        # instead, with a ``uri_template`` key -- neither the method nor the
        # field name the spec defines, so a compliant client saw a list of
        # objects with no ``uri`` and nothing usable in it. Concrete
        # resources still belong here; today there are none, and an empty
        # list is the honest answer rather than a malformed full one.
        concrete = [r for r in resources if "uri" in r]

        # FORGE-408: a template stops being a template once its one parameter
        # is known. Every MetaForge resource is keyed by project, and since
        # FORGE-335 the session carries a project -- so with one open, these
        # *are* concrete and belong here.
        #
        # Leaving them out was spec-correct and useless: a client that calls
        # `resources/list` (which is most of them, and was the agent in the
        # FORGE-408 transcript) saw an empty list and concluded MetaForge
        # publishes no context at all. The brief it needed was reachable only
        # by reading `resources/templates/list` and assembling the URI by
        # hand.
        # ``params`` rather than ``{}``: a client may name the project
        # explicitly, which is the only route open to one whose session the
        # server cannot bind scope to (stdio without a session header --
        # `project.open` tells it so via `scope_bound: false`). Without this
        # the feature would exist only for clients that already had it easy.
        project_id = _effective_project(params)
        expanded = self._expand_templates_for_project(resources, project_id)
        concrete.extend(expanded)

        result: dict[str, Any] = {"resources": concrete}
        meta: dict[str, Any] = {}
        if unavailable:
            meta["unavailableAdapters"] = unavailable
        if project_id:
            meta["project"] = project_id
        elif expanded == []:
            # Why the list is short, rather than leaving the model to guess.
            # "No project is open" and "this server has no resources" look
            # identical in an empty list and mean entirely different things.
            meta["hint"] = (
                "No project is in scope, so project resources are not listed. Call "
                "project.open (or pass project_id) and list again; the parameterised "
                "set is in resources/templates/list meanwhile."
            )
        if meta:
            result["_meta"] = meta
        return result

    def _expand_templates_for_project(
        self, resources: list[dict[str, Any]], project_id: str | None
    ) -> list[dict[str, Any]]:
        """Turn ``.../{project_id}`` templates into concrete resources.

        Only that one placeholder. A template with any other parameter is
        still a template, and guessing a value for it would publish a URI
        that does not resolve -- worse than not listing it, because a model
        will read it.
        """
        if not project_id:
            return []
        out: list[dict[str, Any]] = []
        for entry in resources:
            template = entry.get("uri_template") or entry.get("uriTemplate")
            if not isinstance(template, str) or "{project_id}" not in template:
                continue
            filled = template.replace("{project_id}", project_id)
            if "{" in filled:
                continue
            concrete = {k: v for k, v in entry.items() if k not in ("uri_template", "uriTemplate")}
            concrete["uri"] = filled
            out.append(concrete)
        return out

    async def _resources_templates_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """``resources/templates/list`` -- the parameterised ones.

        Adapters still speak the legacy ``resources/list`` internally and
        report ``uri_template``; the translation to the spec's
        ``resourceTemplates`` / ``uriTemplate`` happens here, in one place,
        rather than in every adapter.
        """
        sub_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unified-resource-templates",
                "method": "resources/list",
                "params": params,
            }
        )
        templates: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for adapter in self._adapters:
            adapter_id = str(getattr(adapter, "adapter_id", "?"))
            try:
                raw = await adapter.handle_request(sub_request)
                payload = json.loads(raw)
            except Exception as exc:
                logger.error(
                    "unified_mcp_resource_templates_adapter_unavailable",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                unavailable.append({"adapter_id": adapter_id, "error": str(exc)})
                continue
            for entry in payload.get("result", {}).get("resources", []):
                template = entry.get("uri_template") or entry.get("uriTemplate")
                if not template:
                    continue
                out = {k: v for k, v in entry.items() if k != "uri_template"}
                out["uriTemplate"] = template
                templates.append(out)

        result: dict[str, Any] = {"resourceTemplates": templates}
        if unavailable:
            result["_meta"] = {"unavailableAdapters": unavailable}
        return result

    async def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route ``resources/read`` to the adapter named in the URI.

        ``metaforge://<adapter>/<path>`` carries its own routing, so this
        does not have to ask every adapter and take the first answer — which
        would make the result depend on registration order.
        """
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ResourceReadError(str(uri), "uri is required")

        try:
            parsed = parse_resource_uri(uri)
        except ResourceUriError as exc:
            raise ResourceReadError(uri, str(exc)) from exc

        adapter = next(
            (a for a in self._adapters if getattr(a, "adapter_id", None) == parsed.adapter),
            None,
        )
        if adapter is None:
            known = sorted(str(getattr(a, "adapter_id", "?")) for a in self._adapters)
            raise ResourceNotFoundError(
                f"{uri} (no adapter {parsed.adapter!r}; loaded: {', '.join(known)})"
            )

        raw = await adapter.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "unified-resources-read",
                    "method": "resources/read",
                    "params": params,
                }
            )
        )
        payload = json.loads(raw)
        if "error" in payload:
            err = payload["error"]
            message = err.get("message", "resource read failed")
            # FORGE-388: honour the adapter's own code. This used to raise
            # ResourceReadError whatever came back, so an adapter saying
            # "this resource does not exist" reached the client as an
            # execution error carrying the words "Resource not found" --
            # the code contradicting its own message, and a client
            # branching on the code drawing the wrong conclusion.
            if err.get("code") == _RESOURCE_NOT_FOUND:
                raise ResourceNotFoundError(f"{uri}: {message}")
            raise ResourceReadError(uri, message)
        return dict(payload.get("result", {}))

    # ── Tool-id resolution (FORGE-343) ────────────────────────────────────
    #
    # MetaForge tool ids are dotted (``twin.get_node``) but models routinely
    # send ``twin_get_node`` or ``twin/get_node``. FORGE-236 fixed exactly
    # this on the harness side after watching a model read a bare "not
    # found" as a dead backend; the MCP path — the one every external
    # harness uses — never got the same treatment.

    @staticmethod
    def _slug(name: str) -> str:
        """Collapse every separator so spellings of one tool id agree.

        Splitting on the first separator does not work: ``omniverse_usd``
        is an adapter id that contains an underscore, so there is no
        reliable boundary between adapter and tool. Removing the
        separators entirely sidesteps that.
        """
        return re.sub(r"[^a-z0-9]", "", name.lower())

    def _resolve_tool_id(self, requested: str) -> str:
        """Return the real tool id, or raise naming ones that exist.

        An alias resolves only when it maps to exactly one registered tool.
        Two candidates means guessing, and guessing which tool to run is how
        a read turns into a write.
        """
        if requested in self._tool_index:
            return requested

        slug = self._slug(requested)
        matches = [tid for tid in self._tool_index if self._slug(tid) == slug]
        if len(matches) == 1:
            logger.info("mcp_tool_alias_resolved", requested=requested, resolved=matches[0])
            return matches[0]

        raise ToolNotFoundError(requested, did_you_mean=self._closest_tool_ids(requested))

    def _closest_tool_ids(self, requested: str, limit: int = 5) -> list[str]:
        """Registered ids closest to what was asked for.

        Matched on the slug so a separator mistake still scores as near,
        which is the mistake actually observed.
        """
        known = sorted(self._tool_index)
        if not requested:
            return known[:limit]
        by_slug = {self._slug(tid): tid for tid in known}
        close = difflib.get_close_matches(self._slug(requested), list(by_slug), n=limit, cutoff=0.6)
        if close:
            return [by_slug[c] for c in close]
        # Nothing similar: naming the adapter's own tools is still more use
        # than naming none, when the prefix is recognisable.
        prefix = requested.split(".")[0].split("_")[0].lower()
        return [tid for tid in known if tid.lower().startswith(prefix)][:limit]

    async def _authorise(
        self, tool_id: str, arguments: dict[str, Any], call_id: str = ""
    ) -> Approver | None:
        """Hold a write until a human approves it, whoever asked (FORGE-359).

        Raises rather than returning a flag: every path out of here that is
        not an approval must stop the call, and an exception cannot be
        forgotten at a call site the way a returned bool can.

        Returns the human who approved, when the gate named one. For most
        tools that is bookkeeping; for a human-authority tool (FORGE-393) it
        is the value the tool writes down, so a missing one is fatal here
        rather than filled in later.
        """
        # Before anything else: the caller does not get to say who approved.
        reject_caller_supplied_approver(tool_id, arguments)
        # FORGE-360: the local-write exemption exists only because a stdio
        # session had nowhere to answer an approval -- F1 says so in
        # ``guardrails._EXEMPTIBLE``. A client that can elicit *is* somewhere
        # to answer, so the exemption stops applying to it. This is the flip
        # that comment asked for, narrowed to connections that can actually
        # be asked rather than applied to every stdio session blindly.
        can_elicit = self.can_elicit
        decision = decide(
            tool_id,
            caller=self._caller,
            twin_mutations_enabled=self._twin_mutations_enabled(),
            exempt_local_writes=self._exempt_local_writes and not can_elicit,
            # FORGE-407: `twin.query_cypher` is a read or a write depending on
            # the query. Without the arguments the gate can only judge the
            # tool, which refused every `MATCH ... RETURN` on any deployment
            # with twin mutations enabled.
            arguments=arguments,
        )
        if not decision.requires_approval:
            return None

        # In-harness first: the person who asked for this is looking at that
        # window. Not a fallback chain -- once a client has been asked, going
        # on to the dashboard would put the same question to a second person
        # and discard the first answer.
        gate = (
            elicitation_gate(self._elicitor)
            if can_elicit and self._elicitor is not None
            else self._approval_gate
        )
        if gate is None:
            # Configured to hold, with nothing to hold it with. Refusing is
            # the only honest option: running it would leave the guardrail
            # looking present while doing nothing, and nobody audits a
            # control they believe is switched on.
            logger.error(
                "mcp_approval_gate_missing",
                tool_id=tool_id,
                caller=self._caller.value,
                reason=decision.reason,
            )
            raise ApprovalNotConfiguredError(tool_id, decision.reason)

        logger.info(
            "mcp_tool_call_held_for_approval",
            tool_id=tool_id,
            caller=self._caller.value,
            reason=decision.reason,
            route="elicitation" if can_elicit else "dashboard",
        )
        route = "elicitation" if can_elicit else "dashboard"
        held_from = time.monotonic()
        resolution = resolve_approval(
            await gate(
                ApprovalAsk(
                    tool_id=tool_id,
                    arguments=arguments,
                    caller=self._caller,
                    reason=decision.reason,
                    project=_effective_project(arguments),
                )
            )
        )
        held_seconds = time.monotonic() - held_from
        outcome = resolution.outcome
        if outcome is not ApprovalOutcome.APPROVED:
            logger.info(
                "mcp_tool_call_not_approved",
                tool_id=tool_id,
                caller=self._caller.value,
                outcome=outcome.value,
            )
            raise ApprovalRejectedError(tool_id, outcome)

        if requires_human_authority(tool_id) and resolution.approver is None:
            # Approved, but by nobody we can name. For an ordinary write that
            # would be fine. Here the approver's name *is* the result, so
            # writing the gate anyway would record an authority that does not
            # exist -- which is the bug, just arriving by a different door.
            logger.error(
                "mcp_human_authority_missing",
                tool_id=tool_id,
                caller=self._caller.value,
            )
            raise HumanAuthorityRequiredError(
                tool_id,
                "the approval was granted but the gate did not identify who granted it",
            )

        if call_id:
            self._remember_hold(
                call_id,
                ApprovalRecord(
                    outcome=outcome.value,
                    route=route,
                    held_seconds=held_seconds,
                    approval_id=resolution.approval_id,
                    approver=(
                        resolution.approver.label if resolution.approver is not None else None
                    ),
                    approver_verified=(
                        resolution.approver.verified if resolution.approver is not None else False
                    ),
                ),
            )
        return resolution.approver

    def _remember_hold(self, call_id: str, record: ApprovalRecord) -> None:
        """Keep one hold until the envelope for that call reads it.

        Popped by `_tools_call`. A legacy-dialect caller never reads it, and a
        raised error can skip the pop, so the map is capped rather than
        trusted to drain -- an observability aid must not become a leak.
        """
        if len(self._approval_records) > 64:
            self._approval_records.clear()
        self._approval_records[call_id] = record

    def take_approval_record(self, call_id: str) -> ApprovalRecord | None:
        """The hold for ``call_id``, removed. ``None`` when it was not held."""
        return self._approval_records.pop(call_id, None)

    async def _dispatch_tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route ``tool/call`` to the adapter that owns ``tool_id``."""
        tool_id = self._resolve_tool_id(params.get("tool_id", ""))
        adapter = self._tool_index[tool_id]
        # `_call_id` is protocol bookkeeping, not an argument. Adapters
        # validate what they are given, and an unexpected key is exactly the
        # kind of thing a strict schema rejects. Kept here first, because
        # FORGE-417's approval record is filed against it.
        call_id = str(params.get("_call_id") or "")
        params = {k: v for k, v in params.items() if k != "_call_id"}
        params["tool_id"] = tool_id

        # Take the reserved key away before it is looked at, so a client
        # cannot pre-set the field the dispatcher is about to fill.
        arguments = strip_reserved_arguments(params.get("arguments") or {})
        params["arguments"] = arguments

        approver = await self._authorise(tool_id, arguments, call_id)
        if approver is not None and requires_human_authority(tool_id):
            # The tool reads its deciding human from here and nowhere else.
            arguments[APPROVED_BY_ARG] = approver.label

        # Commit-by-reference: fill a commit_geometry call's step_base64 from the
        # last export for this (session_id, obj_id) so agents needn't thread the
        # blob. Explicit step_base64 always wins.
        if tool_id == "twin.commit_geometry":
            args = params.get("arguments")
            if isinstance(args, dict):
                had_explicit_blob = bool(args.get("step_base64"))
                filled = self._geom_stash.fill(args)
                # MET-642 S4 finding: a commit-by-reference miss (no matching
                # prior export for this session_id/obj_id, and no explicit
                # step_base64 either) previously surfaced only as
                # commit_geometry's generic "no geometry" error, with no way
                # to tell from logs whether the ids just never matched a real
                # export_model call. Logging the attempt either way closes
                # that gap. Not a "miss" if the caller passed step_base64
                # directly -- fill() correctly no-ops in that case.
                if not had_explicit_blob:
                    event = (
                        "geometry_commit_by_reference"
                        if filled
                        else "geometry_commit_by_reference_miss"
                    )
                    logger.info(
                        event,
                        session_id=args.get("session_id"),
                        obj_id=args.get("obj_id"),
                    )
                elif filled.diverged:
                    # MET-684: see the matching branch in
                    # skill_registry/registry_bridge.py. The blob the caller
                    # carried back does not match the export it names, so the
                    # pristine one was substituted -- logged rather than
                    # silently repaired.
                    logger.warning(
                        "geometry_commit_blob_diverged",
                        session_id=args.get("session_id"),
                        obj_id=args.get("obj_id"),
                        stashed_chars=filled.stashed_chars,
                        supplied_chars=filled.supplied_chars,
                        resolution="used_stashed_export",
                    )

        # Delegate to the adapter's own JSON-RPC dispatcher so its
        # per-tool error handling, timing, and structlog records all
        # apply unchanged.
        sub_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "unified",
                "method": "tool/call",
                "params": params,
            }
        )
        sub_response_text = await adapter.handle_request(sub_request)
        sub_response: dict[str, Any] = json.loads(sub_response_text)

        if "error" in sub_response:
            err = sub_response["error"]
            data = err.get("data") or {}
            tid = data.get("tool_id", tool_id)
            if err["code"] == _METHOD_NOT_FOUND:
                raise ToolNotFoundError(tid)
            raise ToolHandlerError(
                tid,
                data.get("details") or err.get("message", "Tool execution failed"),
                float(data.get("duration_ms", 0.0)),
            )
        result = sub_response.get("result", {})
        # Remember an export's STEP so a later commit can reference it (see above).
        if tool_id == "freecad.export_model":
            args = params.get("arguments")
            if isinstance(args, dict) and isinstance(result, dict):
                remembered = self._geom_stash.remember(args, result)
                event = (
                    "geometry_export_remembered" if remembered else "geometry_export_not_remembered"
                )
                logger.info(
                    event,
                    session_id=args.get("session_id"),
                    obj_id=args.get("obj_id"),
                )
        return result

    #: How long an adapter gets to answer a health probe. Short on purpose:
    #: orchestrators poll this endpoint for readiness, and a probe that can
    #: hang turns one sick adapter into a sick gateway.
    _PROBE_TIMEOUT_SECONDS = 3.0

    def _telemetry_report(self) -> dict[str, Any]:
        """Is metric recording actually live on this server (FORGE-413)?

        Three-valued for the same reason the code-version check is: a
        collector that exists but publishes nowhere is not the same as one
        that was never wired, and calling either "on" is the failure being
        fixed.
        """
        if self._metrics is None:
            return {
                "metrics": "not configured",
                "detail": (
                    "No metrics collector was passed to this server, so every "
                    "metaforge_mcp_* metric records nothing and the alert rules "
                    "on them cannot fire. The entrypoint should pass "
                    "metrics=collector_for(...)."
                ),
            }
        recording = getattr(self._metrics, "is_recording", None)
        if recording is False:
            return {
                "metrics": "no-op",
                "detail": (
                    "A collector is wired but has no OTel SDK meter behind it, "
                    "so samples go nowhere. Check OTEL_EXPORTER_OTLP_ENDPOINT "
                    "and that METAFORGE_OTEL_EXPORT is not 'off'."
                ),
            }
        # `None` means a collaborator that predates `is_recording` (a test
        # double); reported as unknown rather than asserted either way.
        return {"metrics": "recording" if recording else "unknown"}

    def _client_report(self) -> dict[str, Any]:
        """Who is on the other end, and whether we speak the same protocol.

        ``connected: false`` is the honest answer before any handshake --
        the legacy transports and the tests call methods directly without
        ``initialize``, and reporting an empty name there would read as a
        client that failed to identify itself rather than one that never
        arrived.
        """
        if self._client_info is None and self._client_protocol is None:
            return {
                "connected": False,
                "protocol_negotiated": self._negotiated_protocol,
                "detail": "no initialize handshake has been completed on this server",
            }
        info = self._client_info or {}
        out: dict[str, Any] = {
            "connected": True,
            "name": info.get("name"),
            "version": info.get("version"),
            "protocol_requested": self._client_protocol,
            "protocol_negotiated": self._negotiated_protocol,
            # FORGE-360: the approval route this connection actually has.
            # "Writes are held for approval" means something different when
            # the holding place is a dashboard nobody has open.
            "can_elicit": self.can_elicit,
        }
        if self._client_protocol and self._client_protocol != self._negotiated_protocol:
            out["protocol_skew"] = True
            out["detail"] = (
                f"client asked for MCP {self._client_protocol}; this server does not "
                f"speak it and negotiated {self._negotiated_protocol}. Anything added "
                "after that revision is not available on this connection."
            )
        return out

    async def _probe_adapter(self, adapter: McpToolServer) -> dict[str, Any]:
        """Ask one adapter whether it is actually there."""
        entry: dict[str, Any] = {
            "adapter_id": str(getattr(adapter, "adapter_id", "?")),
            "version": str(getattr(adapter, "version", "?")),
            "tools_registered": len(getattr(adapter, "tool_ids", []) or []),
        }
        request = json.dumps(
            {"jsonrpc": "2.0", "id": "unified-health", "method": "health/check", "params": {}}
        )
        try:
            raw = await asyncio.wait_for(
                adapter.handle_request(request), timeout=self._PROBE_TIMEOUT_SECONDS
            )
            payload = json.loads(raw)
        except TimeoutError:
            entry["reachable"] = False
            entry["error"] = f"no answer within {self._PROBE_TIMEOUT_SECONDS:g}s"
            return entry
        except Exception as exc:
            entry["reachable"] = False
            entry["error"] = str(exc)
            return entry

        if "error" in payload:
            entry["reachable"] = False
            entry["error"] = payload["error"].get("message", "health/check returned an error")
            return entry
        entry["reachable"] = True
        return entry

    async def _health_check(self) -> dict[str, Any]:
        """Aggregate health across every adapter into one report.

        FORGE-332: this used to return ``status: "healthy"`` unconditionally
        and list each adapter's tool count *from registration* -- so a gateway
        with every CAD container down answered "healthy" with a full adapter
        list. Nothing here had asked an adapter anything.

        That is worse than the tools/list problem it resembles (FORGE-339),
        where the tool count at least shrank. A health report that cannot say
        unhealthy is the one thing a doctor reads, and /metaforge:doctor is
        built on this.

        The service stays ``healthy`` only when every adapter answers;
        otherwise ``degraded``, naming the ones that did not. The HTTP status
        stays 200 either way -- the MCP server *is* up, and an orchestrator
        restarting the gateway because an optional CAD container is down
        would be the wrong cure.
        """
        now = datetime.now(UTC)
        uptime = (now - self._start_time).total_seconds()

        # The reporter does not probe itself: an entry that is reachable by
        # construction is noise in a diagnostic, and it would disagree with
        # `adapter_count` (FORGE-409).
        adapter_health = list(
            await asyncio.gather(*(self._probe_adapter(a) for a in self._domain_adapters()))
        )
        unreachable = [a["adapter_id"] for a in adapter_health if not a["reachable"]]
        # FORGE-379: publish each probe so an alert can fire on a down
        # adapter, rather than it waiting for somebody to run the doctor.
        if self._metrics is not None:
            for entry in adapter_health:
                try:
                    self._metrics.record_mcp_adapter_probe(
                        str(entry["adapter_id"]), bool(entry["reachable"])
                    )
                except Exception as exc:  # noqa: BLE001 — never fail health
                    logger.warning("mcp_adapter_probe_metric_failed", error=str(exc))

        # FORGE-411: which code this process is actually running. The sidecar
        # on fidel-dev served two-day-old code beside a current checkout and
        # nothing said so -- the symptoms looked like a smaller deployment, so
        # four bug reports were filed against that run and two of their
        # findings were not real. The expensive part was not the stale image;
        # it was that staleness took inference to establish.
        from metaforge.mcp.build_info import code_version

        version_info = code_version(reloads=self._reloads)
        code = version_info.report()
        if self._metrics is not None:
            try:
                self._metrics.record_mcp_code_version(version_info.result, reloads=self._reloads)
            except Exception as exc:  # noqa: BLE001 — never fail health
                logger.warning("mcp_code_version_metric_failed", error=str(exc))
        if version_info.stale:
            # Logged as well as reported, because the alert is what makes this
            # useful to somebody who is not already looking at the health
            # call -- which is the entire failure being fixed.
            logger.warning(
                "mcp_running_stale_code",
                build_sha=code["build_sha"],
                source_sha=code["source_sha"],
            )

        report: dict[str, Any] = {
            "service": "metaforge-mcp",
            "version": self._version,
            "code": code,
            # A stale process is not "healthy": it answers every request
            # correctly for code nobody is looking at.
            "status": "stale" if code.get("stale") else "degraded" if unreachable else "healthy",
            "uptime_seconds": round(uptime, 1),
            # Excludes the health adapter itself: it is the reporter, not a
            # reportee, and counting it would tell an operator they have one
            # more tool adapter than they wired (FORGE-409).
            "adapter_count": len(self._domain_adapters()),
            "tool_count": len(self._tool_index),
            # FORGE-332: A5 asks the doctor about four things -- gateway,
            # adapters, auth and version skew. The adapters are probed
            # above; these two are the rest, and neither was answerable
            # from this call before. The doctor workflow already told the
            # agent to "report the auth mode", which it could only do by
            # inventing one.
            "auth": (
                self._auth_posture.report()
                if self._auth_posture is not None
                else dict(UNKNOWN_AUTH)
            ),
            "client": self._client_report(),
            # FORGE-413: whether anything this server records reaches
            # Prometheus. It did not, for every MCP metric, and the only way
            # to find out was to query Prometheus and get zero series back --
            # which reads like a quiet system. Asking the doctor is cheaper.
            "telemetry": self._telemetry_report(),
            # FORGE-371: a doctor that cannot say "links are off" leaves the
            # reader to conclude the tools simply never produce them.
            "dashboard_links": (
                "enabled"
                if self._deeplinks.configured
                else "disabled (no METAFORGE_DASHBOARD_URL configured)"
            ),
            "adapters": adapter_health,
        }
        if unreachable:
            # Named at the top level as well as per-adapter: a caller that
            # reads only `status` still gets told what to look at.
            report["unreachable_adapters"] = unreachable
            report["detail"] = (
                f"{len(unreachable)} of {len(self._adapters)} adapters did not answer: "
                f"{', '.join(unreachable)}. Their tools are registered but calls to them "
                "will fail."
            )
            logger.warning(
                "unified_mcp_health_degraded",
                unreachable=unreachable,
                adapter_count=len(self._adapters),
            )
        return report


#: Exception type -> the ``error_class`` label (FORGE-379).
#:
#: Reuses the exception taxonomy the server already raises rather than
#: inventing a parallel one. A second list is a list that can disagree with
#: the errors clients actually receive, and the disagreement nobody notices
#: is the one where a dashboard says "tool failures" for a queue of writes
#: waiting on a human.
_ERROR_CLASSES: tuple[tuple[str, str], ...] = (
    ("ToolNotFoundError", "tool_not_found"),
    ("PromptNotFoundError", "prompt_not_found"),
    ("ResourceNotFoundError", "resource_not_found"),
    ("ResourceReadError", "resource_read_failed"),
    ("ApprovalNotConfiguredError", "approval_not_configured"),
    ("ApprovalRejectedError", "approval_refused"),
    # FORGE-393. Separate labels on purpose: a rise in the first means a
    # client is still passing the old argument, a rise in the second means
    # approvals are landing without an identified human -- different faults
    # with different fixes, and folding them together hides both.
    ("ApproverArgumentRejectedError", "approver_caller_supplied"),
    ("HumanAuthorityRequiredError", "human_authority_missing"),
    ("ToolHandlerError", "tool_execution_error"),
)


def error_class(exc: BaseException) -> str:
    """The metric label for one failure.

    Matched on the exception's own type name so a subclass added later is
    still classified, and anything unrecognised is ``unexpected`` rather
    than being folded into a known bucket -- an error nobody has classified
    should stand out, not hide inside ``tool_execution_error``.
    """
    names = {cls.__name__ for cls in type(exc).__mro__}
    for name, label in _ERROR_CLASSES:
        if name in names:
            return label
    return "unexpected"


def _effective_project(arguments: dict[str, Any]) -> str | None:
    """Which project this call lands in, for the reviewer's benefit.

    An explicit ``project_id`` argument wins, exactly as it does at the data
    layer. Otherwise it is whatever the session is scoped to -- which since
    FORGE-335 is the normal case, and is invisible in the arguments.
    """
    explicit = arguments.get("project_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        from mcp_core.context import current_context

        scoped = current_context().project_id
    except Exception:  # noqa: BLE001 — a missing project must not block approval
        return None
    return str(scoped) if scoped else None


# ---------------------------------------------------------------------------
# Bootstrap helper
# ---------------------------------------------------------------------------


async def build_unified_server(
    adapter_ids: list[str] | None = None,
    knowledge_service: Any = None,
    twin: Any = None,
    constraint_engine: Any = None,
    project_backend: Any = None,
    memory_client: Any = None,
    memory_insight_store: Any = None,
    twin_allow_mutations: bool = False,
    profile: str | None = None,
    agent_session_store: Any = None,
    capture_sessions: bool = False,
    decision_recorder: Any = None,
    geometry_recorder: Any = None,
    blob_stager: Any = None,
    component_catalog_store: Any = None,
    component_intent_llm: Any = None,
    component_recorder: Any = None,
    brief_provider: Any = None,
    dashboard_url: str | None = None,
    metrics: Any = None,
    caller: Caller = Caller.UNTRUSTED,
    approval_gate: Any = None,
) -> UnifiedMcpServer:
    """Discover and instantiate every enabled adapter, then wrap.

    Reuses ``tool_registry.bootstrap.bootstrap_tool_registry`` so the
    unified server picks up the same env-driven adapter allow-list
    (``METAFORGE_ADAPTERS``, ``METAFORGE_ADAPTER_<ID>_ENABLED``) as the
    main gateway. Knowledge adapter is included only when a
    ``KnowledgeService`` instance is supplied (matches the gateway
    contract from MET-335). Twin and constraint adapters are included
    only when ``twin`` and ``constraint_engine`` are supplied (MET-421).
    Memory adapter is included only when a ``MemoryClient`` is
    supplied (MET-453); the optional ``memory_insight_store`` is
    forwarded so ``memory.list_insights`` works (MET-477).

    ``twin_allow_mutations`` (MET-488) forwards to the twin adapter:
    when True, ``twin.query_cypher`` accepts mutating Cypher
    (CREATE / MERGE / SET / DELETE) so work-products can be created and
    the digital thread built over MCP. Off by default; every call is
    audit-logged by the adapter regardless.

    ``capture_sessions`` (MET-496) turns on server-side auto-capture: when
    True *and* ``agent_session_store`` is supplied, every tool call is
    recorded as an action/error event in an agent session, so MCP/CLI work
    shows up in ``/sessions`` with no client cooperation.

    ``component_catalog_store`` + ``component_intent_llm`` (MET-436):
    the ``component`` adapter (component.search_parametric +
    component.search_intent) registers only when both are supplied,
    together with ``knowledge_service`` (reused for the intent-search
    fuzzy fallback) — same runtime-injected pattern as ``knowledge``.

    ``component_recorder`` (MET-436 follow-up): when supplied, registers
    ``twin.record_component_selection``, which persists one chosen
    ``component.search_*`` result as a BOMItem work product + project
    link. ``None`` skips registration (same pattern as
    ``decision_recorder``).
    """
    registry: ToolRegistry = await bootstrap_tool_registry(
        adapter_ids=adapter_ids,
        knowledge_service=knowledge_service,
        twin=twin,
        constraint_engine=constraint_engine,
        project_backend=project_backend,
        memory_client=memory_client,
        memory_insight_store=memory_insight_store,
        twin_allow_mutations=twin_allow_mutations,
        agent_session_store=agent_session_store,
        decision_recorder=decision_recorder,
        geometry_recorder=geometry_recorder,
        blob_stager=blob_stager,
        component_catalog_store=component_catalog_store,
        component_intent_llm=component_intent_llm,
        component_recorder=component_recorder,
        brief_provider=brief_provider,
    )
    capture = (
        SessionCapture(agent_session_store)
        if capture_sessions and agent_session_store is not None
        else None
    )
    return UnifiedMcpServer(
        adapters=registry.list_adapter_servers(),
        session_capture=capture,
        tool_registry=registry,
        profile=profile,
        dashboard_url=dashboard_url,
        metrics=metrics,
        # FORGE-387: conservative by default, like the constructor. The
        # sidecar's transport overrides it with declare_caller(); an
        # in-process caller (a test, an embedding host) says so here.
        caller=caller,
        # Where a held write goes. The gateway builds one from its own
        # approval store; an embedding host supplies its own.
        approval_gate=approval_gate,
    )
