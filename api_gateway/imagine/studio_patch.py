"""A studio change as a list of edits, not the whole config (MetaForge, 2026-11-11).

``twin.imagine_patch_studio`` takes changes in the shape of JSON Patch (RFC 6902): ``add``,
``replace`` and ``remove``, each at a JSON Pointer ``path``. One extension: in a list of
objects that carry an ``id`` (parts, behaviours, variants, scene objects, context parts), a
path segment may name an item by its ``id`` (``/form/authored/parts/display/code``) as well
as by its index. ``-`` appends with ``add``. The changes apply in order to a copy of the
config, so the caller's config is never touched; the result is checked by the caller exactly
as a full write is.
"""

from __future__ import annotations

import copy
from typing import Any

MAX_CHANGES = 64
OPS = ("add", "replace", "remove")


class PatchError(ValueError):
    """A change that cannot be applied, named by its number (1-based) and path."""


def _segments(path: Any, where: str) -> list[str]:
    if not isinstance(path, str):
        raise PatchError(f"{where}: path must be text, a JSON Pointer such as /form/params/case_d")
    if path == "":
        raise PatchError(
            f"{where}: the path names the whole config; write the whole config with "
            "twin.imagine_put_studio instead"
        )
    if not path.startswith("/"):
        raise PatchError(f'{where}: path "{path}" must start with "/"')
    return [s.replace("~1", "/").replace("~0", "~") for s in path[1:].split("/")]


def _index(items: list[Any], seg: str, where: str, *, for_add: bool = False) -> int:
    """The position a segment names in a list: an item's id first, then an index."""
    for i, item in enumerate(items):
        if isinstance(item, dict) and str(item.get("id")) == seg:
            return i
    if for_add and seg == "-":
        return len(items)
    if seg.isdigit():
        i = int(seg)
        if i < len(items) or (for_add and i == len(items)):
            return i
        raise PatchError(f"{where}: index {seg} is past the end of a list of {len(items)}")
    ids = [str(x.get("id")) for x in items if isinstance(x, dict) and "id" in x]
    known = f"; known ids: {', '.join(ids)}" if ids else ""
    raise PatchError(f'{where}: no item "{seg}" in the list{known}')


def _apply(doc: Any, change: Any, where: str) -> str:
    if not isinstance(change, dict):
        raise PatchError(f"{where}: a change must be an object with op and path")
    op = change.get("op")
    if op not in OPS:
        raise PatchError(f"{where}: op must be one of {', '.join(OPS)}, not {op!r}")
    segs = _segments(change.get("path"), where)
    if op != "remove" and "value" not in change:
        raise PatchError(f"{where}: {op} needs a value")
    parent = doc
    walked = ""
    for seg in segs[:-1]:
        walked += "/" + seg
        if isinstance(parent, dict):
            if seg not in parent:
                raise PatchError(f'{where}: "{walked}" does not exist')
            parent = parent[seg]
        elif isinstance(parent, list):
            parent = parent[_index(parent, seg, where)]
        else:
            raise PatchError(f'{where}: "{walked}" is not an object or a list')
    last = segs[-1]
    value = copy.deepcopy(change.get("value"))
    if isinstance(parent, dict):
        if op != "add" and last not in parent:
            raise PatchError(f'{where}: "{change["path"]}" does not exist; use add to create it')
        if op == "remove":
            del parent[last]
        else:
            parent[last] = value
    elif isinstance(parent, list):
        i = _index(parent, last, where, for_add=op == "add")
        if op == "add":
            parent.insert(i, value)
        elif op == "replace":
            parent[i] = value
        else:
            del parent[i]
    else:
        raise PatchError(f'{where}: the parent of "{change["path"]}" is not an object or a list')
    return f"{op} {change['path']}"


def apply_changes(config: dict[str, Any], changes: Any) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with ``changes`` applied in order, and what each did.

    Raises :class:`PatchError` naming the first change that cannot be applied; nothing is
    applied then, because the caller keeps its own config.
    """
    if not isinstance(changes, list) or not changes:
        raise PatchError("changes must be a list of at least one change")
    if len(changes) > MAX_CHANGES:
        raise PatchError(
            f"{len(changes)} changes; at most {MAX_CHANGES} in one patch, or write the "
            "whole config with twin.imagine_put_studio"
        )
    doc = copy.deepcopy(config)
    applied = [_apply(doc, c, f"change {n}") for n, c in enumerate(changes, start=1)]
    return doc, applied
