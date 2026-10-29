"""Processes and materials as library data (FORGE-451, FORGE-446).

The machine library says what a person's machines can do. This says what a
manufacturing *technique* can do (its design rules and achievable tolerance)
and what a *material* is (its properties), so process ranking, the DFM review
and the flow generator read them instead of re-sourcing them every run.

Two entry kinds, one store:

* a **process** ("cnc_milling", "fdm", "laser_cutting") with its family, the
  machine kinds that run it, the material families it can make, and its
  rules: limits in exactly the vocabulary ``check_manufacturability`` reads
  (wall thickness, hole diameter, inside corner and bend radii, draft,
  overhang, feature size, envelope) plus achievable tolerance. A rule may
  apply only to one material family (aluminium walls differ from ABS).
* a **material** ("aluminium_6061_t6") with its family, designation, forms
  and typed properties: density, stiffness, strengths, elongation, thermal
  figures and cost per kg, with the orthotropic pair for woods and sheet
  goods.

The rules of the machine library hold here too: every value has a fixed
unit and a source; ``standard``, ``datasheet``, ``manufacturer_spec``,
``supplier`` and ``measurement`` need a reference; an ``assumption`` is
flagged for review; nothing is defaulted; every change keeps the previous
version. No property or rule ships built in: entries are recorded with
their sources.

FORGE-631 adds a fourth kind, an **appearance**: a colour, material and
finish (CMF) specification an industrial designer picks for a part: the
finish ("anodised", "powder_coated", "moulded_in_colour"), a colour named in
a colour system (a RAL or Pantone code, or a supplier's own name) with the
sRGB value used to show it on screen and its source, a texture standard
reference, measurable surface properties (gloss, coating thickness,
roughness) and the material and process names it applies to.

FORGE-635 adds a fifth, an **ergonomic limit**: a design limit for a
feature people touch, press, reach or lift (the same kinds and units the
``check_ergonomics`` skill reads), with what it applies to, the population
it describes, and its source (a public-domain standard or survey, cited to
the section or table). The industrial design phase passes them to that
skill; none is built in.

Layer: stdlib and structlog (twin_core may import nothing above it).
"""

from __future__ import annotations

import builtins
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from twin_core.library.licences import ACCEPTED_LICENCES, is_accepted, normalise_licence
from twin_core.library.machines import MACHINE_KINDS

logger = structlog.get_logger(__name__)

__all__ = [
    "MATERIAL_FAMILIES",
    "MATERIAL_FORMS",
    "MATERIAL_PROPERTIES",
    "PROCESS_FAMILIES",
    "RULE_KINDS",
    "SOURCE_KINDS",
    "CatalogError",
    "CatalogNotFoundError",
    "SqliteCatalog",
    "ERGONOMIC_KINDS",
    "ErgonomicLimitEntry",
    "summarize_ergonomic_limit",
    "APPEARANCE_FINISHES",
    "APPEARANCE_PROPERTIES",
    "COLOUR_SYSTEMS",
    "AppearanceEntry",
    "summarize_appearance",
    "COMPONENT_FAMILIES",
    "COMPONENT_ORIGINS",
    "COMPONENT_SPECS",
    "ComponentEntry",
    "summarize_component",
    "summarize_material",
    "summarize_process",
]

SOURCE_KINDS: tuple[str, ...] = (
    "measurement",
    "standard",
    "datasheet",
    "manufacturer_spec",
    "supplier",
    "user",
    "assumption",
)
_NEEDS_REF = ("measurement", "standard", "datasheet", "manufacturer_spec", "supplier")

PROCESS_FAMILIES: tuple[str, ...] = (
    "additive",
    "subtractive",
    "cutting",
    "forming",
    "joining",
    "moulding",
    "casting",
    "pcb_fabrication",
    "pcb_assembly",
    "finishing",
    "other",
)
MATERIAL_FAMILIES: tuple[str, ...] = (
    "metal",
    "polymer",
    "filament",
    "resin",
    "wood",
    "sheet_good",
    "composite",
    "laminate",
    "ceramic",
    "elastomer",
    "other",
)
MATERIAL_FORMS: tuple[str, ...] = (
    "sheet",
    "plate",
    "bar",
    "rod",
    "tube",
    "extrusion",
    "block",
    "filament",
    "resin",
    "powder",
    "pellet",
    "board",
    "other",
)

#: FORGE-449: mechanical COTS part families.
COMPONENT_FAMILIES: tuple[str, ...] = (
    "screw",
    "nut",
    "washer",
    "bearing",
    "bushing",
    "standoff",
    "pin",
    "retaining_ring",
    "shaft_collar",
    "linear_rail",
    "linear_bearing",
    "profile_extrusion",
    "hinge",
    "slide",
    "gear",
    "pulley",
    "belt",
    "spring",
    "actuator",
    "motor",
    "other",
)
#: "standard": dimensions from a standard's table, geometry generated from them;
#: "purchased": a supplier's part with its own CAD.
COMPONENT_ORIGINS: tuple[str, ...] = ("standard", "purchased")

#: Typed component specs; a key outside this table is refused.
COMPONENT_SPECS: dict[str, str] = {
    "nominal_diameter_mm": "mm",
    "thread_pitch_mm": "mm",
    "length_mm": "mm",
    "head_diameter_mm": "mm",
    "head_height_mm": "mm",
    "socket_size_mm": "mm",
    "socket_depth_mm": "mm",
    "width_across_flats_mm": "mm",
    "height_mm": "mm",
    "inner_diameter_mm": "mm",
    "outer_diameter_mm": "mm",
    "thickness_mm": "mm",
    "bore_mm": "mm",
    "width_mm": "mm",
    "dynamic_load_kn": "kN",
    "static_load_kn": "kN",
    "fatigue_load_limit_kn": "kN",
    "reference_speed_rpm": "rpm",
    "limiting_speed_rpm": "rpm",
    "mass_kg": "kg",
    "travel_mm": "mm",
    "force_n": "N",
    "torque_nm": "N m",
    "voltage_v": "V",
}

#: FORGE-631: how a surface is finished.
APPEARANCE_FINISHES: tuple[str, ...] = (
    "as_made",
    "painted",
    "powder_coated",
    "anodised",
    "plated",
    "moulded_in_colour",
    "printed",
    "textured",
    "polished",
    "brushed",
    "bead_blasted",
    "other",
)
#: Where a colour's name comes from. A screen sRGB of a RAL or Pantone code
#: is an approximation and is sourced like any other value.
COLOUR_SYSTEMS: tuple[str, ...] = ("ral", "pantone", "ncs", "supplier", "custom")

#: Measurable surface properties, plus the two render parameters (which
#: describe how it is drawn, not the part, so they are usually a user's
#: choice or an assumption). Each has one unit; ``-`` values are 0..1.
APPEARANCE_PROPERTIES: dict[str, str] = {
    "gloss_gu_60": "GU",
    "coating_thickness_um": "um",
    "surface_roughness_ra_um": "um",
    "render_roughness": "-",
    "render_metallic": "-",
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: FORGE-635: the feature kinds an ergonomic limit can bound, each with its
#: one unit. Must match ``check_ergonomics``' ``UNITS`` (a test pins it).
ERGONOMIC_KINDS: dict[str, str] = {
    "grip_diameter": "mm",
    "grip_width": "mm",
    "handle_length": "mm",
    "finger_clearance": "mm",
    "button_size": "mm",
    "actuation_force": "N",
    "operating_height": "mm",
    "reach_distance": "mm",
    "product_mass": "kg",
}

#: Rule kinds, the same vocabulary as check_manufacturability, plus tolerance.
#: Each has one unit; a rule in another unit is refused.
RULE_KINDS: dict[str, str] = {
    "wall_thickness": "mm",
    "hole_diameter": "mm",
    "inside_corner_radius": "mm",
    "bend_radius": "mm",
    "draft_angle": "deg",
    "overhang_angle": "deg",
    "feature_size": "mm",
    "envelope": "mm",
    "tolerance": "mm",
}


@dataclass(frozen=True)
class _Prop:
    unit: str
    description: str


#: Material properties the library knows. ``cost_per_kg`` carries its
#: currency in the unit ("GBP/kg"); everything else has one fixed unit.
MATERIAL_PROPERTIES: dict[str, _Prop] = {
    "density_kg_m3": _Prop("kg/m3", "density"),
    "youngs_modulus_mpa": _Prop("MPa", "elastic modulus (along the grain for wood)"),
    "youngs_modulus_across_mpa": _Prop("MPa", "elastic modulus across the grain"),
    "poisson_ratio": _Prop("-", "Poisson's ratio"),
    "yield_strength_mpa": _Prop("MPa", "yield (0.2% proof) strength"),
    "ultimate_strength_mpa": _Prop("MPa", "ultimate tensile strength"),
    "yield_strength_across_mpa": _Prop("MPa", "yield strength across the grain"),
    "fatigue_strength_mpa": _Prop("MPa", "fatigue strength (state the cycle count in the source)"),
    # Timber and panels have no yield point: their design strengths are
    # bending (f_m) and tension across the grain (f_t,90), not yield.
    "bending_strength_mpa": _Prop("MPa", "bending strength (along the grain or main axis)"),
    "bending_strength_across_mpa": _Prop("MPa", "bending strength across the grain or minor axis"),
    "tensile_strength_across_mpa": _Prop("MPa", "tension strength across the grain"),
    "elongation_pct": _Prop("%", "elongation at break"),
    "thermal_conductivity_w_mk": _Prop("W/mK", "thermal conductivity"),
    "cte_um_mk": _Prop("um/mK", "coefficient of thermal expansion"),
    "max_service_temp_c": _Prop("C", "highest continuous service temperature"),
    "melting_point_c": _Prop("C", "melting or glass transition point (say which in the source)"),
    "cost_per_kg": _Prop("<currency>/kg", "price per kg, e.g. unit GBP/kg"),
}

_COST_UNIT = re.compile(r"^[A-Z]{3}/kg$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


class CatalogError(ValueError):
    """An entry or value that does not pass the library's rules."""


class CatalogNotFoundError(KeyError):
    """No entry with that id or name."""


@dataclass(frozen=True)
class Sourced:
    value: float
    unit: str
    source_kind: str
    source_ref: str = ""
    needs_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source(where: str, raw: dict[str, Any]) -> tuple[str, str]:
    nested = raw.get("source")
    src: dict[str, Any] = nested if isinstance(nested, dict) else raw
    kind = str(src.get("source_kind") or src.get("kind") or "").strip()
    ref = str(src.get("source_ref") or src.get("ref") or "").strip()
    if kind not in SOURCE_KINDS:
        raise CatalogError(f"{where} needs a source kind, one of {', '.join(SOURCE_KINDS)}")
    if kind in _NEEDS_REF and not ref:
        raise CatalogError(f"{where}: a {kind} source needs its reference")
    return kind, ref


def _number(where: str, value: Any, *, allow_zero: bool = False) -> float:
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    if not ok or value < 0 or (value == 0 and not allow_zero):
        raise CatalogError(f"{where} must be a positive number")
    return float(value)


def parse_properties(raw: Any) -> dict[str, Sourced]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("properties must be an object keyed by property name")
    out: dict[str, Sourced] = {}
    for key, item in raw.items():
        spec = MATERIAL_PROPERTIES.get(key)
        if spec is None:
            raise CatalogError(
                f"unknown property '{key}'; known: {', '.join(sorted(MATERIAL_PROPERTIES))}"
            )
        if not isinstance(item, dict):
            raise CatalogError(f"property {key} must be an object with value, unit and source")
        unit = str(item.get("unit") or spec.unit).strip()
        if key == "cost_per_kg":
            if not _COST_UNIT.match(unit):
                raise CatalogError("cost_per_kg needs its currency in the unit, e.g. GBP/kg")
        elif unit != spec.unit:
            raise CatalogError(f"property {key} is in {spec.unit}, not {unit}")
        value = _number(f"property {key}", item.get("value"), allow_zero=key == "poisson_ratio")
        kind, ref = _source(f"property {key}", item)
        out[key] = Sourced(value, unit, kind, ref, kind == "assumption")
    return out


def parse_specs(raw: Any) -> dict[str, Sourced]:
    """Component specs: known names, fixed units, a source on each."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("specs must be an object keyed by spec name")
    out: dict[str, Sourced] = {}
    for key, item in raw.items():
        unit = COMPONENT_SPECS.get(key)
        if unit is None:
            raise CatalogError(f"unknown spec '{key}'; known: {', '.join(sorted(COMPONENT_SPECS))}")
        if not isinstance(item, dict):
            raise CatalogError(f"spec {key} must be an object with value, unit and source")
        if str(item.get("unit") or unit).strip() != unit:
            raise CatalogError(f"spec {key} is in {unit}")
        value = _number(f"spec {key}", item.get("value"))
        kind, ref = _source(f"spec {key}", item)
        out[key] = Sourced(value, unit, kind, ref, kind == "assumption")
    return out


def parse_appearance_properties(raw: Any) -> dict[str, Sourced]:
    """Appearance properties: known names, fixed units, a source on each."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("properties must be an object keyed by property name")
    out: dict[str, Sourced] = {}
    for key, item in raw.items():
        unit = APPEARANCE_PROPERTIES.get(key)
        if unit is None:
            raise CatalogError(
                f"unknown property '{key}'; known: {', '.join(sorted(APPEARANCE_PROPERTIES))}"
            )
        if not isinstance(item, dict):
            raise CatalogError(f"property {key} must be an object with value, unit and source")
        if str(item.get("unit") or unit).strip() != unit:
            raise CatalogError(f"property {key} is in {unit}")
        value = _number(f"property {key}", item.get("value"), allow_zero=True)
        if unit == "-" and value > 1:
            raise CatalogError(f"property {key} is between 0 and 1")
        kind, ref = _source(f"property {key}", item)
        out[key] = Sourced(value, unit, kind, ref, kind == "assumption")
    return out


def parse_colour(raw: Any) -> dict[str, Any]:
    """A colour: its system and code, and the sourced sRGB used on screen."""
    if not isinstance(raw, dict):
        raise CatalogError("colour needs system, code (unless custom), srgb_hex and a source")
    system = str(raw.get("system") or "").strip().lower()
    if system not in COLOUR_SYSTEMS:
        raise CatalogError(f"colour.system must be one of {', '.join(COLOUR_SYSTEMS)}")
    code = str(raw.get("code") or "").strip()
    if system != "custom" and not code:
        raise CatalogError(f"a {system} colour needs its code, e.g. RAL 9005")
    hex_value = str(raw.get("srgb_hex") or "").strip()
    if not _HEX.match(hex_value):
        raise CatalogError("colour.srgb_hex must be #RRGGBB")
    kind, ref = _source("colour", raw)
    return {
        "system": system,
        "code": code,
        "name": str(raw.get("name") or "").strip(),
        "srgb_hex": hex_value.upper(),
        "source_kind": kind,
        "source_ref": ref,
        "needs_review": kind == "assumption",
    }


def parse_geometry(raw: Any) -> dict[str, Any]:
    """Where the part's CAD comes from: a generator and its parameters, a STEP
    file, or a third-party OpenUSD package."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("geometry must be an object")
    if raw.get("generator"):
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise CatalogError("geometry.params must be an object")
        return {"generator": str(raw["generator"]), "params": params}
    if raw.get("step_url") or raw.get("step_work_product_id"):
        out = {k: str(raw[k]) for k in ("step_url", "step_work_product_id") if raw.get(k)}
        if raw.get("licence"):
            out["licence"] = str(raw["licence"])
        return out
    if raw.get("usd_url"):
        return _usd_geometry(raw)
    raise CatalogError(
        "geometry needs a generator, a step_url, a step_work_product_id or a usd_url"
    )


def _usd_geometry(raw: dict[str, Any]) -> dict[str, Any]:
    """A third-party OpenUSD package (FORGE-711), e.g. an NVIDIA SimReady asset.

    Unlike a STEP link, the licence is required and must be one MetaForge
    accepts: the package is downloaded and stored, so it is a copy, and a copy
    without a usable licence is one nobody can ship. The digest pins the exact
    package, so a later change upstream is a new entry, not a silent swap.
    """
    licence = normalise_licence(raw.get("licence"))
    if not licence:
        raise CatalogError("a usd_url geometry needs the package's licence")
    if not is_accepted(licence):
        raise CatalogError(
            f"licence {licence} is not one MetaForge accepts for stored geometry "
            f"({', '.join(sorted(ACCEPTED_LICENCES))}); a person has to read its terms"
        )
    sha256 = str(raw.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise CatalogError("a usd_url geometry needs the package's sha256")
    out: dict[str, Any] = {"usd_url": str(raw["usd_url"]), "sha256": sha256, "licence": licence}
    for key in ("package_id", "attribution", "preview_glb", "source"):
        if raw.get(key):
            out[key] = str(raw[key])
    return out


def parse_suppliers(raw: Any) -> list[dict[str, str]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise CatalogError("suppliers must be a list of {supplier, sku, url}")
    out = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("supplier") or not item.get("sku"):
            raise CatalogError("each supplier needs supplier and sku")
        out.append({k: str(item[k]) for k in ("supplier", "sku", "url") if item.get(k)})
    return out


def parse_rules(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise CatalogError("rules must be a list")
    rules = []
    for i, item in enumerate(raw):
        where = f"rule {i + 1}"
        if not isinstance(item, dict):
            raise CatalogError(f"{where} must be an object")
        kind = str(item.get("kind") or "")
        unit = RULE_KINDS.get(kind)
        if unit is None:
            raise CatalogError(f"{where}: kind must be one of {', '.join(RULE_KINDS)}")
        if str(item.get("unit") or unit) != unit:
            raise CatalogError(f"{where}: {kind} is in {unit}")
        rule: dict[str, Any] = {"kind": kind, "unit": unit}
        if kind == "envelope":
            dims = item.get("max_dims_mm")
            if not isinstance(dims, list) or len(dims) != 3:
                raise CatalogError(f"{where}: an envelope needs max_dims_mm, three numbers")
            rule["max_dims_mm"] = [_number(where, d) for d in dims]
        elif kind == "tolerance":
            rule["value"] = _number(where, item.get("value"))
        else:
            for bound in ("min", "max"):
                if item.get(bound) is not None:
                    rule[bound] = _number(where, item[bound], allow_zero=True)
            if "min" not in rule and "max" not in rule:
                raise CatalogError(f"{where}: give min, max or both")
        family = item.get("material_family")
        if family is not None:
            if family not in MATERIAL_FAMILIES:
                raise CatalogError(f"{where}: material_family must be one of the families")
            rule["material_family"] = family
        src_kind, ref = _source(where, item)
        rule.update(source_kind=src_kind, source_ref=ref, needs_review=src_kind == "assumption")
        rules.append(rule)
    return rules


def _choices(where: str, values: Any, allowed: tuple[str, ...]) -> list[str]:
    out = [str(v).strip() for v in values or [] if str(v).strip()]
    bad = [v for v in out if v not in allowed]
    if bad:
        raise CatalogError(f"{where}: {', '.join(bad)} not one of {', '.join(allowed)}")
    return out


@dataclass
class ProcessEntry:
    id: str
    name: str
    title: str
    family: str
    machine_kinds: list[str]
    material_families: list[str]
    rules: list[dict[str, Any]]
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="process")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MaterialEntry:
    id: str
    name: str
    title: str
    family: str
    designation: str
    forms: list[str]
    properties: dict[str, Sourced]
    processes: list[str]
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="material")

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["properties"] = {k: v.as_dict() for k, v in self.properties.items()}
        return out


@dataclass
class ComponentEntry:
    id: str
    name: str
    title: str
    family: str
    origin: str
    standard: str
    size: str
    specs: dict[str, Sourced]
    geometry: dict[str, Any]
    material: str
    suppliers: list[dict[str, str]]
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="component")

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["specs"] = {k: v.as_dict() for k, v in self.specs.items()}
        return out


@dataclass
class ErgonomicLimitEntry:
    id: str
    name: str
    title: str
    limit_kind: str
    applies_to: str
    min: float | None
    max: float | None
    unit: str
    population: str
    source_kind: str
    source_ref: str
    needs_review: bool = False
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="ergonomic_limit")

    @property
    def family(self) -> str:
        """The feature kind, so ``list(kind, family=...)`` filters by it."""
        return self.limit_kind

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_limit(self) -> dict[str, Any]:
        """The shape ``check_ergonomics`` takes as one of its ``limits``."""
        return {
            "kind": self.limit_kind,
            "min": self.min,
            "max": self.max,
            "population": self.population,
            "source_ref": f"{self.source_ref} [{self.name} v{self.version}]",
        }


def parse_ergonomic_limit(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the measurable part of an ergonomic limit."""
    limit_kind = str(data.get("limit_kind") or "")
    unit = ERGONOMIC_KINDS.get(limit_kind)
    if unit is None:
        raise CatalogError(f"limit_kind must be one of {', '.join(ERGONOMIC_KINDS)}")
    if str(data.get("unit") or unit) != unit:
        raise CatalogError(f"a {limit_kind} limit is in {unit}")
    bounds: dict[str, float | None] = {}
    for bound in ("min", "max"):
        value = data.get(bound)
        bounds[bound] = None if value is None else _number(bound, value, allow_zero=True)
    if bounds["min"] is None and bounds["max"] is None:
        raise CatalogError("give min, max or both")
    if bounds["min"] is not None and bounds["max"] is not None and bounds["min"] > bounds["max"]:
        raise CatalogError("min is larger than max")
    population = str(data.get("population") or "").strip()
    if not population:
        raise CatalogError("population is required: who the limit is for")
    kind, ref = _source("ergonomic limit", data)
    return {
        "limit_kind": limit_kind,
        "unit": unit,
        **bounds,
        "population": population,
        "source_kind": kind,
        "source_ref": ref,
        "needs_review": kind == "assumption",
    }


@dataclass
class AppearanceEntry:
    id: str
    name: str
    title: str
    finish: str
    colour: dict[str, Any]
    texture: str
    properties: dict[str, Sourced]
    materials: list[str]
    processes: list[str]
    suppliers: list[dict[str, str]]
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="appearance")

    @property
    def family(self) -> str:
        """The finish, so ``list(kind, family=...)`` filters appearances too."""
        return self.finish

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["properties"] = {k: v.as_dict() for k, v in self.properties.items()}
        return out


def _names(values: Any) -> list[str]:
    return [str(v).strip().lower() for v in values or [] if str(v).strip()]


def _name(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not _NAME.match(name):
        raise CatalogError(
            "name must be a lower-case identifier (letters, digits, _ or -), e.g. cnc_milling"
        )
    return name


class SqliteCatalog:
    """Process and material entries in SQLite, every change kept as a version."""

    def __init__(self, path: str = ":memory:", *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_entries (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                body TEXT NOT NULL, version INTEGER NOT NULL, updated_at REAL NOT NULL,
                UNIQUE (kind, name)
            );
            CREATE TABLE IF NOT EXISTS catalog_history (
                id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                changed_at REAL NOT NULL, changed_by TEXT NOT NULL,
                PRIMARY KEY (id, version)
            );
            """
        )
        self._conn.commit()
        logger.info("catalog_opened", path=path)

    # ── reads ────────────────────────────────────────────────────────────

    def _load(self, body: dict[str, Any]) -> Any:
        if body.get("kind") == "process":
            return ProcessEntry(**body)
        if body.get("kind") == "component":
            specs = {k: Sourced(**v) for k, v in (body.pop("specs", None) or {}).items()}
            return ComponentEntry(specs=specs, **body)
        if body.get("kind") == "ergonomic_limit":
            return ErgonomicLimitEntry(**body)
        if body.get("kind") == "appearance":
            props = {k: Sourced(**v) for k, v in (body.pop("properties", None) or {}).items()}
            return AppearanceEntry(properties=props, **body)
        props = {k: Sourced(**v) for k, v in (body.pop("properties", None) or {}).items()}
        return MaterialEntry(properties=props, **body)

    def get(self, ref: str, kind: str | None = None) -> Any:
        """By id, or by name when ``kind`` is given."""
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM catalog_entries WHERE id = ?", (ref,)
            ).fetchone()
            if row is None and kind:
                row = self._conn.execute(
                    "SELECT body FROM catalog_entries WHERE kind = ? AND name = ?",
                    (kind, ref.strip().lower()),
                ).fetchone()
        if row is None:
            raise CatalogNotFoundError(ref)
        entry = self._load(json.loads(row[0]))
        if kind and entry.kind != kind:
            raise CatalogNotFoundError(ref)
        return entry

    def list(self, kind: str, *, family: str | None = None) -> list[Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM catalog_entries WHERE kind = ? ORDER BY name", (kind,)
            ).fetchall()
        entries = [self._load(json.loads(r[0])) for r in rows]
        return [e for e in entries if family is None or e.family == family]

    def history(self, entry_id: str) -> builtins.list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, changed_at, changed_by FROM catalog_history "
                "WHERE id = ? ORDER BY version",
                (entry_id,),
            ).fetchall()
        return [{"version": r[0], "changed_at": r[1], "changed_by": r[2]} for r in rows]

    def process_limits(self, ref: str, material: str | None = None) -> dict[str, Any]:
        """A process's rules as ``check_manufacturability`` limits and a ranking candidate.

        With a material, only rules for every family or that material's family
        apply. Rules for another family are left out, not merged.
        """
        process = self.get(ref, "process")
        assert isinstance(process, ProcessEntry)
        family = None
        material_name = None
        if material:
            mat = self.get(material, "material")
            assert isinstance(mat, MaterialEntry)
            family, material_name = mat.family, mat.name
            if process.material_families and family not in process.material_families:
                raise CatalogError(
                    f"{process.name} is not recorded as making {family} parts "
                    f"({mat.name}); it makes {', '.join(process.material_families)}"
                )
        applicable = [
            r
            for r in process.rules
            if r.get("material_family") is None or r.get("material_family") == family
        ]
        limits: list[dict[str, Any]] = []
        tolerance: float | None = None
        min_feature: float | None = None
        refs: list[str] = []
        for rule in applicable:
            ref_text = rule["source_ref"] or f"{rule['source_kind']} ({process.name} rule)"
            refs.append(ref_text)
            if rule["kind"] == "tolerance":
                tolerance = rule["value"] if tolerance is None else min(tolerance, rule["value"])
                continue
            limit: dict[str, Any] = {"kind": rule["kind"], "source_ref": ref_text}
            for key in ("min", "max", "max_dims_mm"):
                if key in rule:
                    limit[key] = rule[key]
            limits.append(limit)
            if rule["kind"] == "feature_size" and "min" in rule:
                min_feature = rule["min"]
        return {
            "process_id": process.id,
            "process": process.name,
            "material": material_name,
            "limits": limits,
            "candidate": {
                "process": process.name,
                "min_feature_mm": min_feature,
                "achievable_tolerance_mm": tolerance,
                "materials": list(process.material_families),
                "source_refs": sorted(set(refs)),
            },
            "needs_review": sorted({r["kind"] for r in applicable if r.get("needs_review")}),
        }

    # ── writes ───────────────────────────────────────────────────────────

    def _save(self, entry: Any, by: str, *, new: bool) -> None:
        body = json.dumps(entry.as_dict())
        with self._lock:
            try:
                if new:
                    self._conn.execute(
                        "INSERT INTO catalog_entries (id, kind, name, body, version, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (entry.id, entry.kind, entry.name, body, entry.version, entry.updated_at),
                    )
                else:
                    self._conn.execute(
                        "UPDATE catalog_entries SET body = ?, version = ?, updated_at = ? "
                        "WHERE id = ?",
                        (body, entry.version, entry.updated_at, entry.id),
                    )
            except sqlite3.IntegrityError as exc:
                raise CatalogError(f"a {entry.kind} named {entry.name} already exists") from exc
            self._conn.execute(
                "INSERT INTO catalog_history (id, version, body, changed_at, changed_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry.id, entry.version, body, entry.updated_at, by or "unknown"),
            )
            self._conn.commit()
        logger.info(
            "catalog_entry_saved", kind=entry.kind, entry_id=entry.id, version=entry.version, by=by
        )

    def register(self, kind: str, data: dict[str, Any], *, by: str = "") -> Any:
        now = self._clock()
        entry: ProcessEntry | MaterialEntry | ComponentEntry | AppearanceEntry | ErgonomicLimitEntry
        if kind == "process":
            family = str(data.get("family") or "")
            if family not in PROCESS_FAMILIES:
                raise CatalogError(f"family must be one of {', '.join(PROCESS_FAMILIES)}")
            entry = ProcessEntry(
                id=f"pr_{uuid.uuid4().hex[:12]}",
                name=_name(data.get("name")),
                title=str(data.get("title") or data.get("name") or "").strip(),
                family=family,
                machine_kinds=_choices("machine_kinds", data.get("machine_kinds"), MACHINE_KINDS),
                material_families=_choices(
                    "material_families", data.get("material_families"), MATERIAL_FAMILIES
                ),
                rules=parse_rules(data.get("rules")),
                notes=str(data.get("notes") or ""),
                updated_at=now,
            )
        elif kind == "material":
            family = str(data.get("family") or "")
            if family not in MATERIAL_FAMILIES:
                raise CatalogError(f"family must be one of {', '.join(MATERIAL_FAMILIES)}")
            entry = MaterialEntry(
                id=f"ma_{uuid.uuid4().hex[:12]}",
                name=_name(data.get("name")),
                title=str(data.get("title") or data.get("name") or "").strip(),
                family=family,
                designation=str(data.get("designation") or "").strip(),
                forms=_choices("forms", data.get("forms"), MATERIAL_FORMS),
                properties=parse_properties(data.get("properties")),
                processes=[str(p).strip().lower() for p in data.get("processes") or [] if p],
                notes=str(data.get("notes") or ""),
                updated_at=now,
            )
        elif kind == "component":
            family = str(data.get("family") or "")
            if family not in COMPONENT_FAMILIES:
                raise CatalogError(f"family must be one of {', '.join(COMPONENT_FAMILIES)}")
            origin = str(data.get("origin") or "")
            if origin not in COMPONENT_ORIGINS:
                raise CatalogError("origin must be standard or purchased")
            standard = str(data.get("standard") or "").strip()
            if origin == "standard" and not standard:
                raise CatalogError("a standard part names its standard, e.g. ISO 4762")
            entry = ComponentEntry(
                id=f"co_{uuid.uuid4().hex[:12]}",
                name=_name(data.get("name")),
                title=str(data.get("title") or data.get("name") or "").strip(),
                family=family,
                origin=origin,
                standard=standard,
                size=str(data.get("size") or "").strip(),
                specs=parse_specs(data.get("specs")),
                geometry=parse_geometry(data.get("geometry")),
                material=str(data.get("material") or "").strip().lower(),
                suppliers=parse_suppliers(data.get("suppliers")),
                notes=str(data.get("notes") or ""),
                updated_at=now,
            )
        elif kind == "appearance":
            finish = str(data.get("finish") or "")
            if finish not in APPEARANCE_FINISHES:
                raise CatalogError(f"finish must be one of {', '.join(APPEARANCE_FINISHES)}")
            entry = AppearanceEntry(
                id=f"ap_{uuid.uuid4().hex[:12]}",
                name=_name(data.get("name")),
                title=str(data.get("title") or data.get("name") or "").strip(),
                finish=finish,
                colour=parse_colour(data.get("colour")),
                texture=str(data.get("texture") or "").strip(),
                properties=parse_appearance_properties(data.get("properties")),
                materials=_names(data.get("materials")),
                processes=_names(data.get("processes")),
                suppliers=parse_suppliers(data.get("suppliers")),
                notes=str(data.get("notes") or ""),
                updated_at=now,
            )
        elif kind == "ergonomic_limit":
            entry = ErgonomicLimitEntry(
                id=f"el_{uuid.uuid4().hex[:12]}",
                name=_name(data.get("name")),
                title=str(data.get("title") or data.get("name") or "").strip(),
                applies_to=str(data.get("applies_to") or "").strip(),
                notes=str(data.get("notes") or ""),
                updated_at=now,
                **parse_ergonomic_limit(data),
            )
        else:
            raise CatalogError(
                "kind must be process, material, component, appearance or ergonomic_limit"
            )
        self._save(entry, by, new=True)
        return entry

    def update(self, entry_id: str, changes: dict[str, Any], *, by: str = "") -> Any:
        """Change an entry; rules are replaced, properties merged by name."""
        entry = self.get(entry_id)
        if isinstance(entry, ProcessEntry):
            allowed = {"title", "machine_kinds", "material_families", "rules", "notes"}
        elif isinstance(entry, ComponentEntry):
            allowed = {"title", "specs", "geometry", "material", "suppliers", "notes"}
        elif isinstance(entry, ErgonomicLimitEntry):
            allowed = {
                "title",
                "applies_to",
                "min",
                "max",
                "population",
                "source_kind",
                "source_ref",
                "notes",
            }
        elif isinstance(entry, AppearanceEntry):
            allowed = {
                "title",
                "finish",
                "colour",
                "texture",
                "properties",
                "materials",
                "processes",
                "suppliers",
                "notes",
            }
        else:
            allowed = {"title", "designation", "forms", "properties", "processes", "notes"}
        unknown = set(changes) - allowed
        if unknown:
            raise CatalogError(f"cannot change {', '.join(sorted(unknown))}")
        if isinstance(entry, ErgonomicLimitEntry):
            merged = {
                "limit_kind": entry.limit_kind,
                "min": entry.min,
                "max": entry.max,
                "population": entry.population,
                "source_kind": entry.source_kind,
                "source_ref": entry.source_ref,
                **{k: v for k, v in changes.items() if k in {"min", "max", "population"}},
            }
            if "source_kind" in changes or "source_ref" in changes:
                merged["source_kind"] = changes.get("source_kind", entry.source_kind)
                merged["source_ref"] = changes.get("source_ref", entry.source_ref)
            parsed = parse_ergonomic_limit(merged)
            entry.min, entry.max = parsed["min"], parsed["max"]
            entry.population = parsed["population"]
            entry.source_kind, entry.source_ref = parsed["source_kind"], parsed["source_ref"]
            entry.needs_review = parsed["needs_review"]
            if "applies_to" in changes:
                entry.applies_to = str(changes["applies_to"] or "").strip()
        elif isinstance(entry, AppearanceEntry):
            if "finish" in changes:
                if changes["finish"] not in APPEARANCE_FINISHES:
                    raise CatalogError(f"finish must be one of {', '.join(APPEARANCE_FINISHES)}")
                entry.finish = str(changes["finish"])
            if "colour" in changes:
                entry.colour = parse_colour(changes["colour"])
            if "texture" in changes:
                entry.texture = str(changes["texture"] or "").strip()
            if "properties" in changes:
                entry.properties = {
                    **entry.properties,
                    **parse_appearance_properties(changes["properties"]),
                }
            if "materials" in changes:
                entry.materials = _names(changes["materials"])
            if "processes" in changes:
                entry.processes = _names(changes["processes"])
            if "suppliers" in changes:
                entry.suppliers = parse_suppliers(changes["suppliers"])
        elif isinstance(entry, ComponentEntry):
            if "specs" in changes:
                entry.specs = {**entry.specs, **parse_specs(changes["specs"])}
            if "geometry" in changes:
                entry.geometry = parse_geometry(changes["geometry"])
            if "material" in changes:
                entry.material = str(changes["material"] or "").strip().lower()
            if "suppliers" in changes:
                entry.suppliers = parse_suppliers(changes["suppliers"])
        elif isinstance(entry, ProcessEntry):
            if "machine_kinds" in changes:
                entry.machine_kinds = _choices(
                    "machine_kinds", changes["machine_kinds"], MACHINE_KINDS
                )
            if "material_families" in changes:
                entry.material_families = _choices(
                    "material_families", changes["material_families"], MATERIAL_FAMILIES
                )
            if "rules" in changes:
                entry.rules = parse_rules(changes["rules"])
        else:
            if "forms" in changes:
                entry.forms = _choices("forms", changes["forms"], MATERIAL_FORMS)
            if "properties" in changes:
                entry.properties = {**entry.properties, **parse_properties(changes["properties"])}
            if "processes" in changes:
                entry.processes = [str(p).strip().lower() for p in changes["processes"] or []]
            if "designation" in changes:
                entry.designation = str(changes["designation"] or "").strip()
        if "title" in changes:
            entry.title = str(changes["title"] or "").strip()
        if "notes" in changes:
            entry.notes = str(changes["notes"] or "")
        entry.version += 1
        entry.updated_at = self._clock()
        self._save(entry, by, new=False)
        return entry


def _fmt(value: float) -> str:
    return f"{value:g}"


def summarize_process(entry: ProcessEntry) -> str:
    """One line for a flow's context: the process and its rules."""
    parts = []
    for rule in entry.rules:
        scope = f" ({rule['material_family']})" if rule.get("material_family") else ""
        if rule["kind"] == "tolerance":
            parts.append(f"tolerance {_fmt(rule['value'])} mm{scope}")
        elif rule["kind"] == "envelope":
            dims = "x".join(_fmt(v) for v in rule["max_dims_mm"])
            parts.append(f"envelope {dims} mm{scope}")
        else:
            if "min" in rule:
                parts.append(f"{rule['kind']} >= {_fmt(rule['min'])} {rule['unit']}{scope}")
            if "max" in rule:
                parts.append(f"{rule['kind']} <= {_fmt(rule['max'])} {rule['unit']}{scope}")
    review = sorted({r["kind"] for r in entry.rules if r.get("needs_review")})
    if review:
        parts.append(f"assumed, needs review: {', '.join(review)}")
    head = (
        f"{entry.title or entry.name} [{entry.id}] {entry.family}"
        + (f", on {', '.join(entry.machine_kinds)}" if entry.machine_kinds else "")
        + (f", makes {', '.join(entry.material_families)}" if entry.material_families else "")
    )
    return f"{head}: {'; '.join(parts) or 'no rules recorded'}"


_SHORT = {
    "density_kg_m3": "density",
    "youngs_modulus_mpa": "E",
    "yield_strength_mpa": "yield",
    "ultimate_strength_mpa": "UTS",
    "max_service_temp_c": "max service",
    "cost_per_kg": "cost",
}


def summarize_material(entry: MaterialEntry) -> str:
    """One line for a flow's context: the material and its key properties."""
    parts = [
        f"{_SHORT[k]} {_fmt(p.value)} {p.unit}" for k, p in entry.properties.items() if k in _SHORT
    ]
    review = sorted(k for k, p in entry.properties.items() if p.needs_review)
    if review:
        parts.append(f"assumed, needs review: {', '.join(review)}")
    head = (
        f"{entry.title or entry.name} [{entry.id}] {entry.family}"
        + (f" {entry.designation}" if entry.designation else "")
        + (f", forms {', '.join(entry.forms)}" if entry.forms else "")
    )
    return f"{head}: {'; '.join(parts) or 'no properties recorded'}"


def summarize_component(entry: ComponentEntry) -> str:
    """One line: the part, its standard and size, its key specs."""
    parts = [f"{k} {_fmt(v.value)} {v.unit}" for k, v in list(entry.specs.items())[:6]]
    head = f"{entry.title or entry.name} [{entry.id}] {entry.family}" + (
        f" {entry.standard} {entry.size}".rstrip() if entry.standard or entry.size else ""
    )
    return f"{head}: {'; '.join(parts) or 'no specs recorded'}"


def summarize_appearance(entry: AppearanceEntry) -> str:
    """One line: the finish, its colour and how it was sourced."""
    colour = entry.colour
    named = " ".join(
        part for part in (colour.get("code"), colour.get("name")) if part
    ) or colour.get("system", "")
    parts = [f"colour {named} ({colour.get('srgb_hex')} on screen)"]
    if entry.texture:
        parts.append(f"texture {entry.texture}")
    parts += [f"{k} {_fmt(v.value)} {v.unit}" for k, v in entry.properties.items()]
    review = sorted(k for k, v in entry.properties.items() if v.needs_review)
    if colour.get("needs_review"):
        review.insert(0, "colour")
    if review:
        parts.append(f"assumed, needs review: {', '.join(review)}")
    head = f"{entry.title or entry.name} [{entry.id}] {entry.finish}" + (
        f", for {', '.join(entry.materials)}" if entry.materials else ""
    )
    return f"{head}: {'; '.join(parts)}"


def summarize_ergonomic_limit(entry: ErgonomicLimitEntry) -> str:
    """One line: the limit, what it applies to, for whom, and its source."""
    bounds = []
    if entry.min is not None:
        bounds.append(f">= {_fmt(entry.min)} {entry.unit}")
    if entry.max is not None:
        bounds.append(f"<= {_fmt(entry.max)} {entry.unit}")
    review = "; assumed, needs review" if entry.needs_review else ""
    return (
        f"{entry.title or entry.name} [{entry.id}] {entry.limit_kind} {' and '.join(bounds)}"
        f"{f' ({entry.applies_to})' if entry.applies_to else ''}, for {entry.population}; "
        f"source: {entry.source_ref or entry.source_kind}{review}"
    )
