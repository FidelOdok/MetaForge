"""Standard mechanical parts built from their standards' tables (FORGE-449).

The pattern CAD tools use for fasteners and bearings: one family per
standard, a dimension table, and a part made on demand for a size. Here a
family function turns a size into a library ``component`` entry: its
dimensions as typed specs, each citing the table row it came from, and a
geometry *generator* with the parameters a CAD tool needs to build it.

The tables are bd_warehouse's (Apache-2.0, vendored unmodified in
``data/bd_warehouse`` with its licence and a NOTICE). Families:

* ISO 4762 socket head cap screws (``iso4762``)
* ISO 4032 hex nuts (``iso4032``)
* ISO 7089 plain washers (``iso7089``)
* single-row deep groove ball bearings, by designation (``deep_groove_bearing``)

A size the table does not have, or a screw length outside the standard's
range for that size, is refused. Nothing is interpolated.

Layer: stdlib only.
"""

from __future__ import annotations

import csv
from functools import cache
from pathlib import Path
from typing import Any

__all__ = [
    "SOURCE_COMMIT",
    "StandardPartError",
    "deep_groove_bearing",
    "iso4032",
    "iso4762",
    "iso7089",
    "build_standard_part",
    "seed_entries",
]

DATA = Path(__file__).parent / "data" / "bd_warehouse"
SOURCE_COMMIT = "8e9be06"


class StandardPartError(ValueError):
    """A size or length the standard's table does not hold."""


@cache
def _table(filename: str) -> dict[str, dict[str, str]]:
    with (DATA / filename).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    out = {}
    for row in rows:
        clean = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        out[clean["Size"]] = clean
    return out


def _ref(filename: str, row: str, what: str) -> str:
    return f"{what}, bd_warehouse {filename} row {row} (commit {SOURCE_COMMIT}, Apache-2.0)"


def _spec(value: float, unit: str, ref: str, kind: str = "standard") -> dict[str, Any]:
    return {"value": value, "unit": unit, "source_kind": kind, "source_ref": ref}


def _num(row: dict[str, str], column: str, size: str, family: str) -> float:
    raw = row.get(column, "")
    try:
        return float(raw)
    except ValueError as exc:
        raise StandardPartError(f"{family} has no {column} for {size}") from exc


def _metric(size: str) -> tuple[str, float]:
    """``M3`` -> (``M3``, 3.0); accepts ``m3`` too."""
    text = size.strip().upper()
    if not text.startswith("M"):
        raise StandardPartError(f"{size!r} is not a metric size like M3")
    try:
        return text, float(text[1:])
    except ValueError as exc:
        raise StandardPartError(f"{size!r} is not a metric size like M3") from exc


def _row_for(
    table: dict[str, dict[str, str]], size: str, column: str
) -> tuple[str, dict[str, str]]:
    """The row for ``size`` that the standard covers (``column`` filled in).

    Tables list several pitches per size (``M3-0.5``, ``M3-0.35``); a size
    with no pitch given takes the coarse thread, the largest pitch, which is
    the default for standard fasteners. A row named exactly is used as is.
    """
    if size in table and table[size].get(column):
        return size, table[size]
    matches = [k for k in table if k.split("-")[0] == size and table[k].get(column)]
    if not matches:
        raise StandardPartError(f"no {size} in the table for {column.split(':')[0]}")
    key = max(matches, key=lambda k: float(k.split("-")[1]) if "-" in k else 0.0)
    return key, table[key]


def _designation(text: str) -> str:
    return text.replace("*", "").strip()


def _slug(text: str) -> str:
    return text.lower().replace(".", "_").replace("-", "_").replace("/", "_")


def iso4762(size: str, length_mm: float) -> dict[str, Any]:
    """An ISO 4762 socket head cap screw, e.g. ``iso4762("M3", 10)``."""
    family = "ISO 4762"
    name, diameter = _metric(size)
    filename = "socket_head_cap_parameters.csv"
    key, row = _row_for(_table(filename), name, "iso4762:dk")
    shortest = _num(row, "iso4762:short", key, family)
    longest = _num(row, "iso4762:long", key, family)
    if not shortest <= length_mm <= longest:
        raise StandardPartError(
            f"{family} {name} lengths run {shortest:g} to {longest:g} mm, not {length_mm:g}"
        )
    pitch = float(key.split("-")[1])
    std = _ref(filename, key, family)
    head_d = _num(row, "iso4762:dk", key, family)
    head_h = _num(row, "iso4762:k", key, family)
    socket = _num(row, "iso4762:s", key, family)
    depth = _num(row, "iso4762:t", key, family)
    label = f"{name}x{length_mm:g}"
    return {
        "name": f"iso4762_{_slug(label)}",
        "title": f"Socket head cap screw {label} ISO 4762",
        "family": "screw",
        "origin": "standard",
        "standard": family,
        "size": label,
        "specs": {
            "nominal_diameter_mm": _spec(diameter, "mm", std),
            "thread_pitch_mm": _spec(pitch, "mm", std),
            "length_mm": _spec(float(length_mm), "mm", f"{family} nominal length {length_mm:g} mm"),
            "head_diameter_mm": _spec(head_d, "mm", std),
            "head_height_mm": _spec(head_h, "mm", std),
            "socket_size_mm": _spec(socket, "mm", std),
            "socket_depth_mm": _spec(depth, "mm", std),
        },
        "geometry": {
            "generator": "socket_head_cap_screw",
            "params": {
                "d": diameter,
                "length": float(length_mm),
                "dk": head_d,
                "k": head_h,
                "s": socket,
                "t": depth,
            },
        },
        "notes": "Threads are not modelled; the shank is a plain cylinder of the nominal diameter.",
    }


def iso4032(size: str) -> dict[str, Any]:
    """An ISO 4032 hex nut, e.g. ``iso4032("M5")``."""
    family = "ISO 4032"
    name, diameter = _metric(size)
    filename = "hex_nut_parameters.csv"
    key, row = _row_for(_table(filename), name, "iso4032:m")
    std = _ref(filename, key, family)
    height = _num(row, "iso4032:m", key, family)
    flats = _num(row, "iso4032:s", key, family)
    return {
        "name": f"iso4032_{_slug(name)}",
        "title": f"Hex nut {name} ISO 4032",
        "family": "nut",
        "origin": "standard",
        "standard": family,
        "size": name,
        "specs": {
            "nominal_diameter_mm": _spec(diameter, "mm", std),
            "thread_pitch_mm": _spec(float(key.split("-")[1]), "mm", std),
            "height_mm": _spec(height, "mm", std),
            "width_across_flats_mm": _spec(flats, "mm", std),
        },
        "geometry": {"generator": "hex_nut", "params": {"d": diameter, "m": height, "s": flats}},
        "notes": "Threads are not modelled; the bore is the nominal diameter.",
    }


def iso7089(size: str) -> dict[str, Any]:
    """An ISO 7089 plain washer, e.g. ``iso7089("M6")``."""
    family = "ISO 7089"
    name, diameter = _metric(size)
    filename = "plain_washer_parameters.csv"
    key, row = _row_for(_table(filename), name, "iso7089:d1")
    std = _ref(filename, key, family)
    inner = _num(row, "iso7089:d1", key, family)
    outer = _num(row, "iso7089:d2", key, family)
    thick = _num(row, "iso7089:h", key, family)
    return {
        "name": f"iso7089_{_slug(name)}",
        "title": f"Plain washer {name} ISO 7089",
        "family": "washer",
        "origin": "standard",
        "standard": family,
        "size": name,
        "specs": {
            "nominal_diameter_mm": _spec(diameter, "mm", std),
            "inner_diameter_mm": _spec(inner, "mm", std),
            "outer_diameter_mm": _spec(outer, "mm", std),
            "thickness_mm": _spec(thick, "mm", std),
        },
        "geometry": {"generator": "washer", "params": {"d1": inner, "d2": outer, "h": thick}},
    }


def deep_groove_bearing(designation: str) -> dict[str, Any]:
    """A single-row deep groove ball bearing by designation, e.g. ``"608"``."""
    filename = "single_row_deep_groove_ball_bearing_parameters.csv"
    table = _table(filename)
    # Some designations carry a leading "* " (a product-line marker in the
    # catalogue); matched without it, kept in the notes.
    rows = {
        _designation(r.get("SKT:Designation", "")): (k, r, r.get("SKT:Designation", ""))
        for k, r in table.items()
    }
    designation = _designation(designation)
    if designation not in rows:
        raise StandardPartError(f"no deep groove bearing {designation!r} in the table")
    key, row, raw_designation = rows[designation]
    cat = _ref(filename, key, f"Deep groove ball bearing {designation} catalogue values")

    def spec(column: str, unit: str) -> dict[str, Any]:
        return _spec(_num(row, column, key, designation), unit, cat, "manufacturer_spec")

    bore, outer, width = (_num(row, c, key, designation) for c in ("SKT:d", "SKT:D", "SKT:B"))
    return {
        "name": f"bearing_{_slug(designation)}",
        "title": f"Deep groove ball bearing {designation} ({bore:g}x{outer:g}x{width:g})",
        "family": "bearing",
        "origin": "standard",
        "standard": "ISO 15 (boundary dimensions)",
        "size": designation,
        "specs": {
            "bore_mm": spec("SKT:d", "mm"),
            "outer_diameter_mm": spec("SKT:D", "mm"),
            "width_mm": spec("SKT:B", "mm"),
            "dynamic_load_kn": spec("SKT:C", "kN"),
            "static_load_kn": spec("SKT:C0", "kN"),
            "fatigue_load_limit_kn": spec("SKT:Pu", "kN"),
            "reference_speed_rpm": spec("SKT:Speed-reference", "rpm"),
            "limiting_speed_rpm": spec("SKT:Speed-limit", "rpm"),
            "mass_kg": spec("SKT:mass", "kg"),
        },
        "geometry": {"generator": "ball_bearing", "params": {"d": bore, "D": outer, "B": width}},
        "notes": (
            "Load ratings, speeds and mass are a bearing catalogue's, as tabulated in "
            "bd_warehouse; the catalogue is not named there. Confirm against the chosen "
            "maker's datasheet before relying on a rating."
            + (
                f" The table lists it as {raw_designation!r} (marked in the catalogue)."
                if raw_designation.strip() != designation
                else ""
            )
        ),
    }


#: The families :func:`build_standard_part` knows, by the name tools use.
FAMILIES = ("iso4762", "iso4032", "iso7089", "deep_groove_bearing")


def build_standard_part(family: str, size: str, length_mm: float | None = None) -> dict[str, Any]:
    """The library entry for one standard part; ``length_mm`` for screws only."""
    key = family.strip().lower().replace(" ", "")
    if key == "iso4762":
        if length_mm is None:
            raise StandardPartError("an ISO 4762 screw needs length_mm")
        return iso4762(size, length_mm)
    if key == "iso4032":
        return iso4032(size)
    if key == "iso7089":
        return iso7089(size)
    if key == "deep_groove_bearing":
        return deep_groove_bearing(size)
    raise StandardPartError(f"family must be one of {', '.join(FAMILIES)}")


#: The seed set: common sizes, each one a standard part with sourced specs.
_SCREWS: dict[str, tuple[float, ...]] = {
    "M2.5": (6, 8, 10, 12),
    "M3": (6, 8, 10, 12, 16, 20),
    "M4": (8, 10, 12, 16, 20, 25),
    "M5": (10, 12, 16, 20, 25),
    "M6": (12, 16, 20, 25, 30),
}
_NUTS_WASHERS = ("M2.5", "M3", "M4", "M5", "M6", "M8")


def seed_entries() -> list[dict[str, Any]]:
    """Every seed entry: ISO 4762 screws, ISO 4032 nuts, ISO 7089 washers, bearings."""
    out = [iso4762(size, length) for size, lengths in _SCREWS.items() for length in lengths]
    out += [iso4032(size) for size in _NUTS_WASHERS]
    out += [iso7089(size) for size in _NUTS_WASHERS]
    bearing_table = _table("single_row_deep_groove_ball_bearing_parameters.csv")
    designations = sorted(
        {
            _designation(r["SKT:Designation"])
            for r in bearing_table.values()
            if _designation(r.get("SKT:Designation", ""))
        }
    )
    out += [deep_groove_bearing(d) for d in designations]
    return out
