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

:data:`MODERN_PROTOCOL_VERSIONS` is empty on purpose. Serving 2026-07-28
needs ``resultType`` on every result and ``server/discover`` (FORGE-600), and
input requests in results instead of server-initiated ``elicitation/create``
(FORGE-601). Listing the revision before those land would tell a modern
client we follow rules we do not. Until then a modern request gets the
spec's own error naming the versions that do work, which a dual-era client
reads as "use one of these".

Layer-1 module: stdlib only.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HEADER_MISMATCH",
    "LEGACY_PROTOCOL_VERSIONS",
    "META_PROTOCOL_VERSION",
    "MODERN_PROTOCOL_VERSIONS",
    "PROTOCOL_VERSION_HEADER",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "header_mismatch",
    "request_protocol_version",
    "unsupported_version_data",
]

#: Revisions served through the ``initialize`` handshake, oldest first.
#: 2025-11-25 (FORGE-599): what Claude Code asks for. Its server-side
#: obligations that touch us are met: form elicitation with a plain string
#: enum is unchanged, and a client that declares only URL-mode elicitation is
#: never sent a form (``mcp_core.elicitation.declares_form_elicitation``).
LEGACY_PROTOCOL_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-06-18", "2025-11-25")

#: Handshake-free revisions served per request. Empty until FORGE-600 and
#: FORGE-601 land (see the module docstring); 2026-07-28 goes here then.
MODERN_PROTOCOL_VERSIONS: tuple[str, ...] = ()

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = LEGACY_PROTOCOL_VERSIONS + MODERN_PROTOCOL_VERSIONS

#: The ``_meta`` key a modern request carries its revision in.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"

#: The Streamable HTTP header that mirrors it.
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

#: Error codes from the range the spec reserves for itself (-32020..-32099).
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022


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
    return {"supported": sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True), "requested": requested}


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
