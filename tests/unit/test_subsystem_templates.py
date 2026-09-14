"""Unit tests for subsystem role templates (MET-436).

The main invariant: every category name referenced by a template must
actually exist in the taxonomy — a template referencing a typo'd or
since-renamed category would silently produce dead-end searches.
"""

from __future__ import annotations

from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY
from digital_twin.knowledge.subsystem_templates import FLIGHT_CONTROLLER, KNOWN_SUBSYSTEMS


def test_every_template_role_category_exists_in_taxonomy() -> None:
    for template in KNOWN_SUBSYSTEMS.values():
        for role in template.roles:
            assert role.category in CATEGORY_REGISTRY, (
                f"subsystem {template.subsystem!r} role {role.role!r} references "
                f"unknown category {role.category!r}"
            )


def test_every_template_cots_category_exists_in_taxonomy() -> None:
    for template in KNOWN_SUBSYSTEMS.values():
        if template.cots_category is not None:
            assert template.cots_category in CATEGORY_REGISTRY


def test_template_cots_category_is_actually_a_cots_assembly() -> None:
    for template in KNOWN_SUBSYSTEMS.values():
        if template.cots_category is not None:
            spec = CATEGORY_REGISTRY[template.cots_category]
            assert spec.purchase_unit == "cots_assembly"


def test_flight_controller_template_registered() -> None:
    assert KNOWN_SUBSYSTEMS["flight_controller"] is FLIGHT_CONTROLLER


def test_flight_controller_roles_seeded_from_reference_bom() -> None:
    role_names = {r.role for r in FLIGHT_CONTROLLER.roles}
    assert role_names == {
        "mcu",
        "imu",
        "barometer",
        "gps",
        "buck_converter",
        "ldo",
        "esd_protection",
        "reverse_polarity_protection",
    }


def test_flight_controller_excludes_passives_and_connectors() -> None:
    """Deliberate scope boundary — a shopping list at passive/connector
    granularity is schematic synthesis, a different feature."""
    role_categories = {r.category for r in FLIGHT_CONTROLLER.roles}
    passive_categories = {"resistor", "capacitor_ceramic", "capacitor_electrolytic", "inductor"}
    connector_categories = {"board_to_board", "wire_to_board", "usb", "rf_connector"}
    assert role_categories.isdisjoint(passive_categories)
    assert role_categories.isdisjoint(connector_categories)


def test_no_duplicate_roles_within_a_template() -> None:
    for template in KNOWN_SUBSYSTEMS.values():
        role_names = [r.role for r in template.roles]
        assert len(role_names) == len(set(role_names)), (
            f"subsystem {template.subsystem!r} has duplicate role names"
        )
