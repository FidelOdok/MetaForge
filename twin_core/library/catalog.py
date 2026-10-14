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

Layer: stdlib and structlog (twin_core may import nothing above it).
"""

from __future__ import annotations

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
    src = raw.get("source") if isinstance(raw.get("source"), dict) else raw
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

    def _load(self, body: dict[str, Any]) -> ProcessEntry | MaterialEntry:
        if body.get("kind") == "process":
            return ProcessEntry(**body)
        props = {k: Sourced(**v) for k, v in (body.pop("properties", None) or {}).items()}
        return MaterialEntry(properties=props, **body)

    def get(self, ref: str, kind: str | None = None) -> ProcessEntry | MaterialEntry:
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

    def history(self, entry_id: str) -> list[dict[str, Any]]:
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

    def _save(self, entry: ProcessEntry | MaterialEntry, by: str, *, new: bool) -> None:
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
        entry: ProcessEntry | MaterialEntry
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
        else:
            raise CatalogError("kind must be process or material")
        self._save(entry, by, new=True)
        return entry

    def update(self, entry_id: str, changes: dict[str, Any], *, by: str = "") -> Any:
        """Change an entry; rules are replaced, properties merged by name."""
        entry = self.get(entry_id)
        if isinstance(entry, ProcessEntry):
            allowed = {"title", "machine_kinds", "material_families", "rules", "notes"}
        else:
            allowed = {"title", "designation", "forms", "properties", "processes", "notes"}
        unknown = set(changes) - allowed
        if unknown:
            raise CatalogError(f"cannot change {', '.join(sorted(unknown))}")
        if isinstance(entry, ProcessEntry):
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
