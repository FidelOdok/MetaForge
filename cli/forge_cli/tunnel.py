"""Expose a local gateway to a cloud harness, safely (FORGE-387).

I2a: "serve a local gateway to cloud harnesses via an off-the-shelf
tunnel, **with all controls in the local MCP server**". ChatGPT and
claude.ai cannot reach `localhost`; a tunnel is how a hosted harness
talks to a gateway on someone's machine.

The tunnel itself is not ours -- this shells out to `cloudflared`, which
already does TLS, reconnection and a public hostname better than
anything worth writing here. What is ours is refusing to open one over a
gateway that is not ready to be reachable from the internet.

**The pre-flight is the point of this module.** A tunnel does not change
what the server enforces; it changes who can reach it. A gateway running
in open mode is fine on a laptop and is an unauthenticated write
endpoint on the public internet, and the moment between "it worked
locally" and "it is public" is exactly where nobody re-checks.

stdlib only, so it runs before anything else is set up.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from cli.forge_cli.discover import GatewayProbe, probe_gateway

__all__ = [
    "TUNNEL_COMMANDS",
    "PreflightResult",
    "preflight",
    "tunnel_command",
]

#: Off-the-shelf tunnels, in the order we look for them. Each maps to the
#: argv that forwards a public HTTPS hostname to a local port.
TUNNEL_COMMANDS: dict[str, list[str]] = {
    "cloudflared": ["cloudflared", "tunnel", "--url"],
    "ngrok": ["ngrok", "http"],
}


@dataclass
class PreflightResult:
    """Whether this gateway is fit to be reachable from the internet."""

    ok: bool
    probe: GatewayProbe | None = None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines: list[str] = []
        if self.blockers:
            lines.append("Refusing to open a tunnel:")
            lines += [f"  - {b}" for b in self.blockers]
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return "\n".join(lines)


def preflight(url: str, *, timeout: float = 3.0) -> PreflightResult:
    """Check a gateway before making it public.

    Blocks, rather than warns, on the one combination that must be
    impossible to reach by accident: a reachable gateway that accepts
    every connection. Everything else the server still enforces on its
    own -- the tunnel does not weaken the write gate, it just widens who
    can knock.
    """
    probe = probe_gateway(url, timeout=timeout)
    blockers: list[str] = []
    warnings: list[str] = []

    if not probe.reachable:
        blockers.append(
            f"nothing is answering at {url} ({probe.detail or 'no answer'}) -- "
            "start the gateway first, or run `forge connect` to find it"
        )
        return PreflightResult(False, probe, blockers, warnings)

    if not probe.usable:
        blockers.append(
            f"{url} answered, but not as a MetaForge gateway ({probe.detail}) -- "
            "tunnelling it would publish somebody else's service"
        )
        return PreflightResult(False, probe, blockers, warnings)

    if not probe.requires_auth:
        # The gateway let an unauthenticated probe complete an MCP
        # handshake. On a laptop that is the default and harmless; on the
        # public internet it is an open write endpoint.
        blockers.append(
            "this gateway accepts unauthenticated connections. On your own "
            "machine that is fine; through a tunnel it is an open endpoint "
            "on the internet. Set METAFORGE_MCP_API_KEY (or configure OAuth) "
            "and restart it before exposing it."
        )

    warnings.append(
        "writes from a tunnelled caller are held for approval -- answer them "
        "in the dashboard, or from a client that supports MCP elicitation"
    )
    return PreflightResult(not blockers, probe, blockers, warnings)


def tunnel_command(port: int, *, prefer: str | None = None) -> tuple[str, list[str]] | None:
    """The argv for whichever tunnel is installed, or None if none is.

    Not bundled and not installed for the user: a binary that opens a
    public hostname is something they should have chosen to have.
    """
    names = [prefer] if prefer else list(TUNNEL_COMMANDS)
    for name in names:
        argv = TUNNEL_COMMANDS.get(name or "")
        if argv and shutil.which(argv[0]):
            return name or "", [*argv, f"http://localhost:{port}"]
    return None
