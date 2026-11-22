"""Real part data for an electronic component (FORGE-906).

A library part already had one sourced number per spec. A real part needs
more before a design can rely on it, and each piece says where it came from:

* **Ratings** from the datasheet's own tables: the absolute maximum ratings
  (never to be reached), the recommended operating conditions (where the
  part is specified to work) and electrical characteristics (what it does
  there). Each row has min, typ and max as the datasheet gives them, its
  test conditions and its source, so a derating check can compare a design
  value with the right limit (FORGE-908).
* **Lifecycle**: active, NRND, end of life or obsolete, with who said so and
  when, instead of a title prefix.
* **ECAD**: the KiCad symbol and footprint the part is drawn with.

``check_part_data`` says, item by item, what a part still lacks. It never
fills a gap: a missing value is ``missing``, an assumed one ``review``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from twin_core.library.catalog import CatalogError, _source

__all__ = [
    "LIFECYCLE_STATUSES",
    "RATING_KINDS",
    "RATING_PARAMS",
    "check_part_data",
    "parse_ecad",
    "parse_lifecycle",
    "parse_ratings",
]

#: The datasheet table a rating row comes from.
RATING_KINDS: tuple[str, ...] = ("abs_max", "recommended", "electrical")

#: The parameters a rating row may name, and the one unit each is given in.
#: Closed, like ``COMPONENT_SPECS``, so a check can find a limit by name.
RATING_PARAMS: dict[str, str] = {
    "supply_voltage_v": "V",
    "input_voltage_v": "V",
    "output_voltage_v": "V",
    "pin_voltage_v": "V",
    "dropout_voltage_v": "V",
    "forward_voltage_v": "V",
    "reverse_voltage_v": "V",
    "voltage_rating_v": "V",
    "esd_hbm_v": "V",
    "esd_cdm_v": "V",
    "output_current_a": "A",
    "input_current_a": "A",
    "forward_current_a": "A",
    "peak_current_a": "A",
    "current_limit_a": "A",
    "quiescent_current_a": "A",
    "leakage_current_a": "A",
    "power_dissipation_w": "W",
    "power_rating_w": "W",
    "junction_temp_c": "degC",
    "operating_temp_c": "degC",
    "storage_temp_c": "degC",
    "thermal_resistance_ja_c_per_w": "degC/W",
    "thermal_resistance_jc_c_per_w": "degC/W",
    "switching_frequency_hz": "Hz",
    "capacitance_f": "F",
    "resistance_ohm": "ohm",
    "inductance_h": "H",
    "tolerance_pct": "%",
}

LIFECYCLE_STATUSES: tuple[str, ...] = ("active", "nrnd", "eol", "obsolete", "unknown")

MAX_RATINGS = 200
#: A lifecycle status older than this is shown for review: makers announce
#: end of life with months of notice, not years.
LIFECYCLE_STALE_DAYS = 365

_KICAD_ID = re.compile(r"^[A-Za-z0-9_.+\-]+:[^\s:][^:]*$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Families that are electronic parts on a board and need the whole set.
_ELECTRONIC = ("electronic",)
#: Families bought as a finished unit: no SPICE model or footprint expected.
_UNIT = ("module", "battery")


def _bound(where: str, value: Any) -> float | None:
    if value is None:
        return None
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    if not ok or value != value or value in (float("inf"), float("-inf")):
        raise CatalogError(f"{where} must be a number")
    return float(value)


def parse_ratings(raw: Any) -> list[dict[str, Any]]:
    """Datasheet rating rows: [{param, kind, min?, typ?, max?, unit, conditions?, source}]."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise CatalogError("ratings must be a list of rows from the datasheet's tables")
    if len(raw) > MAX_RATINGS:
        raise CatalogError(f"at most {MAX_RATINGS} rating rows")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise CatalogError(f"rating {i} must be an object")
        param = str(item.get("param") or "").strip()
        unit = RATING_PARAMS.get(param)
        if unit is None:
            raise CatalogError(
                f"rating {i}: unknown param '{param}'; known: {', '.join(sorted(RATING_PARAMS))}"
            )
        where = f"rating {param}"
        kind = str(item.get("kind") or "").strip()
        if kind not in RATING_KINDS:
            raise CatalogError(f"{where}: kind must be one of {', '.join(RATING_KINDS)}")
        given = str(item.get("unit") or unit).strip()
        if given != unit:
            raise CatalogError(f"{where} is in {unit}, not {given}")
        bounds = {b: _bound(f"{where} {b}", item.get(b)) for b in ("min", "typ", "max")}
        present = [v for v in bounds.values() if v is not None]
        if not present:
            raise CatalogError(f"{where}: give min, typ or max as the datasheet does")
        if present != sorted(present):
            raise CatalogError(f"{where}: min, typ and max must be in order")
        source_kind, source_ref = _source(where, item)
        out.append(
            {
                "param": param,
                "kind": kind,
                **bounds,
                "unit": unit,
                "conditions": str(item.get("conditions") or "").strip()[:300],
                "source_kind": source_kind,
                "source_ref": source_ref,
                "needs_review": source_kind == "assumption",
            }
        )
    return out


def parse_lifecycle(raw: Any) -> dict[str, Any]:
    """{status, source, read_on}: who said the part is active or ending, and when."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("lifecycle must be an object with status, source and read_on")
    status = str(raw.get("status") or "").strip().lower()
    if status not in LIFECYCLE_STATUSES:
        raise CatalogError(f"lifecycle status must be one of {', '.join(LIFECYCLE_STATUSES)}")
    source_kind, source_ref = _source("lifecycle", raw)
    read_on = str(raw.get("read_on") or "").strip()
    if not _DAY.match(read_on):
        raise CatalogError("lifecycle read_on is the day it was read, YYYY-MM-DD")
    out: dict[str, Any] = {
        "status": status,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "read_on": read_on,
        "needs_review": source_kind == "assumption",
    }
    last_buy = str(raw.get("last_time_buy") or "").strip()
    if last_buy:
        if not _DAY.match(last_buy):
            raise CatalogError("lifecycle last_time_buy is a day, YYYY-MM-DD")
        out["last_time_buy"] = last_buy
    return out


def parse_ecad(raw: Any) -> dict[str, Any]:
    """{symbol, footprint, source}: the KiCad library ids the part is drawn with."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CatalogError("ecad must be an object with symbol, footprint and source")
    out: dict[str, Any] = {}
    for key in ("symbol", "footprint"):
        value = str(raw.get(key) or "").strip()
        if value and not _KICAD_ID.match(value):
            raise CatalogError(f"ecad {key} is a KiCad id, Library:Name, e.g. Device:R")
        if value:
            out[key] = value
    if not out:
        raise CatalogError("ecad needs a symbol, a footprint or both")
    source_kind, source_ref = _source("ecad", raw)
    out.update(
        source_kind=source_kind,
        source_ref=source_ref,
        needs_review=source_kind == "assumption",
    )
    return out


# -- the check --------------------------------------------------------------------


def _item(
    key: str, label: str, status: str, detail: str = "", *, required: bool = True
) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, "required": required}


def _ratings_item(ratings: list[dict[str, Any]], kind: str, label: str) -> dict[str, Any]:
    rows = [r for r in ratings if r.get("kind") == kind]
    if not rows:
        return _item(f"ratings_{kind}", label, "missing", "none recorded from the datasheet")
    assumed = [r["param"] for r in rows if r.get("needs_review")]
    if assumed:
        detail = f"assumed, not from the datasheet: {', '.join(assumed)}"
        return _item(f"ratings_{kind}", label, "review", detail)
    return _item(f"ratings_{kind}", label, "ok", f"{len(rows)} rows")


def _models(entry: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        m
        for m in entry.get("models") or []
        if m.get("kind") == kind and m.get("status", "active") != "withdrawn"
    ]


def _has_3d(entry: dict[str, Any]) -> bool:
    geometry = entry.get("geometry") or {}
    return bool(
        _models(entry, "cad")
        or geometry.get("step_url")
        or geometry.get("step_work_product_id")
        or geometry.get("usd_url")
        or geometry.get("generator")
    )


def _offer_item(entry: dict[str, Any], today: date) -> dict[str, Any]:
    offers = [
        s.get("offer") or {}
        for s in entry.get("suppliers") or []
        if isinstance(s, dict) and s.get("offer")
    ]
    if not offers:
        return _item("offer", "Price and stock", "missing", "no supplier offer recorded")
    days = []
    for offer in offers:
        read = str(offer.get("read_on") or "")
        if _DAY.match(read):
            days.append((today - date.fromisoformat(read)).days)
    if not days:
        return _item("offer", "Price and stock", "review", "offer has no read date")
    newest = min(days)
    if newest > 30:
        return _item("offer", "Price and stock", "review", f"newest offer is {newest} days old")
    return _item("offer", "Price and stock", "ok", f"read {newest} days ago")


def _lifecycle_item(lifecycle: dict[str, Any], today: date) -> dict[str, Any]:
    if not lifecycle:
        return _item("lifecycle", "Lifecycle", "missing", "not recorded")
    status = lifecycle.get("status", "unknown")
    read = str(lifecycle.get("read_on") or "")
    age = (today - date.fromisoformat(read)).days if _DAY.match(read) else None
    if status in ("eol", "obsolete"):
        said = lifecycle.get("source_ref") or lifecycle.get("source_kind")
        detail = f"{status.upper()} per {said}"
        return _item("lifecycle", "Lifecycle", "bad", detail)
    if status == "unknown" or lifecycle.get("needs_review"):
        return _item("lifecycle", "Lifecycle", "review", f"{status}, not confirmed")
    if age is not None and age > LIFECYCLE_STALE_DAYS:
        return _item("lifecycle", "Lifecycle", "review", f"{status}, read {age} days ago")
    return _item("lifecycle", "Lifecycle", "ok", status.upper() if status == "nrnd" else status)


def check_part_data(entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """What a library part has and lacks to be relied on in a design.

    ``entry`` is the entry as a dict (``ComponentEntry.as_dict``). Each item is
    ``ok``, ``review`` (present but assumed or stale), ``missing`` or ``bad``
    (an ended part). ``complete`` means every required item is ``ok``.
    """
    day = today or date.today()
    family = str(entry.get("family") or "")
    electronic = family in _ELECTRONIC
    unit = family in _UNIT
    if not (electronic or unit):
        return {
            "applies": False,
            "complete": None,
            "items": [],
            "summary": (
                "part data is checked for electronic parts, modules and batteries, "
                f"not {family or 'this family'}"
            ),
        }
    ratings = list(entry.get("ratings") or [])
    ecad = dict(entry.get("ecad") or {})
    items = [
        _item(
            "mpn",
            "Exact part number",
            "ok" if entry.get("mpn") and entry.get("manufacturer") else "missing",
            entry.get("mpn") or "no manufacturer part number",
        ),
        _item(
            "datasheet",
            "Datasheet",
            "ok" if entry.get("datasheet") else "missing",
            "linked" if entry.get("datasheet") else "no datasheet link",
        ),
        _ratings_item(ratings, "abs_max", "Absolute maximum ratings"),
        _ratings_item(ratings, "recommended", "Recommended operating conditions"),
        _lifecycle_item(dict(entry.get("lifecycle") or {}), day),
        _offer_item(entry, day),
        _item(
            "model_3d",
            "3D model",
            "ok" if _has_3d(entry) else "missing",
            "" if _has_3d(entry) else "no STEP or drawn model",
        ),
    ]
    if electronic:
        for key, label in (("symbol", "KiCad symbol"), ("footprint", "KiCad footprint")):
            value = ecad.get(key)
            status = "ok" if value else "missing"
            if value and ecad.get("needs_review"):
                status = "review"
            items.append(_item(f"ecad_{key}", label, status, value or "not recorded"))
        spice = _models(entry, "spice")
        items.append(
            _item(
                "spice",
                "SPICE model",
                "ok" if spice else "missing",
                f"{spice[0]['model_id']} v{spice[0]['version']}" if spice else "none attached",
                required=False,
            )
        )
    required = [i for i in items if i["required"]]
    complete = all(i["status"] == "ok" for i in required)
    gaps = [i["label"] for i in required if i["status"] != "ok"]
    summary = "complete" if complete else f"{len(gaps)} of {len(required)} to do: {', '.join(gaps)}"
    return {"applies": True, "complete": complete, "items": items, "summary": summary}
