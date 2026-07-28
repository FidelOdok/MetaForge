"""Commit-by-reference geometry stash (MET-10).

An agent authors geometry over MCP, calls ``freecad.export_model`` (which returns
a multi-KB base64 STEP), then must call ``twin.commit_geometry`` with that blob —
but agents can't reliably thread a large value between tool calls, so the commit
arrives with an empty ``step_base64`` and fails.

This stash remembers each export's STEP keyed by ``(session_id, obj_id)`` so
``twin.commit_geometry`` can be called *by reference* — with just those small ids
— and the blob filled in at the dispatch seam. It lives here (stdlib-only, no
deps) so every MCP dispatch path can share it: the unified sidecar server and the
in-process registry bridge the gateway chat uses.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class GeometryStash:
    """Bounded LRU of authored STEP blobs, keyed by (session_id, obj_id)."""

    def __init__(self, max_entries: int = 32) -> None:
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._max = max_entries

    def remember(self, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        """Cache the STEP from a ``freecad.export_model`` result.

        Handles both the raw handler return and the ``result["data"]`` envelope.
        """
        if not isinstance(result, dict):
            return
        data = result.get("data")
        payload = data if isinstance(data, dict) else result
        sid, oid = arguments.get("session_id"), arguments.get("obj_id")
        blob = payload.get("step_base64")
        if sid and oid and isinstance(blob, str) and blob:
            key = (str(sid), str(oid))
            self._cache[key] = blob
            self._cache.move_to_end(key)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)

    def fill(self, arguments: dict[str, Any]) -> bool:
        """Fill a commit_geometry call's ``step_base64`` from a prior export.

        Explicit ``step_base64`` always wins. Returns True if a blob was injected.
        """
        if arguments.get("step_base64"):
            return False
        sid, oid = arguments.get("session_id"), arguments.get("obj_id")
        if not (sid and oid):
            return False
        blob = self._cache.get((str(sid), str(oid)))
        if blob:
            arguments["step_base64"] = blob
            return True
        return False
