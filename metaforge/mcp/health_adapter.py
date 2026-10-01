"""Health as a tool, not only a protocol method (FORGE-409).

``/metaforge:doctor`` told the agent to call ``health/check``, ``tools/list``
and ``resources/list``. Those are JSON-RPC *methods*. A harness exposes
*tools*, so the agent cannot call them — in the FORGE-409 test it reached for
Bash and curl instead, which means doctor worked only where a shell was
available and not at all in Codex, ChatGPT or claude.ai.

The protocol method stays (transports and the gateway use it). This adds a
read-only ``health.check`` tool and a ``metaforge://health`` resource over the
same report, so the diagnostic an agent is told to run is one it can actually
reach.

Owned by :class:`~metaforge.mcp.server.UnifiedMcpServer` rather than a
domain adapter: the report is about the whole server, including which
adapters answered, and no single adapter can see that.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceManifestEntry, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)

__all__ = ["HEALTH_RESOURCE_URI", "HealthServer"]

HEALTH_RESOURCE_URI = "metaforge://health/connection"

#: Returns the same report the ``health/check`` protocol method does.
HealthReporter = Callable[[], Awaitable[dict[str, Any]]]


class HealthServer(McpToolServer):
    """Exposes the server's own health report as a tool and a resource."""

    def __init__(self, reporter: HealthReporter) -> None:
        super().__init__(adapter_id="health", version="0.1.0")
        self._reporter = reporter

        self.register_tool(
            manifest=ToolManifest(
                tool_id="health.check",
                adapter_id="health",
                name="Check this MetaForge connection",
                description=(
                    "Diagnose this connection: overall status, which tool adapters "
                    "answered and which did not, auth posture, negotiated protocol "
                    "version, server version and whether dashboard deep links are "
                    "configured.\n\n"
                    "Read `status` and `unreachable_adapters` rather than inferring "
                    "health from the length of the tool list: a registered adapter that "
                    "did not answer still contributes its tools to `tools_registered`, "
                    "and `reachable` is the field that says whether those tools can "
                    "actually be called."
                ),
                capability="diagnostics",
                input_schema={"type": "object", "properties": {}},
            ),
            handler=self.check,
        )

        self.register_resource(
            manifest=ResourceManifestEntry(
                uri_template=HEALTH_RESOURCE_URI,
                adapter_id="health",
                name="Connection health",
                description=(
                    "This MetaForge connection's health as markdown. Re-read it rather "
                    "than caching: an adapter that was down may have come back."
                ),
                mime_type="text/markdown",
            ),
            reader=self._read,
            matcher=lambda uri: uri == HEALTH_RESOURCE_URI,
        )

    async def check(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments  # the report takes no parameters
        report: dict[str, Any] = await self._reporter()
        return report

    async def _read(self, uri: str) -> list[dict[str, Any]]:
        # A *list* of content blocks. Returning the bare dict makes the
        # dispatcher's `list(contents)` yield the dict's keys -- a valid
        # JSON-RPC result containing ["uri", "mimeType", "text"] and no
        # content at all. It fails silently, so it is worth the type hint.
        report = await self._reporter()
        return [
            {
                "uri": uri,
                "mimeType": "text/markdown",
                "text": render_health_markdown(report),
            }
        ]


def render_health_markdown(report: dict[str, Any]) -> str:
    """The health report as text, for a client reading resources.

    Leads with what is wrong. A reader who stops after two lines should still
    know whether anything is broken, which is not true of a report that opens
    with a version banner.
    """
    status = str(report.get("status", "unknown"))
    lines = [f"# MetaForge connection: **{status}**", ""]

    unreachable = report.get("unreachable_adapters") or []
    if unreachable:
        lines.append(f"**{len(unreachable)} adapter(s) did not answer**: {', '.join(unreachable)}")
        lines.append("")
        lines.append(
            "> Their tools are still listed and will fail when called. A shorter tool "
            "list is not the symptom; this is."
        )
        lines.append("")
    if report.get("detail"):
        lines.append(str(report["detail"]))
        lines.append("")

    for key, label in (
        ("version", "Server version"),
        ("protocol_version", "Protocol"),
        ("tool_count", "Tools registered"),
        ("adapter_count", "Adapters registered"),
        ("dashboard_links", "Dashboard links"),
    ):
        if key in report:
            lines.append(f"- **{label}**: {report[key]}")

    auth = report.get("auth")
    if isinstance(auth, dict):
        lines.append(f"- **Auth**: {auth.get('mode', 'unknown')}")
    client = report.get("client")
    if isinstance(client, dict) and client.get("name"):
        lines.append(f"- **Client**: {client.get('name')} {client.get('version', '')}".rstrip())

    adapters = report.get("adapters")
    if isinstance(adapters, list) and adapters:
        lines.append("")
        lines.append("## Adapters")
        lines.append("")
        for entry in adapters:
            if not isinstance(entry, dict):
                continue
            reachable = entry.get("reachable")
            mark = "ok" if reachable else "DOWN" if reachable is False else "unknown"
            line = f"- `{entry.get('adapter_id', '?')}` — {mark}"
            if entry.get("error"):
                line += f" ({entry['error']})"
            lines.append(line)
    return "\n".join(lines)


def health_payload(report: dict[str, Any]) -> str:
    """JSON form, for logs and tests."""
    return json.dumps(report, sort_keys=True, default=str)
