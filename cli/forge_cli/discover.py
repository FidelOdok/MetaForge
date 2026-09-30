"""Find the MetaForge gateway, or say plainly that there isn't one (FORGE-329).

A2 is "guided connect: auto-detect a local gateway or enter a team URL".
What shipped was the second half: the plugin prompts for a URL with a
localhost default. Nothing checked whether anything was actually there, so
getting it wrong installed cleanly and then failed on every tool call --
which in a harness reads as "the plugin is broken", not "you typed the
wrong port".

The check that matters is not "is something listening". A dev box has
plenty of things on plenty of ports, and reporting one of them as a
gateway would be worse than finding nothing: the user would paste it in
and get a stranger set of failures. So a candidate only counts when it
answers an MCP ``initialize`` and names itself.

stdlib only (``urllib``), so this runs before anything is installed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

__all__ = [
    "DEFAULT_CANDIDATES",
    "candidates_for",
    "GatewayProbe",
    "describe",
    "detect_gateway",
    "probe_gateway",
]

#: Where a local gateway usually is. ``8765`` is the MCP sidecar
#: (docker-compose.override.yml); ``8000`` is the gateway itself, for a
#: deployment that serves MCP from the same process.
DEFAULT_CANDIDATES: tuple[str, ...] = (
    "http://localhost:8765/mcp",
    "http://127.0.0.1:8765/mcp",
    "http://localhost:8000/mcp",
)

#: Where the MCP sidecar sits when it is a separate service from the
#: REST gateway (docker-compose.override.yml).
_SIDECAR_PORT = 8765

#: What a MetaForge server calls itself in ``initialize``.
_SERVER_NAME = "metaforge-mcp"

_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": "forge-connect-probe",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "forge-connect", "version": "1"},
        },
    }
).encode()


@dataclass(frozen=True)
class GatewayProbe:
    """What one candidate URL turned out to be."""

    url: str
    reachable: bool
    is_metaforge: bool
    server_name: str = ""
    server_version: str = ""
    protocol: str = ""
    requires_auth: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Answered, and answered as MetaForge.

        A server needing credentials still counts: the URL is right, the
        token is a separate thing to supply, and telling someone their
        gateway was not found because they have auth on would send them
        looking for the wrong problem.
        """
        return self.reachable and (self.is_metaforge or self.requires_auth)


def probe_gateway(url: str, *, timeout: float = 2.0) -> GatewayProbe:
    """Ask one URL whether it is a MetaForge MCP server."""
    request = urllib.request.Request(  # noqa: S310 — http(s) only, see below
        url,
        data=_INITIALIZE,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if not url.startswith(("http://", "https://")):
        return GatewayProbe(url, False, False, detail="not an http(s) URL")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # Something is there and it wants credentials. The URL is
            # right; the token is the user's next step, not a reason to
            # keep looking.
            return GatewayProbe(
                url, True, False, requires_auth=True, detail=f"needs credentials (HTTP {exc.code})"
            )
        return GatewayProbe(url, True, False, detail=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return GatewayProbe(url, False, False, detail=str(getattr(exc, "reason", exc)))

    try:
        payload = json.loads(body)
        info = payload["result"]["serverInfo"]
        name = str(info.get("name") or "")
    except (ValueError, KeyError, TypeError):
        # Something answered, and it is not this. Naming it as a gateway
        # would send the user to paste in a URL that will never work.
        return GatewayProbe(
            url, True, False, detail="answered, but not with an MCP initialize result"
        )

    if name != _SERVER_NAME:
        return GatewayProbe(
            url, True, False, server_name=name, detail=f"an MCP server, but {name!r}"
        )
    return GatewayProbe(
        url,
        True,
        True,
        server_name=name,
        server_version=str(info.get("version") or ""),
        protocol=str(payload["result"].get("protocolVersion") or ""),
    )


def candidates_for(configured: str) -> tuple[str, ...]:
    """Candidate MCP endpoints, most-likely first, for a configured base URL.

    ``configured`` is the REST gateway (what this CLI talks to). The MCP
    endpoint is not always on it: in the standard compose file the gateway
    is :8000 and the MCP sidecar is a separate service on :8765, so
    ``<gateway>/mcp`` 404s and the right answer is the same host on 8765.
    Both are tried, in that order, and each is verified before being
    reported -- deriving a URL is a guess, and a guess only becomes an
    answer once something at the other end says it is MetaForge.
    """
    if not configured:
        return DEFAULT_CANDIDATES
    base = configured.rstrip("/")
    derived = [f"{base}/mcp"]
    parsed = urllib.parse.urlsplit(base)
    if parsed.hostname:
        port = f":{_SIDECAR_PORT}"
        derived.append(
            urllib.parse.urlunsplit(
                (parsed.scheme or "http", f"{parsed.hostname}{port}", "/mcp", "", "")
            )
        )
    ordered = [u for u in derived if u not in DEFAULT_CANDIDATES]
    return tuple(ordered) + DEFAULT_CANDIDATES


def detect_gateway(
    candidates: tuple[str, ...] | list[str] = DEFAULT_CANDIDATES,
    *,
    timeout: float = 2.0,
) -> tuple[GatewayProbe | None, list[GatewayProbe]]:
    """Probe each candidate in order; return the first usable one and all results.

    Every probe is returned, not just the winner: "nothing found" is only
    actionable if the user can see what was tried and what each said.
    """
    probes = [probe_gateway(url, timeout=timeout) for url in candidates]
    found = next((p for p in probes if p.usable), None)
    return found, probes


def describe(found: GatewayProbe | None, probes: list[GatewayProbe]) -> str:
    """The message a human reads. Never claims more than was established."""
    lines: list[str] = []
    if found is not None:
        lines.append(f"Found a MetaForge gateway at {found.url}")
        if found.server_version:
            lines.append(f"  version {found.server_version}, MCP {found.protocol}")
        if found.requires_auth:
            lines.append("  it needs credentials -- set the plugin's api_token as well")
        lines.append("")
        lines.append("Use this as the plugin's gateway URL:")
        lines.append(f"  {found.url}")
        return "\n".join(lines)

    lines.append("No MetaForge gateway found. Tried:")
    for probe in probes:
        lines.append(f"  {probe.url} -- {probe.detail or 'no answer'}")
    lines.append("")
    lines.append("Either start one locally:")
    lines.append("  docker compose up gateway")
    lines.append("or, for a team or hosted gateway, enter its URL in the plugin's settings.")
    lines.append("To work with no gateway at all, install the metaforge-local package instead.")
    return "\n".join(lines)
