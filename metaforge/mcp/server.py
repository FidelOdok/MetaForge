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
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from mcp_core.annotations import annotations_for
from mcp_core.auth import UNKNOWN_AUTH, AuthPosture
from mcp_core.elicitation import ELICITATION_PROTOCOL_VERSION, Elicitor, elicitation_gate
from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalGateFn,
    ApprovalNotConfiguredError,
    ApprovalOutcome,
    ApprovalRejectedError,
    Caller,
    decide,
)
from mcp_core.profiles import tools_for_profile
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
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_TOOL_EXECUTION_ERROR = -32001
# MET-338: dedicated code for failed API-key auth so clients can branch
# on it without parsing message strings.
_AUTH_DENIED = -32002


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
        caller: Caller = Caller.LOCAL,
        approval_gate: ApprovalGateFn | None = None,
        exempt_local_writes: bool = True,
        auth_posture: AuthPosture | None = None,
        elicitor: Elicitor | None = None,
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
        # `caller` is set per transport today (stdio is the engineer at the
        # machine; anything remote is not) and becomes per-request identity
        # when FORGE-330 lands OAuth — the shape does not change, only where
        # the value comes from.
        self._caller = caller
        self._approval_gate = approval_gate
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

    def attach_elicitor(self, elicitor: Elicitor) -> None:
        """Give this server a way to put a question to the connected client.

        Set by the transport for the same reason the auth posture is: only
        the transport has a channel back. A transport with no
        server-to-client direction never calls this, and ``can_elicit``
        stays False -- which is the honest state, not a degraded one.
        """
        self._elicitor = elicitor

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
                return json.dumps(make_error(request_id, _METHOD_NOT_FOUND, str(exc)))
            except (ResourceNotFoundError, ResourceReadError) as exc:
                # Same reason the approval errors are caught below: an
                # exception escaping handle_request reaches the client as a
                # dropped connection, which says nothing about what went
                # wrong. A missing resource is an ordinary answer.
                missing = isinstance(exc, ResourceNotFoundError)
                return json.dumps(
                    make_error(
                        request_id,
                        _METHOD_NOT_FOUND if missing else _TOOL_EXECUTION_ERROR,
                        str(exc),
                        {"uri": getattr(exc, "uri", None), "retryable": False},
                    )
                )
            except (ApprovalNotConfiguredError, ApprovalRejectedError) as exc:
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
                logger.error(
                    "unified_mcp_tool_failed",
                    tool_id=exc.tool_id,
                    duration_ms=round(exc.duration_ms, 2),
                    details=exc.details,
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

    # Protocol version we negotiate with. The MCP spec rev that Claude
    # Code 2.1.x speaks; bumping this is a coordinated change with the
    # client side, not a routine bump.
    _MCP_PROTOCOL_VERSION = "2024-11-05"
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
        if self._client_protocol in self._SUPPORTED_PROTOCOL_VERSIONS:
            self._negotiated_protocol = self._client_protocol
        else:
            self._negotiated_protocol = self._DEFAULT_PROTOCOL_VERSION
            if self._client_protocol:
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
        allowed = set(tools_for_profile(self._profile)) if self._profile else None
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
                "name": self._profile,
                "toolCount": len(mcp_tools),
                # A profile naming a tool no loaded adapter registers is a
                # configuration mistake, not a smaller profile. Say so.
                "missing": sorted(allowed - served),
            }
        if meta:
            result["_meta"] = meta
        return result

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
            result = await self._tool_call(
                {"tool_id": tool_name, "arguments": arguments, "_call_id": call_id}
            )
        except (ToolNotFoundError, ToolHandlerError):
            # Re-raise so the outer handler emits a JSON-RPC error
            # envelope. The MCP spec also accepts isError=true content
            # responses, but JSON-RPC errors are clearer for "tool not
            # found" / hard execution failures and Claude Code surfaces
            # both correctly.
            raise
        # Body is wrapped in MCP's ``content`` array. We use ``text``
        # type with a JSON-serialised payload — the adapter outputs are
        # structured dicts, and clients can json.parse the text. If we
        # add binary outputs later we'll branch here on result shape.
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result),
                }
            ],
            "isError": False,
            # The reference lives in `_meta` rather than inside the text
            # payload: the text is the tool's own output and belongs to the
            # tool, and burying a protocol-level id in it would make every
            # adapter's schema wrong.
            "_meta": {"callId": call_id},
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
        """
        if self._capture is None:
            return await self._dispatch_tool_call(params)

        tool_id = params.get("tool_id", "")
        arguments = params.get("arguments", {}) or {}
        call_id = params.get("_call_id") or uuid4().hex[:16]
        t0 = time.monotonic()
        try:
            result = await self._dispatch_tool_call(params)
        except ToolHandlerError as exc:
            await self._capture.on_tool_call(
                tool_id,
                arguments,
                status="error",
                duration_ms=(time.monotonic() - t0) * 1000,
                error=exc.details,
                call_id=call_id,
            )
            raise
        await self._capture.on_tool_call(
            tool_id,
            arguments,
            status="ok",
            duration_ms=(time.monotonic() - t0) * 1000,
            result=result,
            call_id=call_id,
        )
        return result

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

        result: dict[str, Any] = {"resources": resources}
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
            raise ResourceReadError(uri, err.get("message", "resource read failed"))
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

    async def _authorise(self, tool_id: str, arguments: dict[str, Any]) -> None:
        """Hold a write until a human approves it, whoever asked (FORGE-359).

        Raises rather than returning a flag: every path out of here that is
        not an approval must stop the call, and an exception cannot be
        forgotten at a call site the way a returned bool can.
        """
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
        )
        if not decision.requires_approval:
            return

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
        outcome = await gate(
            ApprovalAsk(
                tool_id=tool_id,
                arguments=arguments,
                caller=self._caller,
                reason=decision.reason,
                project=_effective_project(arguments),
            )
        )
        if outcome is not ApprovalOutcome.APPROVED:
            logger.info(
                "mcp_tool_call_not_approved",
                tool_id=tool_id,
                caller=self._caller.value,
                outcome=outcome.value,
            )
            raise ApprovalRejectedError(tool_id, outcome)

    async def _dispatch_tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route ``tool/call`` to the adapter that owns ``tool_id``."""
        tool_id = self._resolve_tool_id(params.get("tool_id", ""))
        adapter = self._tool_index[tool_id]
        # `_call_id` is protocol bookkeeping, not an argument. Adapters
        # validate what they are given, and an unexpected key is exactly the
        # kind of thing a strict schema rejects.
        params = {k: v for k, v in params.items() if k != "_call_id"}
        params["tool_id"] = tool_id
        await self._authorise(tool_id, params.get("arguments") or {})

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

        adapter_health = list(
            await asyncio.gather(*(self._probe_adapter(a) for a in self._adapters))
        )
        unreachable = [a["adapter_id"] for a in adapter_health if not a["reachable"]]

        report: dict[str, Any] = {
            "service": "metaforge-mcp",
            "version": self._version,
            "status": "degraded" if unreachable else "healthy",
            "uptime_seconds": round(uptime, 1),
            "adapter_count": len(self._adapters),
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
    )
