"""Explicit unit conversion for catalog spec fields (MET-436).

No ``pint`` dependency exists in this repo (checked repo-wide). This
module is deliberately small and **fails closed**: an unrecognized
``(from_unit, to_unit)`` pair raises ``UnitConversionError`` rather than
guessing a scale factor — a silently-wrong 1000x scale error on
``i_out_max`` is worse than a dropped field, since it would corrupt a
range filter with no signal anywhere downstream.

``efficiency``-type fields canonicalize to a 0-1 ratio (``"92%"`` ->
``0.92``), not a percentage, so a query like ``efficiency > 0.9`` is
directly usable without a caller-side percent/ratio conversion.
"""

from __future__ import annotations

import re
from typing import Any


class UnitConversionError(ValueError):
    """Raised when a value's unit can't be safely converted to the field's canonical unit."""


# (from_unit, to_unit) -> multiply-by factor. Both directions are listed
# explicitly (not derived) so the table stays auditable at a glance —
# see taxonomy.py's Risks note on unit-conversion correctness.
_SCALE: dict[tuple[str, str], float] = {
    ("mV", "V"): 1e-3,
    ("V", "mV"): 1e3,
    ("kV", "V"): 1e3,
    ("V", "kV"): 1e-3,
    ("uA", "A"): 1e-6,
    ("A", "uA"): 1e6,
    ("mA", "A"): 1e-3,
    ("A", "mA"): 1e3,
    ("mW", "W"): 1e-3,
    ("W", "mW"): 1e3,
    ("Hz", "kHz"): 1e-3,
    ("kHz", "Hz"): 1e3,
    ("Hz", "MHz"): 1e-6,
    ("MHz", "Hz"): 1e6,
    ("MHz", "GHz"): 1e-3,
    ("GHz", "MHz"): 1e3,
    ("Hz", "GHz"): 1e-9,
    ("GHz", "Hz"): 1e9,
    ("kHz", "MHz"): 1e-3,
    ("MHz", "kHz"): 1e3,
    ("mohm", "ohm"): 1e-3,
    ("ohm", "mohm"): 1e3,
    ("kohm", "ohm"): 1e3,
    ("ohm", "kohm"): 1e-3,
    ("Mohm", "ohm"): 1e6,
    ("ohm", "Mohm"): 1e-6,
    ("pF", "F"): 1e-12,
    ("F", "pF"): 1e12,
    ("nF", "F"): 1e-9,
    ("F", "nF"): 1e9,
    ("uF", "F"): 1e-6,
    ("F", "uF"): 1e6,
    ("nH", "H"): 1e-9,
    ("H", "nH"): 1e9,
    ("uH", "H"): 1e-6,
    ("H", "uH"): 1e6,
    ("mH", "H"): 1e-3,
    ("H", "mH"): 1e3,
    ("%", "ratio"): 1e-2,
    ("ratio", "%"): 1e2,
    ("mm", "cm"): 1e-1,
    ("cm", "mm"): 1e1,
    ("g", "kg"): 1e-3,
    ("kg", "g"): 1e3,
    # weight_g is its own canonical unit distinct from plain "g" (which
    # elsewhere means g-force / gravitational acceleration, see imu's
    # accel_range_max) — no scale needed, but list identity explicitly
    # so a mismatched "g_accel" -> "weight_g" request fails loudly
    # instead of silently no-op'ing through the identity short-circuit.
    ("g", "weight_g"): 1.0,
    ("weight_g", "g"): 1.0,
}

# Common datasheet spellings that must collapse to the canonical symbol
# used in ``_SCALE`` above before lookup (Unicode Ω/µ vs. ASCII ohm/u).
_UNIT_ALIASES: dict[str, str] = {
    "Ω": "ohm",
    "kΩ": "kohm",
    "MΩ": "Mohm",
    "mΩ": "mohm",
    "µF": "uF",
    "μF": "uF",
    "µH": "uH",
    "μH": "uH",
    "µA": "uA",
    "μA": "uA",
}


def _canon_unit(unit: str) -> str:
    return _UNIT_ALIASES.get(unit, unit)


_NUM_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def _coerce_numeric(raw: str) -> float:
    match = _NUM_RE.match(raw)
    if not match:
        raise UnitConversionError(f"no leading numeric token in {raw!r}")
    return float(match.group(1))


def normalize_value(
    raw: str,
    *,
    from_unit: str | None,
    to_unit: str | None,
    value_type: str,
    enum_values: tuple[str, ...] | None = None,
) -> Any:
    """Coerce a datasheet-extracted string to a field's canonical type/unit.

    Fails closed: raises ``UnitConversionError`` on an unrecognized
    ``(from_unit, to_unit)`` pair rather than guessing a scale factor —
    callers (``indexer.py``) treat that the same as a missing value.
    """
    text = raw.strip()
    if not text:
        raise UnitConversionError("empty value")

    if value_type == "bool":
        lowered = text.lower()
        if lowered in ("true", "yes", "1", "y"):
            return True
        if lowered in ("false", "no", "0", "n"):
            return False
        raise UnitConversionError(f"cannot coerce {raw!r} to bool")

    if value_type == "enum":
        if enum_values:
            if text in enum_values:
                return text
            for candidate in enum_values:
                if candidate.lower() == text.lower():
                    return candidate
            raise UnitConversionError(f"{raw!r} is not one of {enum_values!r}")
        return text

    if value_type == "str":
        return text

    if value_type not in ("float", "int"):
        raise UnitConversionError(f"unsupported value_type {value_type!r}")

    number = _coerce_numeric(text)
    if from_unit and to_unit:
        from_c, to_c = _canon_unit(from_unit), _canon_unit(to_unit)
        if from_c != to_c:
            scale = _SCALE.get((from_c, to_c))
            if scale is None:
                raise UnitConversionError(f"no known conversion from {from_unit!r} to {to_unit!r}")
            number *= scale

    return int(round(number)) if value_type == "int" else number


__all__ = ["UnitConversionError", "normalize_value"]
