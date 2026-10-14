"""Resolve a material name to its library figures, and fill tool calls (FORGE-446).

The hard-coded material tables are gone; this is the one way a material
name becomes numbers. A name resolves only to a library entry it names
exactly (its identifier, title or designation, ignoring case, spaces and
hyphens). Anything else is refused with the closest entries, so a generic
"steel" or "pla" is never quietly turned into one grade: the person or the
agent picks it.

Where the library is depends on the process, so the lookup is a pluggable
*source*: the gateway installs one over its own catalog; the MCP sidecar
installs one that asks the gateway (``GET /v1/library/materials/resolve``).
With nothing installed, the library file at the default path is used.

:func:`fill_material_arguments` is what the dispatchers call before a
tool runs: every ``material`` string in the arguments gets a
``material_properties`` block beside it, and a ``candidate_materials`` list
gets a ``candidate_material_properties`` map. The adapter side reads them
(``tool_registry.material_context``).

Layer: stdlib and structlog, plus the catalog.
"""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from twin_core.library.catalog import MaterialEntry

logger = structlog.get_logger(__name__)

__all__ = [
    "MATERIAL_TOOL_PREFIXES",
    "MaterialResolutionError",
    "catalog_source",
    "fill_material_arguments",
    "install_material_source",
    "resolve_material",
]

#: Tools whose ``material`` is an engineering material to look up.
MATERIAL_TOOL_PREFIXES = ("cadquery.", "freecad.", "calculix.", "twin.")

MaterialSource = Callable[[str], Awaitable[dict[str, Any]]]

_source: MaterialSource | None = None


class MaterialResolutionError(ValueError):
    """A material name the library cannot resolve; ``candidates`` are the closest."""

    def __init__(self, name: str, candidates: list[str], reason: str = "") -> None:
        self.name = name
        self.candidates = candidates
        hint = f" Closest library materials: {', '.join(candidates)}." if candidates else ""
        super().__init__(
            reason
            or (
                f"material {name!r} is not in the material library.{hint} Name one exactly "
                "(library.list_materials), or record it with its sources "
                "(library.register_material)."
            )
        )


def _norm(text: str) -> str:
    # Case, spaces and hyphens ignored; "aluminum" and "aluminium" are one spelling.
    norm = text.strip().lower().replace(" ", "_").replace("-", "_")
    return norm.replace("aluminum", "aluminium")


def block(entry: MaterialEntry) -> dict[str, Any]:
    """The figures a tool call carries for one material."""
    return {
        "name": entry.name,
        "entry_id": entry.id,
        "title": entry.title,
        "properties": {
            k: {
                "value": p.value,
                "unit": p.unit,
                "source_kind": p.source_kind,
                "source_ref": p.source_ref,
                "needs_review": p.needs_review,
            }
            for k, p in entry.properties.items()
        },
    }


def match(entries: list[MaterialEntry], name: str) -> MaterialEntry:
    """The entry ``name`` names exactly, or :class:`MaterialResolutionError`."""
    wanted = _norm(name)
    exact = [
        e
        for e in entries
        if wanted and wanted in {_norm(e.name), _norm(e.title), _norm(e.designation)}
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise MaterialResolutionError(
            name,
            [e.name for e in exact],
            f"material {name!r} matches more than one library entry "
            f"({', '.join(e.name for e in exact)}); name the one you mean.",
        )
    labels: dict[str, str] = {}
    for e in entries:
        for label in (e.name, _norm(e.title), _norm(e.designation)):
            if label:
                labels.setdefault(label, e.name)
    close = difflib.get_close_matches(wanted, list(labels), n=8, cutoff=0.5)
    contains = [lbl for lbl in labels if wanted and (wanted in lbl or lbl in wanted)]
    candidates = list(dict.fromkeys(labels[lbl] for lbl in contains + close))[:5]
    raise MaterialResolutionError(name, candidates)


def catalog_source(get_catalog: Callable[[], Any]) -> MaterialSource:
    """A source over a catalog in this process (read each call, so swaps are seen)."""

    async def resolve(name: str) -> dict[str, Any]:
        return block(match(get_catalog().list("material"), name))

    return resolve


def install_material_source(source: MaterialSource | None) -> None:
    """Install the process's material source; ``None`` reverts to the default file."""
    global _source  # noqa: PLW0603
    _source = source


def _default_source() -> MaterialSource:
    from twin_core.library.catalog import SqliteCatalog
    from twin_core.library.machines import default_machine_library_path

    catalog = SqliteCatalog(str(default_machine_library_path()))
    return catalog_source(lambda: catalog)


async def resolve_material(name: str) -> dict[str, Any]:
    """The library figures for ``name``, or :class:`MaterialResolutionError`."""
    global _source  # noqa: PLW0603
    if _source is None:
        _source = _default_source()
    return await _source(name)


async def fill_material_arguments(tool_id: str, arguments: Any) -> list[str]:
    """Attach library figures to every material a tool call names; returns the names.

    A name the library cannot resolve raises :class:`MaterialResolutionError`,
    so the call fails with the candidates rather than running on no figures.
    Explicit figures the caller passed are left for the tool to prefer.
    """
    if not tool_id.startswith(MATERIAL_TOOL_PREFIXES) or not isinstance(arguments, dict):
        return []
    resolved: dict[str, dict[str, Any]] = {}

    async def figures(name: str) -> dict[str, Any]:
        if _norm(name) not in resolved:
            resolved[_norm(name)] = await resolve_material(name)
        return resolved[_norm(name)]

    async def walk(node: Any) -> None:
        if isinstance(node, dict):
            material = node.get("material")
            if isinstance(material, str) and material.strip() and "material_properties" not in node:
                node["material_properties"] = await figures(material)
            elif (
                isinstance(material, dict)
                and isinstance(material.get("name"), str)
                and material["name"].strip()
                and "material_properties" not in material
            ):
                # The FEA form: {"material": {"name": ...}} (explicit figures win there).
                material["material_properties"] = await figures(material["name"])
            candidates = node.get("candidate_materials")
            if isinstance(candidates, list) and "candidate_material_properties" not in node:
                node["candidate_material_properties"] = {
                    str(c): await figures(str(c)) for c in candidates if str(c).strip()
                }
            for key, value in list(node.items()):
                if key not in ("material_properties", "candidate_material_properties"):
                    await walk(value)
        elif isinstance(node, list):
            for item in node:
                await walk(item)

    await walk(arguments)
    if resolved:
        logger.info("material_arguments_filled", tool_id=tool_id, materials=sorted(resolved))
    return sorted(resolved)
