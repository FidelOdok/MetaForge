"""Which MCP protocol revisions this server speaks, and how a request says which (FORGE-599).

MCP has two eras (spec 2026-07-28, "Versioning and Compatibility"):

* **Legacy** revisions (2025-11-25 and earlier) agree a version once, in the
  ``initialize`` handshake, and hold it for the session.
* **Modern** revisions (2026-07-28 and later) have no handshake. Every
  request carries its version in ``params._meta`` under
  ``io.modelcontextprotocol/protocolVersion`` (on HTTP also in the
  ``MCP-Protocol-Version`` header), and the server accepts or rejects each
  request on its own. A version it does not serve is answered with
  ``UnsupportedProtocolVersionError`` listing the ones it does.

This server is dual-era by design: an ``initialize`` request selects the
legacy path exactly as before, and a request carrying a version in its
``_meta`` is checked against :data:`SUPPORTED_PROTOCOL_VERSIONS`.

2026-07-28 is served by default (FORGE-592): ``server/discover`` and
``resultType`` (FORGE-600), questions returned inside results (FORGE-601),
session handles (FORGE-602) and ``subscriptions/listen`` (FORGE-603) are all
in place. ``METAFORGE_MCP_MODERN=off`` stops serving it, for a rollback; a
modern request then gets the spec's own error naming the versions that do
work, which a dual-era client reads as "use one of these".

Layer-1 module: stdlib only.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CACHE_HINTS",
    "HEADER_MISMATCH",
    "INVALID_PARAMS",
    "META_CLIENT_CAPABILITIES",
    "META_CLIENT_INFO",
    "META_SERVER_INFO",
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "MINTING_TOOLS",
    "MODERN_PREVIEW_ENV",
    "SESSION_ARGUMENT",
    "SESSION_ARGUMENT_SCHEMA",
    "META_SESSION_HANDLE",
    "META_SUBSCRIPTION_ID",
    "is_subscription_listen",
    "subscription_ack",
    "subscription_problem",
    "MODERN_PREVIEW_VERSION",
    "MODERN_SWITCH_ENV",
    "LEGACY_PROTOCOL_VERSIONS",
    "META_PROTOCOL_VERSION",
    "MODERN_PROTOCOL_VERSIONS",
    "PROTOCOL_VERSION_HEADER",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "ModernRequest",
    "current_modern_request",
    "decode_header_value",
    "header_mismatch",
    "modern_header_problem",
    "modern_status",
    "META_ERROR",
    "is_legacy_error_code",
    "is_modern",
    "modern_error_code",
    "modern_meta_problem",
    "modern_versions",
    "supported_versions",
    "request_protocol_version",
    "unsupported_version_data",
    "with_modern_request",
]

#: Revisions served through the ``initialize`` handshake, oldest first.
#: 2025-11-25 (FORGE-599): what Claude Code asks for. Its server-side
#: obligations that touch us are met: form elicitation with a plain string
#: enum is unchanged, and a client that declares only URL-mode elicitation is
#: never sent a form (``mcp_core.elicitation.declares_form_elicitation``).
LEGACY_PROTOCOL_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-06-18", "2025-11-25")

#: Handshake-free revisions served per request.
MODERN_PROTOCOL_VERSIONS: tuple[str, ...] = ("2026-07-28",)

#: ``off`` stops serving the handshake-free revisions (a rollback switch).
MODERN_SWITCH_ENV = "METAFORGE_MCP_MODERN"

#: The FORGE-600 preview switch. 2026-07-28 is now on by default, so it no
#: longer changes anything; kept so a deployment that still sets it is not
#: an error.
MODERN_PREVIEW_ENV = "METAFORGE_MCP_MODERN_PREVIEW"
MODERN_PREVIEW_VERSION = "2026-07-28"

#: Every revision this server can serve. ``supported_versions()`` is what a
#: request is checked against (it honours the switch).
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = LEGACY_PROTOCOL_VERSIONS + MODERN_PROTOCOL_VERSIONS


def modern_versions() -> tuple[str, ...]:
    """Handshake-free revisions served now; none when switched off."""
    switch = os.environ.get(MODERN_SWITCH_ENV, "").strip().lower()
    if switch in {"0", "off", "false", "no"}:
        return ()
    return MODERN_PROTOCOL_VERSIONS


def supported_versions() -> tuple[str, ...]:
    return LEGACY_PROTOCOL_VERSIONS + modern_versions()


def is_modern(version: str | None) -> bool:
    """Whether ``version`` is a handshake-free revision we serve."""
    return version is not None and version in modern_versions()


#: The ``_meta`` keys a modern request carries, and the one a result does.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

#: The Streamable HTTP header that mirrors it.
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

#: Error codes from the range the spec reserves for itself (-32020..-32099).
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
INVALID_PARAMS = -32602

#: Caching hints 2026-07-28 requires on these results (``ttlMs``,
#: ``cacheScope``). The lists depend on the connection's profile and on who
#: is calling, so none is ``public``; the tool set is fixed once the server
#: is up, so lists stay fresh for five minutes. A resource read reflects the
#: twin as it is now, so it is never fresh.
CACHE_HINTS: dict[str, tuple[int, str]] = {
    "server/discover": (300_000, "private"),
    "tools/list": (300_000, "private"),
    "prompts/list": (300_000, "private"),
    "resources/list": (300_000, "private"),
    "resources/templates/list": (300_000, "private"),
    "resources/read": (0, "private"),
}


def request_protocol_version(params: Any) -> str | None:
    """The revision a request names in ``params._meta``, if any."""
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get(META_PROTOCOL_VERSION)
    return value if isinstance(value, str) and value else None


def unsupported_version_data(requested: str) -> dict[str, Any]:
    """``error.data`` for ``UnsupportedProtocolVersionError``, newest first."""
    return {"supported": sorted(supported_versions(), reverse=True), "requested": requested}


def modern_meta_problem(params: Any) -> str | None:
    """What a handshake-free request's ``_meta`` is missing, or None.

    The revision and the client's capabilities are required on every request;
    a request without them is malformed (``-32602``). ``clientInfo`` is only a
    SHOULD, so its absence is not an error.
    """
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict):
        return "params._meta is required"
    if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
        return f"params._meta['{META_CLIENT_CAPABILITIES}'] is required and must be an object"
    return None


@dataclass(frozen=True)
class ModernRequest:
    """The handshake-free request being served (FORGE-600).

    Everything a 2026-07-28 request says about its client comes with the
    request, never from an earlier one, so it is held for that request only.
    """

    version: str
    client_capabilities: dict[str, Any] = field(default_factory=dict)
    #: ``io.modelcontextprotocol/clientInfo`` from the request's ``_meta``,
    #: so health.check can name the client without a handshake (FORGE-696).
    client_info: dict[str, Any] | None = None


_CURRENT: ContextVar[ModernRequest | None] = ContextVar("mcp_modern_request", default=None)


def current_modern_request() -> ModernRequest | None:
    """The handshake-free request in progress, or None on the handshake path."""
    return _CURRENT.get()


@contextmanager
def with_modern_request(request: ModernRequest) -> Iterator[ModernRequest]:
    token = _CURRENT.set(request)
    try:
        yield request
    finally:
        _CURRENT.reset(token)


#: FORGE-602: 2026-07-28 removed protocol sessions (``Mcp-Session-Id``).
#: Cross-call state a client wants kept (the project ``project.open`` scopes
#: later calls to, the record ``session.start`` opens) is referenced instead by
#: a server-minted handle the client passes back as an ordinary tool argument.
#: These tools mint one when the caller has none.
MINTING_TOOLS = frozenset({"project.open", "session.start"})

#: The tool argument a handshake-free caller passes its handle in. Every tool
#: lists it, optional, for those callers only.
SESSION_ARGUMENT = "metaforge_session"
SESSION_ARGUMENT_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "Session handle returned by project.open or session.start. Pass it on "
        "every later call so they keep that project's scope and session record."
    ),
}

#: FORGE-603: ``subscriptions/listen`` replaced the GET stream and
#: ``resources/subscribe``. This server's tools, prompts and resource lists
#: are fixed once it is up and it offers no per-resource updates, so it
#: honours none of the notification types: the acknowledgement lists none, and
#: the subscription is then closed gracefully (a completion result), so a
#: client knows it ended on purpose rather than dropped.
META_SUBSCRIPTION_ID = "io.modelcontextprotocol/subscriptionId"
_FILTER_FIELDS = {
    "toolsListChanged": bool,
    "promptsListChanged": bool,
    "resourcesListChanged": bool,
    "resourceSubscriptions": list,
}


def is_subscription_listen(message: Any) -> bool:
    """Whether ``message`` is a handshake-free ``subscriptions/listen`` we serve."""
    return (
        isinstance(message, dict)
        and message.get("method") == "subscriptions/listen"
        and "id" in message
        and is_modern(request_protocol_version(message.get("params")))
    )


def subscription_problem(params: Any) -> str | None:
    """What is wrong with a ``subscriptions/listen`` filter, or None."""
    filters = params.get("notifications") if isinstance(params, dict) else None
    if filters is None:
        return None
    if not isinstance(filters, dict):
        return "notifications must be an object"
    for name, kind in _FILTER_FIELDS.items():
        if name in filters and not isinstance(filters[name], kind):
            return f"notifications.{name} must be a {kind.__name__}"
    return None


def subscription_ack(request_id: Any) -> dict[str, Any]:
    """The acknowledgement: none of the requested types is honoured here."""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/subscriptions/acknowledged",
        "params": {"_meta": {META_SUBSCRIPTION_ID: request_id}, "notifications": {}},
    }


#: Error codes 2026-07-28 calls legacy: allocated by implementations before
#: the spec claimed -32020..-32099 for itself. New implementations SHOULD NOT
#: use them, and the spec says new application codes go outside the whole
#: JSON-RPC server range (-32768..-32000). This server's own codes
#: (``mcp_core.protocol``: tool execution -32001, unavailable -32003, read
#: error -32005, timeout -32006, auth -32007) all sit in the legacy block.
_LEGACY_ERROR_RANGE = range(-32019, -31999)

#: Where a handshake-free ``tools/call`` refusal keeps its code and details.
META_ERROR = "com.metaforge/error"


def is_legacy_error_code(code: Any) -> bool:
    return isinstance(code, int) and code in _LEGACY_ERROR_RANGE


def modern_error_code(code: int) -> int:
    """The application code a legacy one becomes for a 2026-07-28 caller.

    Moved out of the reserved block by 1000, so ``-32001`` (tool execution)
    is ``-31001``: the number still lines up with the code handshake clients
    see and with the docs, and it is outside every reserved range.
    """
    return code + 1000 if is_legacy_error_code(code) else code


#: Where a minted handle is also reported in the result's ``_meta``.
META_SESSION_HANDLE = "com.metaforge/sessionHandle"

#: Methods whose target is mirrored in ``Mcp-Name``, and where it comes from.
_NAME_SOURCES = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}


def modern_header_problem(headers: Any, message: Any) -> str | None:
    """Why a handshake-free HTTP request's headers fail validation, or None.

    2026-07-28 requires ``MCP-Protocol-Version`` and ``Mcp-Method`` on every
    request, and ``Mcp-Name`` on ``tools/call``, ``prompts/get`` and
    ``resources/read``; each must match the body. ``headers`` is any mapping
    with case-insensitive ``get`` (a Starlette ``Headers``).
    """
    if not isinstance(message, dict):
        return None
    method = message.get("method")
    if not headers.get(PROTOCOL_VERSION_HEADER):
        return f"Header mismatch: required header {PROTOCOL_VERSION_HEADER} is missing"
    header_method = headers.get("Mcp-Method")
    if not header_method:
        return "Header mismatch: required header Mcp-Method is missing"
    if header_method != method:
        return (
            f"Header mismatch: Mcp-Method header value '{header_method}' "
            f"does not match body value '{method}'"
        )
    source = _NAME_SOURCES.get(str(method))
    if source is None:
        return None
    params = message.get("params")
    body_name = params.get(source) if isinstance(params, dict) else None
    raw = headers.get("Mcp-Name")
    if not raw:
        return "Header mismatch: required header Mcp-Name is missing"
    decoded = decode_header_value(raw)
    if decoded is None:
        return "Header mismatch: Mcp-Name header value is not valid base64"
    if decoded != body_name:
        return (
            f"Header mismatch: Mcp-Name header value '{decoded}' "
            f"does not match body value '{body_name}'"
        )
    return None


def modern_status(error_code: int | None) -> int:
    """The HTTP status a handshake-free request's answer is sent with.

    An unknown method is ``404`` (so a client can tell a modern server from a
    legacy one that never hosted the endpoint); a malformed request, a
    missing capability and an unserved revision are ``400``.
    """
    if error_code == -32601:
        return 404
    if error_code in (
        INVALID_PARAMS,
        HEADER_MISMATCH,
        MISSING_REQUIRED_CLIENT_CAPABILITY,
        UNSUPPORTED_PROTOCOL_VERSION,
    ):
        return 400
    return 200


def decode_header_value(value: str) -> str | None:
    """A header value with the ``=?base64?...?=`` sentinel decoded.

    None when the sentinel is present but not valid base64 UTF-8, which the
    caller treats as a malformed header.
    """
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return value


def header_mismatch(header_value: str | None, params: Any) -> str | None:
    """Why the ``MCP-Protocol-Version`` header and the body disagree, or None.

    Only a disagreement counts. A header with no ``_meta`` version is a legacy
    client (2025-06-18 defined the header before ``_meta`` carried a
    version), and a ``_meta`` version with no header is left to the version
    check, so neither is rejected here.
    """
    body_value = request_protocol_version(params)
    if not header_value or body_value is None:
        return None
    if header_value.strip() == body_value:
        return None
    return (
        f"Header mismatch: {PROTOCOL_VERSION_HEADER} header value '{header_value.strip()}' "
        f"does not match body value '{body_value}'"
    )
