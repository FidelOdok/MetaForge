"""One component, followed through Imagine, Develop, Check and Make (FORGE-903).

A physical part shows up in each stage as a different record: a part in the Imagine
studio, a component selection (a ``BOMItem``) and a library entry in Develop, a part
on a board in the system schematic, a requirement margin in Check, a line in a BOM
file and a line on a purchase order in Make. This module joins them into one thread
per component, so every stage can show where the same part stands in the others.

The joins, strongest first:

1. **Stored links.** A selection recorded after FORGE-903 carries
   ``specifications.thread.studio_parts``: the studio parts it fills, decided when it
   was written (or named by the caller). A concept assembly part carries
   ``studio_part``.
2. **Order code.** Two order codes are the same part when they are equal, and the
   same family when one extends the other (``AL8862`` and ``AL8862SP-13``), compared
   on letters and digits only.
3. **Role.** A selection whose role equals exactly one studio part's role.

This module does no I/O: the caller gathers the records and passes them in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Shortest order-code stem that may stand for a family; shorter ones match too much.
MIN_FAMILY = 4

RELATIONS = ("same", "refined", "replaced", "develop_only", "imagine_only")


def mpn_key(value: Any) -> str:
    """``"AL8862SP-13"`` to ``"AL8862SP13"``; empty for a placeholder or nothing."""
    text = str(value or "").strip()
    if not text or "<" in text or "not yet" in text.lower():
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def same_family(a: Any, b: Any) -> bool:
    """The same part, or one order code extending the other."""
    ka, kb = mpn_key(a), mpn_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= MIN_FAMILY and long_.startswith(short)


def role_key(value: Any) -> str:
    """``"fn:e_ldrv"`` and ``"e_ldrv"`` compare equal."""
    text = str(value or "").strip().lower()
    return text.split(":", 1)[1] if text.startswith("fn:") else text


@dataclass
class StudioPart:
    id: str
    name: str
    role: str = ""
    mpn: str = ""
    manufacturer: str = ""
    library: str = ""
    chosen: bool = True


@dataclass
class Selection:
    node_id: str
    mpn: str
    manufacturer: str = ""
    role: str = ""
    selection: str = "selected"
    studio_parts: list[str] = field(default_factory=list)
    library: dict[str, Any] | None = None
    recorded_at: str = ""
    datasheet: str = ""
    violates: list[str] = field(default_factory=list)
    margins: int = 0
    unchecked: int = 0


@dataclass
class BoardPart:
    board: str
    ref: str
    mpn: str = ""
    name: str = ""


@dataclass
class MakeLine:
    """A BOM file line or a purchase order line."""

    record: str
    kind: str  # "bom" or "procurement"
    part: str
    quantity: float = 1
    refs: list[str] = field(default_factory=list)
    text: str = ""
    board: str = ""


def link_selection(sel: Selection, parts: Iterable[StudioPart]) -> tuple[list[str], str]:
    """The studio parts a selection fills, and how that was decided."""
    parts = list(parts)
    known = {p.id for p in parts}
    stored = [p for p in sel.studio_parts if p in known]
    if stored:
        return stored, "stored"
    by_mpn = [p.id for p in parts if same_family(p.mpn, sel.mpn)]
    if by_mpn:
        # One selection can fill several studio parts (two channels, one driver).
        chosen = [pid for pid in by_mpn if next(p for p in parts if p.id == pid).chosen]
        return (chosen or by_mpn), "mpn"
    rk = role_key(sel.role)
    if rk:
        by_role = [p.id for p in parts if role_key(p.role) == rk and p.chosen]
        if len(by_role) == 1:
            return by_role, "role"
    return [], ""


def latest_selections(selections: Iterable[Selection]) -> list[Selection]:
    """One selection per role (or order code when there is no role): the newest.

    A component selection is a definition keyed by its role, so a new part for a
    role is that item's next revision; the older revisions are history.
    """
    newest: dict[str, Selection] = {}
    for sel in selections:
        key = role_key(sel.role) or f"mpn:{mpn_key(sel.mpn)}"
        prior = newest.get(key)
        if prior is None or (sel.recorded_at or "") >= (prior.recorded_at or ""):
            newest[key] = sel
    return list(newest.values())


def _relation(part: StudioPart | None, sels: list[Selection], how: str) -> str:
    if part is None:
        return "develop_only"
    if not sels:
        return "imagine_only"
    mpns = {mpn_key(s.mpn) for s in sels}
    if mpn_key(part.mpn) in mpns:
        if any(
            s.library and s.library.get("name") and s.library.get("name") != part.library
            for s in sels
        ):
            return "refined"
        return "same"
    if any(same_family(part.mpn, s.mpn) for s in sels):
        return "refined"
    return "replaced" if how in ("stored", "role") else "refined"


def _line_matches(line: MakeLine, mpn: str, board_refs: set[tuple[str, str]]) -> bool:
    if board_refs and line.refs:
        boards = {b for b, _ in board_refs}
        refs = {r for _, r in board_refs}
        if (not line.board or line.board in boards) and refs & set(line.refs):
            return True
    key = mpn_key(mpn)
    if not key:
        return False
    if same_family(line.part, mpn):
        return True
    return len(key) >= MIN_FAMILY and key in mpn_key(line.text)


def build_threads(
    *,
    studio_parts: list[StudioPart],
    studio: dict[str, Any] | None = None,
    selections: list[Selection],
    board_parts: list[BoardPart] | None = None,
    boards: list[str] | None = None,
    make_lines: list[MakeLine] | None = None,
    component_ids: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every component's thread, studio parts first in studio order.

    ``studio`` is ``{status, revision}``. ``component_ids`` maps a studio part id to
    the concept component id its handover issued.
    """
    studio = studio or {}
    board_parts = board_parts or []
    make_lines = make_lines or []
    component_ids = component_ids or {}
    sels = latest_selections(selections)

    filled: dict[str, list[Selection]] = {}
    how_by_part: dict[str, str] = {}
    loose: list[Selection] = []
    for sel in sels:
        pids, how = link_selection(sel, studio_parts)
        sel.studio_parts = pids
        if not pids:
            loose.append(sel)
        for pid in pids:
            filled.setdefault(pid, []).append(sel)
            how_by_part[pid] = how

    threads: list[dict[str, Any]] = []
    used_board_parts: set[int] = set()
    used_lines: set[int] = set()

    def develop_of(mpns: list[str]) -> list[dict[str, Any]]:
        out = []
        for i, bp in enumerate(board_parts):
            if any(same_family(bp.mpn, m) for m in mpns if m):
                used_board_parts.add(i)
                out.append({"board": bp.board, "ref": bp.ref})
        return out

    def make_of(mpns: list[str], on_boards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs = {(b["board"], b["ref"]) for b in on_boards}
        out = []
        for i, line in enumerate(make_lines):
            if any(_line_matches(line, m, refs) for m in mpns if m) or (
                refs and _line_matches(line, "", refs)
            ):
                used_lines.add(i)
                out.append(
                    {
                        "record": line.record,
                        "kind": line.kind,
                        "part": line.part,
                        "quantity": line.quantity,
                        "refs": line.refs,
                    }
                )
        return out

    def thread(
        key: str, name: str, role: str, part: StudioPart | None, psels: list[Selection], how: str
    ) -> dict[str, Any]:
        mpns = [s.mpn for s in psels] or ([part.mpn] if part else [])
        on_boards = develop_of(mpns)
        lines = make_of(mpns, on_boards)
        violates = sorted({v for s in psels for v in s.violates})
        checked = sum(s.margins for s in psels)
        unchecked = sum(s.unchecked for s in psels)
        relation = _relation(part, psels, how)
        selected = [s for s in psels if s.selection == "selected"]
        bom = [ln for ln in lines if ln["kind"] == "bom"]
        ordered = [ln for ln in lines if ln["kind"] == "procurement"]
        adopt_from = selected[0] if selected else (psels[0] if psels else None)
        return {
            "key": key,
            "name": name,
            "role": role,
            "relation": relation,
            "imagine": (
                {
                    "status": "chosen" if part.chosen else "candidate",
                    "part_id": part.id,
                    "name": part.name,
                    "mpn": part.mpn,
                    "manufacturer": part.manufacturer,
                    "library": part.library or None,
                    "component_id": component_ids.get(part.id),
                    "revision": studio.get("revision"),
                    "handed_over": studio.get("status") == "handed_over",
                }
                if part
                else {"status": "absent"}
            ),
            "develop": {
                "status": "selected" if selected else ("candidate" if psels else "absent"),
                "linked_by": how or None,
                "selections": [
                    {
                        "node_id": s.node_id,
                        "mpn": s.mpn,
                        "manufacturer": s.manufacturer,
                        "role": s.role or None,
                        "selection": s.selection,
                        "library": s.library,
                    }
                    for s in psels
                ],
                "boards": on_boards,
            },
            "check": {
                "status": (
                    "violates"
                    if violates
                    else ("pass" if checked else ("unchecked" if psels else "absent"))
                ),
                "violates": violates,
                "margins": checked,
                "unchecked": unchecked,
            },
            "make": {
                "status": "ordered" if ordered else ("in_bom" if bom else "absent"),
                "lines": lines,
            },
            "adopt": (
                {
                    "kind": "part",
                    "node_id": adopt_from.node_id,
                    "mpn": adopt_from.mpn,
                    "manufacturer": adopt_from.manufacturer,
                    "library": (adopt_from.library or {}).get("name"),
                    "datasheet": adopt_from.datasheet or None,
                    "recorded_at": adopt_from.recorded_at or None,
                    "needs_rework": studio.get("status") == "handed_over",
                }
                if adopt_from is not None and relation in ("refined", "replaced", "develop_only")
                else None
            ),
        }

    for part in studio_parts:
        psels = filled.get(part.id, [])
        if not part.chosen and not psels:
            continue  # an alternative nobody took up
        threads.append(
            thread(
                f"part:{part.id}", part.name, part.role, part, psels, how_by_part.get(part.id, "")
            )
        )
    for sel in loose:
        threads.append(
            thread(
                f"selection:{sel.node_id}",
                f"{sel.manufacturer} {sel.mpn}".strip(),
                sel.role,
                None,
                [sel],
                "",
            )
        )
    # Board parts no selection or studio part explains, grouped by order code.
    extra: dict[str, list[BoardPart]] = {}
    for i, bp in enumerate(board_parts):
        if i in used_board_parts:
            continue
        extra.setdefault(mpn_key(bp.mpn) or f"{bp.board}:{bp.ref}", []).append(bp)
    for key, bps in extra.items():
        first = bps[0]
        on_boards = [{"board": b.board, "ref": b.ref} for b in bps]
        lines = make_of([first.mpn] if first.mpn else [], on_boards)
        threads.append(
            {
                "key": f"board-part:{key}",
                "name": first.name or first.mpn or first.ref,
                "role": "",
                "relation": "develop_only",
                "imagine": {"status": "absent"},
                "develop": {
                    "status": "on_board",
                    "linked_by": None,
                    "selections": [],
                    "boards": on_boards,
                },
                "check": {"status": "absent", "violates": [], "margins": 0, "unchecked": 0},
                "make": {
                    "status": (
                        "ordered"
                        if any(ln["kind"] == "procurement" for ln in lines)
                        else ("in_bom" if lines else "absent")
                    ),
                    "lines": lines,
                },
                "adopt": None,
            }
        )
    # The boards themselves: designed in Develop, made in Make.
    for board in boards or []:
        lines = [
            {
                "record": ln.record,
                "kind": ln.kind,
                "part": ln.part,
                "quantity": ln.quantity,
                "refs": ln.refs,
            }
            for i, ln in enumerate(make_lines)
            if ln.kind == "procurement" and board.lower() in (ln.part + " " + ln.text).lower()
        ]
        threads.append(
            {
                "key": f"board:{board}",
                "name": board,
                "role": "board",
                "relation": "develop_only",
                "imagine": {"status": "absent"},
                "develop": {
                    "status": "designed",
                    "linked_by": None,
                    "selections": [],
                    "boards": [{"board": board, "ref": None}],
                },
                "check": {"status": "absent", "violates": [], "margins": 0, "unchecked": 0},
                "make": {"status": "ordered" if lines else "absent", "lines": lines},
                # A board is designed, not bought: the studio's parts are sourced
                # bought parts, so a board is shown to Imagine, never written into it.
                "adopt": None,
            }
        )
    return threads


def summary(threads: list[dict[str, Any]]) -> dict[str, int]:
    """How many threads have each relation."""
    out = {r: 0 for r in RELATIONS}
    for t in threads:
        out[t["relation"]] = out.get(t["relation"], 0) + 1
    return out
