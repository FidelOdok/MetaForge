"""Hand-authored subsystem role templates for build-vs-buy intent search (MET-436).

Role inference for a subsystem-shaped intent ("a flight controller for a
250mm quad") is genuinely hard to do reliably with a pure LLM enumeration —
miss a role (no reverse-polarity protection, no ESD array) and the
"shopping list" is silently incomplete with no signal to the caller that
anything is missing. This module is the deterministic half of the
template-first, LLM-gap-filler design: a small, versioned table of known
subsystems mapping each to its constituent roles and the catalog category
each role resolves to. ``intent_search.py`` seeds from the LLM's own
answer and merges in any template roles the LLM didn't mention, flagging
the difference so callers can distinguish "known-complete list" from
"best guess."

Deliberately excludes passive components (crystals, MLCCs, resistors) and
connectors even though a real BOM has them (see
``examples/drone_flight_controller/bom/bom.json``) — a shopping list at
that granularity is closer to schematic synthesis than component search,
a different feature. Every category name referenced below must exist in
``digital_twin.catalog.taxonomy.CATEGORY_REGISTRY`` — enforced by
``tests/unit/test_subsystem_templates.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubsystemRole:
    """One functional slot in a subsystem, resolved to a catalog category."""

    role: str
    category: str
    required: bool = True


@dataclass(frozen=True)
class SubsystemTemplate:
    """The role list for one known subsystem, plus its COTS-assembly category if one exists."""

    subsystem: str
    cots_category: str | None
    roles: tuple[SubsystemRole, ...]


# Seeded from examples/drone_flight_controller/bom/bom.json's actual
# U1-U7/Q1 parts: STM32F405 (mcu), MPU-6050 (imu), BMP280 (barometer),
# NEO-M8N (gps), TPS54302 (buck_converter), an LDO, a TVS array
# (esd_protection), and a reverse-polarity MOSFET.
FLIGHT_CONTROLLER = SubsystemTemplate(
    subsystem="flight_controller",
    cots_category="flight_controller",
    roles=(
        SubsystemRole("mcu", "microcontroller"),
        SubsystemRole("imu", "imu"),
        SubsystemRole("barometer", "pressure", required=False),
        SubsystemRole("gps", "gps_module", required=False),
        SubsystemRole("buck_converter", "buck_converter"),
        SubsystemRole("ldo", "ldo"),
        SubsystemRole("esd_protection", "tvs_diode", required=False),
        SubsystemRole("reverse_polarity_protection", "mosfet", required=False),
    ),
)

KNOWN_SUBSYSTEMS: dict[str, SubsystemTemplate] = {
    FLIGHT_CONTROLLER.subsystem: FLIGHT_CONTROLLER,
}

__all__ = ["KNOWN_SUBSYSTEMS", "FLIGHT_CONTROLLER", "SubsystemRole", "SubsystemTemplate"]
