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

**The stash is authoritative when it has an entry** (MET-684). It holds the exact
bytes the adapter produced; an inline ``step_base64`` on a call that *also* names
a ``(session_id, obj_id)`` the stash knows is a reproduction of those same bytes,
and a reproduction can only be equal or wrong. This used to be the other way
round ("explicit always wins"), which meant a single mistyped character in a
30,000-character base64 string silently replaced a pristine blob — see the class
docstring note on :class:`FillResult`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FillResult:
    """Outcome of a :meth:`GeometryStash.fill` call.

    Truthy when a blob was injected, so ``if stash.fill(args):`` reads the same
    as it did when this returned a bare bool.

    ``diverged`` is the interesting one. It means the caller supplied a
    ``step_base64`` that did **not** match the stashed export for the same
    ``(session_id, obj_id)`` — i.e. the blob was damaged between the export
    result and the commit call. Live-caught in MET-684: a committed STEP
    contained ``NAMED_URIT(*)`` where the fixed OCCT boilerplate says
    ``NAMED_UNIT(*)``, a single-character substitution that crashed the OCCT
    converter on read. One wrong base64 character reproduces exactly that
    one-byte change with both neighbouring bytes intact, so the damage happened
    while the blob was base64 *text* being carried between two tool calls —
    not to raw bytes in a buffer.
    """

    injected: bool
    diverged: bool = False
    stashed_chars: int = 0
    supplied_chars: int = 0

    def __bool__(self) -> bool:
        return self.injected


class GeometryStash:
    """Bounded LRU of authored STEP blobs, keyed by (session_id, obj_id)."""

    def __init__(self, max_entries: int = 32) -> None:
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._max = max_entries

    def remember(self, arguments: dict[str, Any], result: dict[str, Any]) -> bool:
        """Cache the STEP from a ``freecad.export_model`` result.

        Handles both the raw handler return and the ``result["data"]`` envelope.
        Returns True if a blob was actually cached, so callers can log a miss
        (e.g. the export returned no step_base64, or session_id/obj_id was
        missing) instead of it silently vanishing -- see MET-642 S4 finding.
        """
        if not isinstance(result, dict):
            return False
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
            return True
        return False

    def fill(self, arguments: dict[str, Any]) -> FillResult:
        """Fill a commit_geometry call's ``step_base64`` from a prior export.

        A stash hit wins over an inline ``step_base64`` (MET-684). The stashed
        value came straight off the adapter; a value carried back in through a
        tool call is a copy of it, so when the two differ the copy is the
        damaged one. Substituting the pristine blob is therefore always at
        least as correct as honouring the copy, and the caller is told via
        ``diverged`` so the damage is visible rather than silently repaired.

        A **miss** leaves an explicit ``step_base64`` completely alone: geometry
        produced some other way (a raw CadQuery script, an upload) has no
        stash entry and must still commit.
        """
        sid, oid = arguments.get("session_id"), arguments.get("obj_id")
        supplied = arguments.get("step_base64")
        supplied_str = supplied if isinstance(supplied, str) else ""

        if not (sid and oid):
            return FillResult(injected=False, supplied_chars=len(supplied_str))

        blob = self._cache.get((str(sid), str(oid)))
        if not blob:
            return FillResult(injected=False, supplied_chars=len(supplied_str))

        if supplied_str and supplied_str == blob:
            # Faithfully reproduced. Nothing to do, and nothing to report.
            return FillResult(
                injected=False,
                stashed_chars=len(blob),
                supplied_chars=len(supplied_str),
            )

        arguments["step_base64"] = blob
        return FillResult(
            injected=True,
            diverged=bool(supplied_str),
            stashed_chars=len(blob),
            supplied_chars=len(supplied_str),
        )
