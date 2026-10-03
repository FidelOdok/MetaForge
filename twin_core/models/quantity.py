"""Quantity -- a value with a unit and optional uncertainty, backed by
``pint`` for REAL dimensional-consistency checking (FORGE-311, lifecycle
step 1, spec section 30 guardrail "attach units to physical quantities and
enforce dimensional consistency").

Closes a real gap, not a hypothetical one: today, ``twin_core.consistency.
metrics.compute_metric_total`` reads a metric's value by an EXACT metadata
key match (``f"{metric}_{unit}"``, e.g. ``"mass_kg"``). A WorkProduct that
stores the same physical quantity under a different -- but dimensionally
compatible -- unit (``"mass_g"``) is invisible to it: the key lookup misses
and that data silently contributes 0, indistinguishable from genuinely
absent data. A key that names an outright incompatible dimension
(``"mass_mm"``, length where a mass was expected) fails exactly the same
way -- silently, not as an error. ``Quantity`` lets a caller convert between
compatible units correctly, and raise a clear, specific error rather than a
wrong answer when the dimensions don't match at all.

Not a general physics/units library wrapper: this module exposes exactly
the surface `twin_core`/`api_gateway` recorders and consistency engines need
(construct, validate the unit string is real, convert, round-trip to/from a
plain JSON-compatible dict for storage in a ``Constraint``/``EngineeringEntity``
``metadata`` dict) -- everything else is delegated straight to ``pint``.
"""

from __future__ import annotations

from typing import Any

import pint
from pydantic import BaseModel, field_validator

# One shared registry (pint's own recommended pattern -- registries are not
# meant to be instantiated per call; parsing/caching cost is paid once).
_UREG = pint.UnitRegistry()

# Currency isn't a physical quantity pint knows about by default, but this
# codebase already uses a bare currency code as a Budget/Invariant `unit`
# value (metric="cost", unit="usd" -- e.g. component_recorder.py's
# unit_cost_usd, the dashboard's own currency-symbol set). Each currency
# gets its OWN independent dimension rather than one shared "currency"
# dimension: this codebase does no real foreign-exchange conversion, so
# treating usd/gbp as the same dimension at a bogus 1:1 factor would be a
# silently wrong number, worse than the pre-FORGE-311 behavior of simply not
# finding mismatched data. Making them mutually incompatible means a
# cross-currency mix is correctly rejected (real conversion needs an
# exchange rate, not a unit factor) while same-currency data still works.
for _code in ("usd", "gbp", "eur", "jpy"):
    _UREG.define(f"{_code} = [{_code}_currency]")


class IncompatibleUnitsError(ValueError):
    """Raised when converting between (or otherwise comparing) two units of
    different physical dimension -- e.g. a length where a mass was expected.
    A ``ValueError`` subclass so it's caught by this codebase's existing
    ``except ValueError`` graceful-degrade convention (``twin_core.
    consistency.gates``'s ``_load_budgets``/``_load_invariants``, etc.)
    without those call sites needing to know about this module at all.
    """

    def __init__(self, from_unit: str, to_unit: str, *, context: str | None = None) -> None:
        self.from_unit = from_unit
        self.to_unit = to_unit
        message = f"cannot convert {from_unit!r} to {to_unit!r} -- incompatible physical dimensions"
        if context:
            message = f"{context}: {message}"
        super().__init__(message)


def is_currency_code(unit: str) -> bool:
    """Whether ``unit`` is an ISO 4217 style currency code (``GBP``, ``USD``).

    Currency is a label, not a pint unit (FORGE-515): there is no exchange
    rate, so values are only comparable within one code (see ``same_currency``).
    """
    code = unit.strip() if unit else ""
    return len(code) == 3 and code.isascii() and code.isalpha() and code.isupper()


def same_currency(a: str, b: str) -> bool:
    """Whether two currency codes name the same currency."""
    return is_currency_code(a) and is_currency_code(b) and a.strip() == b.strip()


def is_valid_unit(unit: str) -> bool:
    """Whether ``unit`` is a real, parseable pint unit string (e.g. ``"kg"``,
    ``"mm"``, ``"N*m"``) -- never raises. An empty/whitespace-only string is
    always rejected: pint parses it as dimensionless, but in this codebase
    that's never a deliberate "no units" declaration, only a caller that
    forgot to set one."""
    if not unit or not unit.strip():
        return False
    try:
        _UREG.parse_units(unit)
    except Exception:  # noqa: BLE001 -- pint raises several distinct error types
        return False
    return True


class Quantity(BaseModel):
    """A physical value with a unit and optional uncertainty (same unit as
    ``value``). Deliberately NOT a Constraint/EngineeringEntity field type
    swap in this ticket -- both of those keep their existing plain
    ``float``/``str`` fields; this is the conversion+validation primitive
    those fields' *values* are checked and converted through."""

    value: float
    unit: str
    uncertainty: float | None = None

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, v: str) -> str:
        if not is_valid_unit(v):
            raise ValueError(f"{v!r} is not a recognized unit")
        return v

    def to(self, unit: str) -> Quantity:
        """Convert to ``unit``. Raises ``IncompatibleUnitsError`` when the
        target unit is a different physical dimension -- never silently
        returns a wrong number."""
        try:
            converted = _UREG.Quantity(self.value, self.unit).to(unit)
        except pint.DimensionalityError as exc:
            raise IncompatibleUnitsError(self.unit, unit) from exc
        new_uncertainty = None
        if self.uncertainty is not None:
            # Uncertainty is in the same unit as value, so it scales by the
            # same conversion factor.
            new_uncertainty = _UREG.Quantity(self.uncertainty, self.unit).to(unit).magnitude
        return Quantity(value=converted.magnitude, unit=unit, uncertainty=new_uncertainty)

    def compatible_with(self, unit: str) -> bool:
        """Whether ``unit`` is the same physical dimension as this quantity's
        own unit (a same-dimension check with no conversion side effect)."""
        try:
            _UREG.Quantity(1.0, self.unit).to(unit)
        except pint.DimensionalityError:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible round-trip form for ``Constraint``/
        ``EngineeringEntity`` ``metadata`` dicts."""
        data: dict[str, Any] = {"value": self.value, "unit": self.unit}
        if self.uncertainty is not None:
            data["uncertainty"] = self.uncertainty
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quantity:
        return cls(
            value=float(data["value"]),
            unit=str(data["unit"]),
            uncertainty=(
                float(data["uncertainty"]) if data.get("uncertainty") is not None else None
            ),
        )
