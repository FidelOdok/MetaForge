"""A component in the form is the real part, not a stand-in.

An authored part of kind ``component`` names its part in ``context.components.parts``
with ``component_ref``, and says where it sits with ``at`` ``[x, y, z]`` (and optionally
``rot`` ``[rx, ry, rz]`` in degrees). Given no code of its own, the studio writes the
drawing code from that part's size, so the shape in the form and the sourced part are one
thing and cannot drift apart: every write derives the code again from the part as it is
now. A part that carries its own code keeps it, because the agent chose to draw it; so
does a derived part whose code the agent has rewritten into anything but the derived box.

The form is y up, and a part's envelope is ``[length, width, height]`` with height up, so
the box drawn is ``g.box(length, height, width)``. It is the part's bounding box, which is
what a part's ``fidelity`` already says.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

MARK = "component_ref"

# The only code this module writes: one box, maybe turned, then placed. A derived part
# whose code no longer has this form was redrawn by the agent (MetaForge, 2026-11-11).
# A real part whose library entry has a model is drawn with it (2026-11-12): the derived
# code is then one g.model call, maybe turned, then placed.
_DERIVED_CODE = re.compile(
    r'^return \[g\.(box\([^()]*\)|model\("[A-Za-z0-9_.-]+"\))'
    r"(\.rot\([^()]*\))?\.at\([^()]*\)\];$"
)


def expand_component_parts(
    config: dict[str, Any], *, has_model: Callable[[str], bool] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of ``config`` with each linked component's code derived, and a note each.

    ``has_model(name)`` says whether the component library entry ``name`` carries a model.
    A real part linked to such an entry (its ``library`` field) is drawn with
    ``g.model(name)``; otherwise it is drawn as its bounding box.
    """
    out = copy.deepcopy(config)
    form = out.get("form") if isinstance(out, dict) else None
    block = form.get("authored") if isinstance(form, dict) else None
    parts = block.get("parts") if isinstance(block, dict) else None
    notes: list[str] = []
    if not isinstance(parts, list):
        return out, notes
    context_parts = _context_parts(out)
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get(MARK), str):
            continue
        own_code = isinstance(part.get("code"), str) and part.get("code", "").strip()
        if own_code and part.get("derived_from") != MARK:
            continue  # the agent drew it itself
        if own_code and not _DERIVED_CODE.match(part["code"].strip()):
            # Marked as derived but redrawn since (the agent read the part back with its
            # mark and wrote its own shape): the drawing is the agent's now, keep it.
            del part["derived_from"]
            continue
        real = context_parts.get(part[MARK])
        size = _size(real)
        place = _vector(part.get("at", [0, 0, 0]))
        rot = _vector(part["rot"]) if "rot" in part else None
        if size is None or place is None or ("rot" in part and rot is None):
            continue  # the checker says what is wrong
        length, width, height = size
        entry = str((real or {}).get("library") or "").strip()
        model = bool(entry) and has_model is not None and _safe(has_model, entry)
        if model:
            shape = f'g.model("{entry}")'
        else:
            shape = f"g.box({_n(length)}, {_n(height)}, {_n(width)})"
        if rot is not None:
            shape += f".rot({', '.join(_n(v) for v in rot)})"
        shape += f".at({', '.join(_n(v) for v in place)})"
        part["code"] = f"return [{shape}];"
        part["derived_from"] = MARK
        part["uses"] = []
        if model:
            notes.append(
                f'part "{part.get("id")}": drawn with library model {entry} '
                f'for context part "{part[MARK]}"'
            )
            continue
        fidelity = (real or {}).get("geometry", {}).get("fidelity") or "not stated"
        notes.append(
            f'part "{part.get("id")}": drawn from context part "{part[MARK]}" '
            f"({_n(length)} x {_n(width)} x {_n(height)} mm, fidelity {fidelity})"
        )
    return out, notes


_ENTRY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")


def _safe(has_model: Callable[[str], bool], entry: str) -> bool:
    """Whether to draw with the model: a usable name and a lookup that answers yes."""
    if not _ENTRY_NAME.fullmatch(entry):
        return False
    try:
        return bool(has_model(entry))
    except Exception:
        return False  # the library could not be read: the box still draws


def _context_parts(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = config.get("context")
    components = context.get("components") if isinstance(context, dict) else None
    parts = components.get("parts") if isinstance(components, dict) else None
    if not isinstance(parts, list):
        return {}
    return {str(p["id"]): p for p in parts if isinstance(p, dict) and p.get("id")}


def _size(part: dict[str, Any] | None) -> tuple[float, float, float] | None:
    env = ((part or {}).get("geometry") or {}).get("envelope")
    if (
        isinstance(env, list)
        and len(env) == 3
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in env)
    ):
        return float(env[0]), float(env[1]), float(env[2])
    return None


def _vector(value: Any) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    ):
        return [float(x) for x in value]
    return None


def _n(value: float) -> str:
    return format(round(float(value), 4), "g")
