"""The studio's material figures, from the material library (FORGE-880, 2026-11-15).

The formgraph engine screens and costs a part with its built-in material table (density,
stiffness ``E``, strength ``str``, thermal conductivity ``k``, price ``costKg``, flammability
``ul94``). The MetaForge material library holds sourced figures for the same materials (a
maker's technical datasheet), and none of them reached the studio.

On each studio write, when ``process.material`` names a library material, its figures are
written into ``context.machines.materials`` under the catalogue material it screens as (the
material itself, or its family: ``prusament_petg`` screens as ``petg``). The engine merges an
extra material over the catalogue's by id and keeps every field the extra does not carry,
so the library's figures replace the catalogue's and the rest stay the catalogue's:

- ``density`` from ``density_kg_m3`` (g/cm3, as the engine reads it);
- ``E`` and ``str`` from the across-layer figures when the library has them (an FDM part is
  weakest across its layers), else ``youngs_modulus_mpa`` and ``ultimate_strength_mpa``;
- what the library does not hold (``costKg``, ``k``, ``ul94``) is listed in ``defaulted`` and
  named in the write's warnings.

A material the library does not hold is left as the catalogue has it. Materials derived
earlier are rebuilt every write; materials the agent wrote are kept.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.library_materials")

MARK = "library"
#: The engine's material fields the library can fill, and the library properties for each,
#: best first.
FIELDS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("density", ("density_kg_m3",), 1 / 1000.0),
    ("E", ("youngs_modulus_across_mpa", "youngs_modulus_mpa"), 1.0),
    ("str", ("tensile_strength_across_mpa", "ultimate_strength_mpa"), 1.0),
    ("k", ("thermal_conductivity_w_mk",), 1.0),
)
#: Engine fields the library has no property for yet.
NOT_IN_LIBRARY = ("costKg", "ul94")

Lookup = Callable[[str], "dict[str, Any] | None"]


def _catalogue_id(process: str, material: str) -> str | None:
    """The catalogue material ``material`` screens as for ``process``, or None."""
    from api_gateway.imagine import engine_page
    from api_gateway.imagine.formgraph import build_page

    proc = (engine_page.catalogue().get("processes") or {}).get(process)
    if not proc:
        return None
    found = build_page.resolve_material(proc, material)
    return str(found) if found else None


def _value(props: dict[str, Any], key: str) -> tuple[float, str] | None:
    prop = props.get(key)
    if not isinstance(prop, dict):
        return None
    value = prop.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        ref = str(prop.get("source_ref") or prop.get("source_kind") or "").strip()
        return float(value), ref
    return None


def studio_material(entry: dict[str, Any], process: str, catalogue_id: str) -> dict[str, Any]:
    """The engine material for a library material, with the fields left to the catalogue."""
    raw = entry.get("properties")
    props: dict[str, Any] = raw if isinstance(raw, dict) else {}
    name = str(entry.get("name") or catalogue_id)
    out: dict[str, Any] = {"id": catalogue_id, "process": process}
    used: list[str] = []
    defaulted: list[str] = []
    refs: list[str] = []
    for field, keys, scale in FIELDS:
        found = next((v for v in (_value(props, k) for k in keys) if v is not None), None)
        if found is None:
            defaulted.append(field)
            continue
        value, ref = found
        out[field] = round(value * scale, 4)
        used.append(field)
        if ref and ref not in refs:
            refs.append(ref)
    defaulted.extend(NOT_IN_LIBRARY)
    version = entry.get("version")
    where = f"library {name} v{version}" if version is not None else f"library {name}"
    out.update(
        {
            "name": str(entry.get("title") or name),
            "source": f"{where}: {', '.join(refs) or 'source not stated'}",
            "derived_from": MARK,
            "libraryMaterial": name,
            "fromLibrary": used,
            "defaulted": sorted(set(defaulted)),
        }
    )
    return out


def derive_material(config: dict[str, Any], lookup: Lookup) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with the library's material figures in the context, and notes."""
    out = copy.deepcopy(config)
    if not isinstance(out, dict):
        return out, []
    raw = out.get("process")
    process_cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    process = str(process_cfg.get("id") or "")
    material = str(process_cfg.get("material") or "").strip()
    notes: list[str] = []
    built: list[dict[str, Any]] = []
    with tracer.start_as_current_span("imagine.library_materials") as span:
        span.set_attribute("imagine.process", process)
        if process and material:
            try:
                entry = lookup(material)
            except Exception as exc:  # noqa: BLE001 - a library read never fails the write
                span.record_exception(exc)
                logger.warning("imagine_library_material_lookup_failed", material=material)
                entry = None
            catalogue_id = _catalogue_id(process, material) if entry is not None else None
            if entry is not None and catalogue_id:
                m = studio_material(entry, process, catalogue_id)
                built.append(m)
                notes.append(
                    f'material "{material}": {", ".join(m["fromLibrary"]) or "no figures"} '
                    f"from the library; {', '.join(m['defaulted'])} stay the {process} "
                    f'catalogue\'s "{catalogue_id}" figures (price per kg, thermal '
                    "conductivity and flammability are not in the library yet)"
                )
            elif entry is not None:
                notes.append(
                    f'material "{material}" is in the library but is not a {process} material '
                    "the studio can screen; choose one of the process's materials or its family"
                )
        ctx_raw = out.get("context")
        context: dict[str, Any] = ctx_raw if isinstance(ctx_raw, dict) else {}
        block_raw = context.get("machines")
        block: dict[str, Any] = block_raw if isinstance(block_raw, dict) else {}
        own = [
            m
            for m in (block.get("materials") or [])
            if isinstance(m, dict) and m.get("derived_from") != MARK
        ]
        if built or own or "materials" in block:
            context["machines"] = {**block, "materials": [*own, *built]}
            out["context"] = context
        span.set_attribute("imagine.library_materials.built", len(built))
    return out, notes


def catalog_material(name: str) -> dict[str, Any] | None:
    """The gateway's library material of this name as a dict, or None when there is none."""
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import CatalogNotFoundError

    try:
        entry: dict[str, Any] = get_catalog().get(name, "material").as_dict()
    except CatalogNotFoundError:
        return None
    return entry
