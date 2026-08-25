"""Unit tests for the parametric catalog taxonomy (MET-436)."""

from __future__ import annotations

import pytest

from digital_twin.catalog.taxonomy import (
    CATEGORY_REGISTRY,
    CategorySpec,
    _register,
    categories_by_purchase_unit,
    sf,
    spec_model,
)

EXPECTED_CATEGORIES = {
    "buck_converter", "boost_converter", "ldo", "battery_charger",
    "battery_protection", "power_mux",
    "resistor", "capacitor_ceramic", "capacitor_electrolytic", "inductor", "ferrite_bead",
    "microcontroller", "microprocessor", "fpga", "memory",
    "imu", "pressure", "temperature", "current_sense", "optical",
    "board_to_board", "wire_to_board", "usb", "rf_connector",
    "wifi_module", "ble_module", "gps_module", "rf_amplifier",
    "motor_driver", "esc", "servo",
    "tvs_diode", "fuse", "esd_protection",
    "level_shifter", "mux_demux", "gate_driver",
    "mosfet", "bjt", "diode_rectifier",
    "flight_controller", "telemetry_radio", "osd_module",
}


def test_registry_has_full_taxonomy():
    assert EXPECTED_CATEGORIES.issubset(CATEGORY_REGISTRY.keys())
    assert len(CATEGORY_REGISTRY) >= 40


@pytest.mark.parametrize("category", sorted(EXPECTED_CATEGORIES))
def test_every_category_has_fields_and_valid_purchase_unit(category: str):
    spec = CATEGORY_REGISTRY[category]
    assert spec.fields, f"{category} has no fields"
    assert spec.purchase_unit in ("discrete_part", "cots_assembly")


def test_cots_assemblies_are_the_expected_subset():
    cots = set(categories_by_purchase_unit("cots_assembly"))
    assert cots == {"flight_controller", "telemetry_radio", "osd_module", "esc", "servo"}


def test_discrete_parts_exclude_cots_assemblies():
    discrete = set(categories_by_purchase_unit("discrete_part"))
    cots = set(categories_by_purchase_unit("cots_assembly"))
    assert discrete & cots == set()
    assert discrete | cots == set(CATEGORY_REGISTRY.keys())


def test_buck_converter_and_flight_controller_share_no_required_field_names():
    """Proves the disjoint-attribute-set design: a discrete part and a
    COTS assembly should have essentially no overlap in their defining
    (required) fields."""
    buck_required = {f.name for f in CATEGORY_REGISTRY["buck_converter"].required_fields()}
    fc_required = {f.name for f in CATEGORY_REGISTRY["flight_controller"].required_fields()}
    assert buck_required & fc_required == set()


def test_category_field_lookup():
    spec = CATEGORY_REGISTRY["buck_converter"]
    assert spec.field("v_out") is not None
    assert spec.field("does_not_exist") is None


def test_queryable_fields_excludes_non_queryable():
    spec = CATEGORY_REGISTRY["flight_controller"]
    queryable_names = {f.name for f in spec.queryable_fields()}
    assert "processor" not in queryable_names  # queryable=False in taxonomy.py
    assert "weight_g" in queryable_names


def test_is_indexed_defaults_to_queryable():
    always_indexed = sf("x", "float")
    assert always_indexed.is_indexed is True
    explicitly_not_indexed = sf("y", "float", indexed=False)
    assert explicitly_not_indexed.is_indexed is False
    not_queryable = sf("z", "str", queryable=False)
    assert not_queryable.is_indexed is False


# ---------------------------------------------------------------------------
# spec_model() — Pydantic model generation
# ---------------------------------------------------------------------------


def test_spec_model_generates_working_pydantic_model():
    model = spec_model("buck_converter")
    instance = model(
        v_in_min=4.5, v_in_max=28.0, v_out=5.0, i_out_max=2.0,
        efficiency=0.92, package="QFN-24",
    )
    assert instance.v_out == 5.0
    assert instance.package == "QFN-24"


def test_spec_model_enforces_required_fields():
    model = spec_model("buck_converter")
    with pytest.raises(Exception):  # pydantic.ValidationError
        model(v_out=5.0)  # missing v_in_min/v_in_max/i_out_max/package


def test_spec_model_allows_omitting_optional_fields():
    model = spec_model("buck_converter")
    instance = model(v_in_min=4.5, v_in_max=28.0, i_out_max=2.0, package="QFN-24")
    assert instance.v_out is None


def test_spec_model_enum_field_rejects_unknown_value():
    model = spec_model("buck_converter")
    with pytest.raises(Exception):  # pydantic.ValidationError
        model(v_in_min=4.5, v_in_max=28.0, i_out_max=2.0, package="NOT-A-REAL-PACKAGE")


def test_spec_model_is_cached():
    assert spec_model("buck_converter") is spec_model("buck_converter")


def test_spec_model_unknown_category_raises_keyerror():
    with pytest.raises(KeyError):
        spec_model("not_a_real_category")


# ---------------------------------------------------------------------------
# _register() validation
# ---------------------------------------------------------------------------


def test_register_rejects_bad_category_name():
    with pytest.raises(ValueError):
        _register("Bad-Name!", "discrete_part", sf("x", "float"))


def test_register_rejects_duplicate_category():
    with pytest.raises(ValueError):
        _register("buck_converter", "discrete_part", sf("x", "float"))


def test_register_rejects_duplicate_field_name():
    with pytest.raises(ValueError):
        _register(
            "_test_only_dup_field", "discrete_part",
            sf("x", "float"), sf("x", "int"),
        )
    CATEGORY_REGISTRY.pop("_test_only_dup_field", None)  # never actually registered, but be safe


def test_sf_enum_type_requires_enum_values():
    with pytest.raises(ValueError):
        sf("x", "enum")


def test_category_spec_and_spec_field_are_frozen():
    field_obj = sf("x", "float")
    with pytest.raises(Exception):
        field_obj.name = "y"  # type: ignore[misc]

    spec = CategorySpec(name="x", purchase_unit="discrete_part", fields=(field_obj,))
    with pytest.raises(Exception):
        spec.name = "y"  # type: ignore[misc]
