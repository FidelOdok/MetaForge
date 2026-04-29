"""Resource URI parsing for MCP ``resources/*`` methods (MET-384).

MetaForge resources use the ``metaforge://`` scheme — short, distinct
from ``http(s)``, and easy to grep for. URIs follow

    metaforge://<adapter>/<kind>/<id>[/<sub>...]

so a resource always carries the adapter that knows how to fetch it.
The adapter then matches its own URI templates against ``path``.

This module is layer-1: stdlib + no I/O. Adapter resolution happens
in ``tool_registry.mcp_server`` (layer 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEME = "metaforge"
_URI_PATTERN = re.compile(
    r"^(?P<scheme>[a-z][a-z0-9+\-.]*)://(?P<adapter>[^/]+)/(?P<path>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedResourceUri:
    """Parsed ``metaforge://...`` URI.

    ``adapter`` is the first segment after the authority — it's how
    the server routes ``resources/read`` to the right registration.
    ``path`` is everything after that, with templates already expanded
    (templates only live in manifests, never in concrete URIs).
    """

    scheme: str
    adapter: str
    path: str

    @property
    def raw(self) -> str:
        return f"{self.scheme}://{self.adapter}/{self.path}"


class ResourceUriError(ValueError):
    """Raised when a URI is malformed or uses an unknown scheme."""


def parse_resource_uri(
    uri: str, *, allowed_schemes: tuple[str, ...] = (SCHEME,)
) -> ParsedResourceUri:
    """Parse a resource URI. Raises ``ResourceUriError`` on bad input.

    Defaults to only accepting ``metaforge://``; pass ``allowed_schemes``
    to widen for tests or experimental schemes.
    """
    if not isinstance(uri, str) or not uri:
        raise ResourceUriError("URI must be a non-empty string")
    match = _URI_PATTERN.match(uri)
    if match is None:
        raise ResourceUriError(f"Malformed resource URI: {uri!r}")
    scheme = match.group("scheme").lower()
    if scheme not in allowed_schemes:
        raise ResourceUriError(f"Unknown URI scheme {scheme!r}; expected one of {allowed_schemes}")
    adapter = match.group("adapter")
    path = match.group("path")
    if not adapter or not path:
        raise ResourceUriError(f"URI missing adapter or path: {uri!r}")
    return ParsedResourceUri(scheme=scheme, adapter=adapter, path=path)


__all__ = [
    "SCHEME",
    "ParsedResourceUri",
    "ResourceUriError",
    "parse_resource_uri",
]
