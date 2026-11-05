"""Component ids from STEP product names into GLB node extras (SYNC-001, SYNC-002).

The gateway writes a CAD revision's component id into its STEP product names
as ``"<name> [CMP-...]"`` (``twin_core.component_identity.
stamp_step_component_ids``; the rule is mirrored here because the converter is
its own image and imports nothing from MetaForge). The converter splits such a
name into the part name it shows and the component id, and writes the id into
the GLB node's ``extras``, which three.js reads as ``userData``. The viewer
then selects a part of a multi-part model by its component, not by its name.

Pure stdlib.
"""

from __future__ import annotations

import json
import re
import struct
from typing import Any

_STEP_NAME = re.compile(r"^(?P<name>.*?)\s*\[(?P<cid>CMP-[A-Za-z0-9-]+)\]\s*$")
_BARE_ID = re.compile(r"^CMP-[A-Za-z0-9-]+$")
_MAGIC = b"glTF"
_JSON = 0x4E4F534A


def split_product_name(name: str | None) -> tuple[str, str | None]:
    """``"Base [CMP-base]"`` to ``("Base", "CMP-base")``; a plain name has no id."""
    text = str(name or "").strip()
    found = _STEP_NAME.match(text)
    if found:
        return found.group("name").strip() or found.group("cid"), found.group("cid")
    if _BARE_ID.match(text):
        return text, text
    return text, None


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def add_node_extras(glb: bytes, extras_by_node: dict[str, dict[str, Any]]) -> tuple[bytes, int]:
    """Merge ``extras`` into the GLB nodes named in ``extras_by_node``.

    Only the JSON chunk changes; the binary chunk is copied as it is. Returns
    ``(glb, nodes changed)``. A GLB that cannot be read is returned unchanged.
    """
    if not extras_by_node or len(glb) < 20 or glb[:4] != _MAGIC:
        return glb, 0
    json_len, kind = struct.unpack_from("<II", glb, 12)
    if kind != _JSON or 20 + json_len > len(glb):
        return glb, 0
    try:
        doc = json.loads(glb[20 : 20 + json_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return glb, 0
    changed = 0
    for node in doc.get("nodes") or []:
        extra = extras_by_node.get(str(node.get("name") or ""))
        if not extra:
            continue
        merged = dict(node.get("extras") or {})
        merged.update(extra)
        node["extras"] = merged
        changed += 1
    if not changed:
        return glb, 0
    body = _pad(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    rest = glb[20 + json_len :]
    total = 12 + 8 + len(body) + len(rest)
    header = struct.pack("<4sII", _MAGIC, 2, total)
    return header + struct.pack("<II", len(body), _JSON) + body + rest, changed


__all__ = ["add_node_extras", "split_product_name"]
