"""Where in the dashboard to look at what a tool just did (FORGE-371).

An agent that says "I committed the bracket" is asking the reader to go
and find it. The reader has a dashboard open and no idea which of thirteen
pages, which project, which node. A link closes that gap, and the server
is the only party that knows both the id and the route.

Two rules the rest of this file exists to keep.

**Never invent the base URL.** The MCP server does not know where the
dashboard is served from; only the deployment does, via
``METAFORGE_DASHBOARD_URL``. With none configured this emits nothing at
all. A guessed ``http://localhost:5173`` is worse than no link: the agent
states it with the same confidence either way, and the reader finds out it
is wrong by clicking it.

**Never invent the route.** A link is a claim that a page will show the
thing. Only mappings verified against ``dashboard/src/App.tsx`` and the
page's own parameter handling are listed here; a tool with no entry gets
no link rather than a plausible-looking one.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

__all__ = [
    "DeepLink",
    "DeepLinkBuilder",
    "links_for",
]


@dataclass(frozen=True)
class DeepLink:
    """One place to look, and what the reader will see there."""

    #: Absolute URL, ready to paste.
    url: str
    #: Short human label, e.g. "Open the part in the twin viewer".
    label: str


#: tool id → (route, which result field carries the id, query key, label).
#:
#: ``None`` for the id field means the route needs no id. The query key is
#: what the page actually reads: ``/twin`` reads ``?node=`` (MET-514) and
#: ``?tab=`` (FORGE-371), ``/runs/new`` reads ``?project=``. Pages that read
#: nothing are linked bare, which is still better than a page name in prose.
_ROUTES: dict[str, tuple[str, str | None, str | None, str]] = {
    # Geometry and structure land in the twin viewer, on the tab that shows
    # the thing that changed.
    "twin.commit_geometry": ("twin?tab=model", "node_id", "node", "View the part in the twin"),
    "twin.commit_design_sketch": ("twin?tab=model", "node_id", "node", "View the sketch"),
    "twin.commit_system_architecture": (
        "twin?tab=structure",
        "node_id",
        "node",
        "View the architecture in Structure",
    ),
    "twin.get_node": ("twin", "node_id", "node", "View the node in the twin"),
    # Requirements, evidence matrix and the gate all live on /requirements.
    "twin.record_constraint_set": ("requirements", None, None, "View the requirements matrix"),
    "twin.attempt_promotion": ("requirements", None, None, "View the gate review"),
    # Simulation.
    "calculix.run_fea": ("sim", None, None, "View the simulation results"),
    "calculix.run_thermal": ("sim", None, None, "View the simulation results"),
    # Projects and sessions.
    "project.open": ("projects", "id", None, "Open the project"),
    "project.create": ("projects", "id", None, "Open the project"),
    "session.start": ("sessions", "session_id", None, "Follow the session"),
}


def _dig(payload: Any, field: str) -> str | None:
    """Find ``field`` in a tool result, enveloped or not.

    Adapter results come back as ``{tool_id, status, data: {...}}``; the
    id is usually in ``data``, sometimes at the top level, and for
    ``project.*`` inside a nested ``project``.
    """
    if not isinstance(payload, dict):
        return None
    for candidate in (payload.get("data"), payload.get("project"), payload):
        if not isinstance(candidate, dict):
            continue
        value = candidate.get(field)
        if isinstance(value, str) and value:
            return value
        nested = candidate.get("project")
        if isinstance(nested, dict):
            value = nested.get(field)
            if isinstance(value, str) and value:
                return value
    return None


class DeepLinkBuilder:
    """Builds dashboard URLs, or nothing when it has no base to build on."""

    def __init__(self, base_url: str | None) -> None:
        self._base = base_url.rstrip("/") if base_url and base_url.strip() else None

    @property
    def configured(self) -> bool:
        return self._base is not None

    def build(self, route: str, params: dict[str, str] | None = None) -> str | None:
        if self._base is None:
            return None
        url = f"{self._base}/{route.lstrip('/')}"
        if params:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}{urlencode(params, quote_via=quote)}"
        return url

    def for_tool(self, tool_id: str, result: Any) -> list[DeepLink]:
        """Links for one tool result. Empty when there is nothing honest to say."""
        if self._base is None:
            return []
        entry = _ROUTES.get(tool_id)
        if entry is None:
            return []
        route, id_field, query_key, label = entry
        params: dict[str, str] = {}
        identifier = _dig(result, id_field) if id_field else None
        if id_field and identifier is None:
            # The tool ran but the result did not carry the id this route
            # needs. Linking the bare page would point at whatever happens
            # to be selected, which is a different object.
            return []
        if identifier is not None:
            if query_key:
                params[query_key] = identifier
            else:
                route = f"{route}/{quote(identifier)}"
        url = self.build(route, params or None)
        return [DeepLink(url=url, label=label)] if url else []


def links_for(builder: DeepLinkBuilder | None, tool_id: str, result: Any) -> list[dict[str, str]]:
    """``_meta.links`` for a tool result: a list of ``{url, label}``."""
    if builder is None:
        return []
    return [{"url": link.url, "label": link.label} for link in builder.for_tool(tool_id, result)]
