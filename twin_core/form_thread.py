"""The form thread: each studio part beside the Develop CAD item that makes it (FORGE-915).

The component thread follows bought parts from Imagine to Make; this follows form. A
studio part (a part the studio draws: a case, a lid) is paired with the Develop CAD item
that realises it, and their sizes are set side by side on the studio's axes. A size that
differs by more than the tolerance is a difference Develop made; the studio can take it
as a recorded change (``twin.imagine_accept_form``) instead of a rework and handover.

Pure: the caller reads the studio, its recorded part sizes and the CAD heads. Sizes are
boxes, not shapes: openings and mounts are not compared yet, and a row says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Millimetres a box side may differ before the row calls it a difference.
TOLERANCE_MM = 0.5

_AXES = ("x", "y", "z")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class FormPart:
    """A part the studio draws."""

    id: str
    name: str
    kind: str = "generated"
    develop_item: str = ""  # an item key the studio names for this part, if any
    accepted: dict[str, Any] | None = None  # the last difference the studio took


@dataclass(frozen=True)
class CadItem:
    """The head revision of a Develop CAD item, with its box (CAD axes, Z up)."""

    item_key: str
    revision: int
    name: str
    node_id: str
    bbox: dict[str, float] | None = None

    @property
    def ref(self) -> str:
        return f"{self.item_key}@{self.revision}"


@dataclass
class FormRow:
    key: str
    name: str
    status: str  # same | differs | accepted | unmeasured | unpaired
    studio: dict[str, Any] = field(default_factory=dict)
    develop: dict[str, Any] | None = None
    delta_mm: list[float] | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "studio": self.studio,
            "develop": self.develop,
            "delta_mm": self.delta_mm,
            "note": self.note,
        }


def cad_size_on_studio_axes(bbox: dict[str, float]) -> list[float]:
    """A CAD box (Z up) as the studio's [x, y, z] (Y up): CAD (x, y, z) is (x, z, -y)."""
    dx = float(bbox["max_x"]) - float(bbox["min_x"])
    dy = float(bbox["max_y"]) - float(bbox["min_y"])
    dz = float(bbox["max_z"]) - float(bbox["min_z"])
    return [round(dx, 3), round(dz, 3), round(dy, 3)]


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def pair(part: FormPart, items: list[CadItem]) -> CadItem | None:
    """The CAD item that makes ``part``: the one it names, else the one name that contains
    every word of the part's name (``Case base`` in ``Flight controller case base``).
    Ambiguity pairs nothing rather than guessing."""
    if part.develop_item:
        return next((i for i in items if i.item_key == part.develop_item), None)
    words = _words(part.name) or _words(part.id.replace("-", " "))
    if not words:
        return None
    found = [i for i in items if words <= _words(i.name)]
    if len(found) != 1:
        return None
    return found[0]


def build_rows(
    parts: list[FormPart],
    sizes: dict[str, list[float]],
    items: list[CadItem],
    *,
    tolerance_mm: float = TOLERANCE_MM,
) -> list[FormRow]:
    rows: list[FormRow] = []
    for part in parts:
        size = sizes.get(part.id)
        studio: dict[str, Any] = {"part_id": part.id, "size_mm": size}
        item = pair(part, items)
        if item is None:
            rows.append(
                FormRow(
                    key=part.id,
                    name=part.name,
                    status="unpaired",
                    studio=studio,
                    note="No Develop CAD item makes this part (or more than one name fits).",
                )
            )
            continue
        develop: dict[str, Any] = {"item_ref": item.ref, "node_id": item.node_id, "name": item.name}
        if not item.bbox:
            develop["size_mm"] = None
            rows.append(
                FormRow(
                    key=part.id,
                    name=part.name,
                    status="unmeasured",
                    studio=studio,
                    develop=develop,
                    note=f"{item.ref} has no validated box yet: validate it in Develop.",
                )
            )
            continue
        cad_size = cad_size_on_studio_axes(item.bbox)
        develop["size_mm"] = cad_size
        if size is None:
            rows.append(
                FormRow(
                    key=part.id,
                    name=part.name,
                    status="unmeasured",
                    studio=studio,
                    develop=develop,
                    note="The studio revision has no part sizes yet: look at it once.",
                )
            )
            continue
        delta = [round(d - s, 3) for d, s in zip(cad_size, size, strict=True)]
        differs = any(abs(d) > tolerance_mm for d in delta)
        accepted = part.accepted or {}
        if not differs:
            status = "same"
        elif accepted.get("item_ref") == item.ref:
            status = "accepted"
        else:
            status = "differs"
        note = ""
        if differs:
            sides = ", ".join(
                f"{a} {'+' if d > 0 else ''}{d:g} mm"
                for a, d in zip(_AXES, delta, strict=True)
                if abs(d) > tolerance_mm
            )
            note = f"Develop's {item.ref} differs ({sides}; studio axes, Y up)."
            if status == "accepted":
                note += f" The studio took it on {accepted.get('at', '')[:10]}."
        rows.append(
            FormRow(
                key=part.id,
                name=part.name,
                status=status,
                studio=studio,
                develop=develop,
                delta_mm=delta,
                note=note,
            )
        )
    return rows


def summary(rows: list[FormRow]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r.status] = out.get(r.status, 0) + 1
    return out


#: The only paths a form acceptance may change: the form's own numbers.
FORM_PATH = re.compile(r"^/form/params/[A-Za-z_][A-Za-z0-9_]*$")


def check_form_changes(changes: Any) -> list[dict[str, Any]]:
    """``changes`` limited to replacing form parameters with numbers; raises ``ValueError``."""
    if changes is None:
        return []
    if not isinstance(changes, list):
        raise ValueError("changes must be a list")
    out = []
    for c in changes:
        if not isinstance(c, dict) or c.get("op") != "replace":
            raise ValueError("each change replaces a form parameter: op 'replace'")
        path = str(c.get("path") or "")
        if not FORM_PATH.match(path):
            raise ValueError(f"{path!r} is not a form parameter (/form/params/<name>)")
        value = c.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} takes a number")
        out.append({"op": "replace", "path": path, "value": value})
    return out
