"""Material figures for the tool call in progress (FORGE-446).

Material properties live in the library, on the gateway. Adapter
containers cannot read it, so the dispatcher (the MCP server or the
gateway's registry bridge) resolves every material a call names and puts
the figures into the call's arguments, as a ``material_properties`` block
beside each ``material`` and a ``candidate_material_properties`` map for a
``candidate_materials`` list.

:func:`extract` takes those blocks out of the arguments before the
handler sees them (so no adapter schema has to allow them) and
:func:`using` holds them for the duration of the call, where
``tool_registry.tools.cadquery.materials`` reads them. A material the
dispatcher did not resolve is simply absent, and the reader refuses it
by name: no table and no default stands in.

Layer: stdlib only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

PROPERTIES_KEY = "material_properties"
CANDIDATES_KEY = "candidate_material_properties"

_current: ContextVar[dict[str, dict[str, Any]]] = ContextVar("material_figures", default={})

__all__ = ["CANDIDATES_KEY", "PROPERTIES_KEY", "current", "extract", "key", "lookup", "using"]


def key(material: str) -> str:
    """How a material name is matched: case, spaces and hyphens ignored.

    The US and UK spellings of aluminium are one material, not a guess.
    """
    norm = material.strip().lower().replace(" ", "_").replace("-", "_")
    return norm.replace("aluminum", "aluminium")


def _add(out: dict[str, dict[str, Any]], name: Any, block: Any) -> None:
    if isinstance(name, str) and isinstance(block, dict):
        props = block.get("properties")
        if isinstance(props, dict):
            out[key(name)] = block


def extract(arguments: Any) -> dict[str, dict[str, Any]]:
    """Pop every resolved block out of ``arguments``; return them by material name."""
    out: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            block = node.pop(PROPERTIES_KEY, None)
            if block is not None:
                # Beside a "material" string, or inside a {"name": ...} material object.
                named = node.get("material")
                _add(out, named if isinstance(named, str) else node.get("name"), block)
            candidates = node.pop(CANDIDATES_KEY, None)
            if isinstance(candidates, dict):
                for name, cand in candidates.items():
                    _add(out, name, cand)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(arguments)
    return out


@contextmanager
def using(figures: dict[str, dict[str, Any]]) -> Iterator[None]:
    token = _current.set(figures)
    try:
        yield
    finally:
        _current.reset(token)


def lookup(material: str | None) -> dict[str, Any] | None:
    """The resolved block for ``material`` in this call, or ``None``."""
    if not material:
        return None
    return _current.get().get(key(material))


def current() -> dict[str, dict[str, Any]]:
    """Every block resolved for this call (a copy)."""
    return dict(_current.get())
