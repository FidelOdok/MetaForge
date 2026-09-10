"""Declarative category taxonomy for the parametric component catalog (MET-436).

A ``flight_controller`` (a COTS assembly, e.g. a CubeOrange) shares almost
no attributes with a ``buck_converter`` (a discrete part) — imu_count and
weight_g vs. v_out and efficiency. One flat table with a fixed column set
can't hold both without most rows being mostly NULL. This module is the
fix: a small, declarative registry of per-category field definitions
(``CategorySpec``/``SpecField``) that generates both a typed Pydantic
validation model (via ``spec_model()``) and per-field query/index behaviour
(consumed by ``digital_twin.catalog.store.schema_statements`` and
``digital_twin.catalog.query.build_sql``) — without hand-writing one
Pydantic class per category.

Adding category #44 is one ``_register(...)`` call with a handful of
``sf(...)`` field definitions — no new class, no schema migration tool
needed, since specs live in a JSONB column (see ``store.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

PurchaseUnit = Literal["discrete_part", "cots_assembly"]
FieldType = Literal["float", "int", "str", "bool", "enum"]


@dataclass(frozen=True)
class SpecField:
    """One typed, optionally range-queryable attribute of a component category."""

    name: str
    type: FieldType
    unit: str | None = None
    enum_values: tuple[str, ...] | None = None
    required: bool = False
    queryable: bool = True
    """Whether this field is allowed in the filter DSL at all."""
    indexed: bool | None = None
    """Whether ``store.schema_statements()`` builds an expression index for
    this field. ``None`` (the common case) defaults to ``queryable`` — set
    explicitly to ``False`` on low-value fields to control index count on
    a category with many attributes."""
    aliases: tuple[str, ...] = ()
    """Alternative datasheet labels fed to ``extract_properties_for_mpn``'s
    ``aliases`` map (e.g. ``["output_voltage", "vout"]`` for ``v_out``)."""
    description: str = ""

    @property
    def is_indexed(self) -> bool:
        return self.queryable if self.indexed is None else self.indexed


@dataclass(frozen=True)
class CategorySpec:
    """The full set of ``SpecField``s for one component category."""

    name: str
    purchase_unit: PurchaseUnit
    fields: tuple[SpecField, ...]

    def field(self, name: str) -> SpecField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def queryable_fields(self) -> tuple[SpecField, ...]:
        return tuple(f for f in self.fields if f.queryable)

    def required_fields(self) -> tuple[SpecField, ...]:
        return tuple(f for f in self.fields if f.required)


CATEGORY_REGISTRY: dict[str, CategorySpec] = {}

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def sf(
    name: str,
    type_: FieldType,
    *,
    unit: str | None = None,
    required: bool = False,
    queryable: bool = True,
    indexed: bool | None = None,
    enum: list[str] | tuple[str, ...] | None = None,
    aliases: tuple[str, ...] | list[str] = (),
    desc: str = "",
) -> SpecField:
    """Convenience constructor for one ``SpecField`` (keeps `_register` calls terse)."""
    if type_ == "enum" and not enum:
        raise ValueError(f"field {name!r} declares type='enum' but no enum values were given")
    return SpecField(
        name=name,
        type=type_,
        unit=unit,
        enum_values=tuple(enum) if enum else None,
        required=required,
        queryable=queryable,
        indexed=indexed,
        aliases=tuple(aliases),
        description=desc,
    )


def _register(name: str, purchase_unit: PurchaseUnit, *fields: SpecField) -> None:
    if not _VALID_NAME.match(name):
        raise ValueError(
            f"category name {name!r} must be lowercase snake_case (^[a-z][a-z0-9_]*$) "
            "— it is interpolated directly into DDL/index names"
        )
    if name in CATEGORY_REGISTRY:
        raise ValueError(f"category {name!r} is already registered")
    seen: set[str] = set()
    for f in fields:
        if not _VALID_NAME.match(f.name):
            raise ValueError(f"field name {f.name!r} in category {name!r} is not a safe identifier")
        if f.name in seen:
            raise ValueError(f"duplicate field {f.name!r} in category {name!r}")
        seen.add(f.name)
    CATEGORY_REGISTRY[name] = CategorySpec(
        name=name, purchase_unit=purchase_unit, fields=tuple(fields)
    )


# ---------------------------------------------------------------------------
# Power Management
# ---------------------------------------------------------------------------

_register(
    "buck_converter",
    "discrete_part",
    sf("v_in_min", "float", unit="V", required=True, desc="Minimum input voltage"),
    sf("v_in_max", "float", unit="V", required=True, desc="Maximum input voltage"),
    sf(
        "v_out",
        "float",
        unit="V",
        aliases=("output_voltage", "vout"),
        desc="Nominal output voltage",
    ),
    sf(
        "adjustable_output",
        "bool",
        indexed=False,
        desc="True if v_out is set by external resistors",
    ),
    sf("i_out_max", "float", unit="A", required=True, aliases=("output_current", "iout_max")),
    sf("efficiency", "float", unit="ratio", aliases=("peak_efficiency", "efficiency_at_full_load")),
    sf("switching_frequency", "float", unit="kHz", indexed=False),
    sf("quiescent_current", "float", unit="uA", required=False, indexed=False),
    sf("package", "enum", enum=("QFN-16", "QFN-24", "QFN-28", "SOT-23-6", "SO-8"), required=True),
)

_register(
    "boost_converter",
    "discrete_part",
    sf("v_in_min", "float", unit="V", required=True),
    sf("v_in_max", "float", unit="V", required=True),
    sf("v_out_max", "float", unit="V", required=True, aliases=("output_voltage",)),
    sf("i_out_max", "float", unit="A", required=True),
    sf("efficiency", "float", unit="ratio"),
    sf("package", "enum", enum=("QFN-16", "SOT-23-6", "SO-8"), required=True),
)

_register(
    "ldo",
    "discrete_part",
    sf("v_in_max", "float", unit="V", required=True),
    sf("v_out", "float", unit="V", required=True, aliases=("output_voltage",)),
    sf("i_out_max", "float", unit="A", required=True),
    sf("dropout_voltage", "float", unit="V", indexed=False),
    sf("package", "enum", enum=("SOT-23-5", "SOT-89", "TO-220"), required=True),
)

_register(
    "battery_charger",
    "discrete_part",
    sf("v_in_max", "float", unit="V", required=True),
    sf("i_charge_max", "float", unit="A", required=True),
    sf("chemistry", "enum", enum=("Li-ion", "LiPo", "NiMH"), required=True),
    sf("package", "enum", enum=("QFN-16", "SOT-23-6"), required=True),
)

_register(
    "battery_protection",
    "discrete_part",
    sf("v_max", "float", unit="V", required=True),
    sf("i_max", "float", unit="A", required=True),
    sf("overdischarge_threshold", "float", unit="V", indexed=False),
    sf("package", "enum", enum=("SOT-23-6", "DFN-8"), required=True),
)

_register(
    "power_mux",
    "discrete_part",
    sf("v_in_max", "float", unit="V", required=True),
    sf("i_out_max", "float", unit="A", required=True),
    sf("r_on", "float", unit="ohm", indexed=False),
    sf("package", "enum", enum=("SOT-23-6", "QFN-10"), required=True),
)

# ---------------------------------------------------------------------------
# Passives
# ---------------------------------------------------------------------------

_register(
    "resistor",
    "discrete_part",
    sf("resistance_ohm", "float", unit="ohm", required=True, aliases=("resistance",)),
    sf("tolerance_pct", "float", unit="%", required=True, aliases=("tolerance",)),
    sf("power_rating", "float", unit="W", required=True, aliases=("power_dissipation",)),
    sf("package", "enum", enum=("0402", "0603", "0805", "1206"), required=True),
    sf("tempco", "float", unit="ppm/C", required=False, indexed=False),
)

_register(
    "capacitor_ceramic",
    "discrete_part",
    sf("capacitance", "float", unit="F", required=True),
    sf("voltage_rating", "float", unit="V", required=True),
    sf("dielectric", "enum", enum=("X5R", "X7R", "C0G"), required=True),
    sf("package", "enum", enum=("0402", "0603", "0805", "1206"), required=True),
)

_register(
    "capacitor_electrolytic",
    "discrete_part",
    sf("capacitance", "float", unit="F", required=True),
    sf("voltage_rating", "float", unit="V", required=True),
    sf("esr", "float", unit="ohm", indexed=False),
    sf("package", "enum", enum=("radial", "SMD-can"), required=True),
)

_register(
    "inductor",
    "discrete_part",
    sf("inductance", "float", unit="H", required=True),
    sf("current_rating", "float", unit="A", required=True),
    sf("dcr", "float", unit="ohm", indexed=False),
    sf("package", "enum", enum=("0805", "1210", "SMD-shielded"), required=True),
)

_register(
    "ferrite_bead",
    "discrete_part",
    sf("impedance_100mhz", "float", unit="ohm", required=True),
    sf("current_rating", "float", unit="A", required=True),
    sf("package", "enum", enum=("0402", "0603", "0805"), required=True),
)

# ---------------------------------------------------------------------------
# MCU / Processing
# ---------------------------------------------------------------------------

_register(
    "microcontroller",
    "discrete_part",
    sf(
        "core_architecture",
        "enum",
        enum=("ARM Cortex-M0", "ARM Cortex-M4", "ARM Cortex-M7", "RISC-V", "AVR"),
        required=True,
    ),
    sf("clock_speed_max", "float", unit="MHz", required=True),
    sf("flash_size", "int", unit="kB", required=True),
    sf("ram_size", "int", unit="kB", required=True),
    sf("operating_voltage_min", "float", unit="V", indexed=False),
    sf("operating_voltage_max", "float", unit="V", indexed=False),
    sf(
        "package",
        "enum",
        enum=("LQFP-32", "LQFP-64", "LQFP-100", "QFN-32", "QFN-48"),
        required=True,
    ),
)

_register(
    "microprocessor",
    "discrete_part",
    sf(
        "core_architecture",
        "enum",
        enum=("ARM Cortex-A53", "ARM Cortex-A72", "x86_64"),
        required=True,
    ),
    sf("clock_speed_max", "float", unit="MHz", required=True),
    sf("core_count", "int", required=True),
    sf("package", "enum", enum=("BGA",), required=True),
)

_register(
    "fpga",
    "discrete_part",
    sf("logic_elements", "int", required=True),
    sf("io_count", "int", required=True),
    sf("package", "enum", enum=("BGA", "QFN-144"), required=True),
)

_register(
    "memory",
    "discrete_part",
    sf("capacity_mbit", "int", required=True),
    sf("interface", "enum", enum=("SPI", "I2C", "parallel"), required=True),
    sf("package", "enum", enum=("SOIC-8", "WSON-8"), required=True),
)

# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

_register(
    "imu",
    "discrete_part",
    sf("axes", "int", required=True),
    sf("gyro_range_max", "float", unit="dps", indexed=False),
    sf("accel_range_max", "float", unit="g_accel", indexed=False),
    sf("interface", "enum", enum=("I2C", "SPI"), required=True),
    sf("package", "enum", enum=("QFN-24", "LGA-14"), required=True),
)

_register(
    "pressure",
    "discrete_part",
    sf("pressure_range_min", "float", unit="hPa", indexed=False),
    sf("pressure_range_max", "float", unit="hPa", indexed=False),
    sf("interface", "enum", enum=("I2C", "SPI"), required=True),
    sf("package", "enum", enum=("LGA-8",), required=True),
)

_register(
    "temperature",
    "discrete_part",
    sf("temp_range_min", "float", unit="C", required=True),
    sf("temp_range_max", "float", unit="C", required=True),
    sf("accuracy", "float", unit="C", indexed=False),
    sf("interface", "enum", enum=("I2C", "SPI", "analog"), required=True),
    sf("package", "enum", enum=("SOT-23", "TO-92"), required=True),
)

_register(
    "current_sense",
    "discrete_part",
    sf("current_range_max", "float", unit="A", required=True),
    sf("shunt_resistance", "float", unit="ohm", indexed=False),
    sf("interface", "enum", enum=("analog", "I2C"), required=True),
    sf("package", "enum", enum=("SOIC-8", "DFN-8"), required=True),
)

_register(
    "optical",
    "discrete_part",
    sf("wavelength", "float", unit="nm", indexed=False),
    sf("interface", "enum", enum=("I2C", "analog"), required=True),
    sf("package", "enum", enum=("0805", "QFN-6"), required=True),
)

# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

_register(
    "board_to_board",
    "discrete_part",
    sf("pin_count", "int", required=True),
    sf("pitch_mm", "float", unit="mm", required=True),
    sf("current_rating", "float", unit="A", indexed=False),
    sf("package", "enum", enum=("SMD", "through-hole"), required=True),
)

_register(
    "wire_to_board",
    "discrete_part",
    sf("pin_count", "int", required=True),
    sf("pitch_mm", "float", unit="mm", required=True),
    sf("current_rating", "float", unit="A", indexed=False),
    sf("package", "enum", enum=("JST-GH", "JST-PH", "molex-picoblade"), required=True),
)

_register(
    "usb",
    "discrete_part",
    sf("usb_type", "enum", enum=("USB-A", "USB-C", "micro-USB", "mini-USB"), required=True),
    sf("current_rating", "float", unit="A", indexed=False),
    sf("package", "enum", enum=("SMD", "through-hole"), required=True),
)

_register(
    "rf_connector",
    "discrete_part",
    sf("connector_type", "enum", enum=("SMA", "U.FL", "MMCX"), required=True),
    sf("impedance", "float", unit="ohm", indexed=False),
    sf("max_frequency", "float", unit="GHz", indexed=False),
)

# ---------------------------------------------------------------------------
# RF / Wireless
# ---------------------------------------------------------------------------

_register(
    "wifi_module",
    "discrete_part",
    sf("wifi_standard", "enum", enum=("802.11b/g/n", "802.11ac"), required=True),
    sf("frequency_band", "enum", enum=("2.4GHz", "5GHz", "dual-band"), required=True),
    sf("interface", "enum", enum=("UART", "SPI", "SDIO"), required=True),
    sf("package", "enum", enum=("SMD-module",), required=True),
)

_register(
    "ble_module",
    "discrete_part",
    sf("bluetooth_version", "enum", enum=("4.2", "5.0", "5.2", "5.3"), required=True),
    sf("tx_power_max", "float", unit="dBm", indexed=False),
    sf("interface", "enum", enum=("UART", "SPI"), required=True),
    sf("package", "enum", enum=("SMD-module",), required=True),
)

_register(
    "gps_module",
    "discrete_part",
    sf("channel_count", "int", indexed=False),
    sf("update_rate_max", "float", unit="Hz", indexed=False),
    sf("interface", "enum", enum=("UART", "I2C"), required=True),
    sf("package", "enum", enum=("LCC-24", "SMD-module"), required=True),
)

_register(
    "rf_amplifier",
    "discrete_part",
    sf("frequency_min", "float", unit="GHz", required=True),
    sf("frequency_max", "float", unit="GHz", required=True),
    sf("gain", "float", unit="dB", indexed=False),
    sf("package", "enum", enum=("QFN-16", "SOT-89"), required=True),
)

# ---------------------------------------------------------------------------
# Motor / Actuation
# ---------------------------------------------------------------------------

_register(
    "motor_driver",
    "discrete_part",
    sf("v_in_max", "float", unit="V", required=True),
    sf("i_out_max", "float", unit="A", required=True),
    sf("motor_type", "enum", enum=("brushed", "brushless", "stepper"), required=True),
    sf("package", "enum", enum=("QFN-24", "SOIC-16"), required=True),
)

_register(
    "esc",
    "cots_assembly",
    sf("i_continuous_max", "float", unit="A", required=True),
    sf("v_in_max", "float", unit="V", required=True),
    sf("protocol", "enum", enum=("PWM", "DShot", "OneShot"), required=True),
    sf("weight_g", "float", unit="weight_g", indexed=False),
)

_register(
    "servo",
    "cots_assembly",
    sf("torque_kg_cm", "float", unit="kg-cm", required=True),
    sf("speed_sec_60deg", "float", unit="s", indexed=False),
    sf("voltage_range", "enum", enum=("4.8-6V", "4.8-7.4V"), required=True),
    sf("weight_g", "float", unit="weight_g", indexed=False),
)

# ---------------------------------------------------------------------------
# Protection
# ---------------------------------------------------------------------------

_register(
    "tvs_diode",
    "discrete_part",
    sf("v_reverse_standoff", "float", unit="V", required=True),
    sf("i_peak_pulse", "float", unit="A", indexed=False),
    sf("package", "enum", enum=("SOD-323", "SOT-23", "DFN"), required=True),
)

_register(
    "fuse",
    "discrete_part",
    sf("current_rating", "float", unit="A", required=True),
    sf("voltage_rating", "float", unit="V", required=True),
    sf("fuse_type", "enum", enum=("fast-acting", "slow-blow", "resettable"), required=True),
    sf("package", "enum", enum=("0603", "1206", "through-hole"), required=True),
)

_register(
    "esd_protection",
    "discrete_part",
    sf("v_working_max", "float", unit="V", required=True),
    sf("channel_count", "int", indexed=False),
    sf("package", "enum", enum=("SOT-23", "DFN-6", "SC-70"), required=True),
)

# ---------------------------------------------------------------------------
# Interface / Logic
# ---------------------------------------------------------------------------

_register(
    "level_shifter",
    "discrete_part",
    sf("channel_count", "int", required=True),
    sf("v_low_side_max", "float", unit="V", indexed=False),
    sf("v_high_side_max", "float", unit="V", indexed=False),
    sf("package", "enum", enum=("TSSOP-8", "QFN-10"), required=True),
)

_register(
    "mux_demux",
    "discrete_part",
    sf("channel_count", "int", required=True),
    sf("v_max", "float", unit="V", indexed=False),
    sf("on_resistance", "float", unit="ohm", indexed=False),
    sf("package", "enum", enum=("TSSOP-16", "QFN-16"), required=True),
)

_register(
    "gate_driver",
    "discrete_part",
    sf("v_max", "float", unit="V", required=True),
    sf("drive_current", "float", unit="A", indexed=False),
    sf("channel_count", "int", indexed=False),
    sf("package", "enum", enum=("SOIC-8", "QFN-10"), required=True),
)

# ---------------------------------------------------------------------------
# Discrete Semiconductors
# ---------------------------------------------------------------------------

_register(
    "mosfet",
    "discrete_part",
    sf("v_ds_max", "float", unit="V", required=True),
    sf("i_d_max", "float", unit="A", required=True),
    sf("r_ds_on", "float", unit="ohm", indexed=False),
    sf("package", "enum", enum=("SOT-23", "TO-220", "D2PAK"), required=True),
)

_register(
    "bjt",
    "discrete_part",
    sf("v_ce_max", "float", unit="V", required=True),
    sf("i_c_max", "float", unit="A", required=True),
    sf("h_fe", "float", unit="ratio", indexed=False),
    sf("package", "enum", enum=("SOT-23", "TO-92"), required=True),
)

_register(
    "diode_rectifier",
    "discrete_part",
    sf("v_reverse_max", "float", unit="V", required=True),
    sf("i_forward_max", "float", unit="A", required=True),
    sf("package", "enum", enum=("SOD-123", "DO-214", "TO-220"), required=True),
)

# ---------------------------------------------------------------------------
# Flight Control / Avionics — COTS-heavy
# ---------------------------------------------------------------------------

_register(
    "flight_controller",
    "cots_assembly",
    sf("imu_count", "int", required=True),
    sf("weight_g", "float", unit="weight_g", required=True),
    sf("connector_type", "enum", enum=("JST-GH", "molex-picoblade", "solder-pads")),
    sf("mounting_pattern_mm", "enum", enum=("30.5x30.5", "20x20", "16x16")),
    sf("processor", "str", queryable=False, desc="Free-text MCU description, not filterable"),
    sf("uart_count", "int", required=False, indexed=False),
    sf("has_osd", "bool", required=False, indexed=False),
)

_register(
    "telemetry_radio",
    "cots_assembly",
    sf("frequency_band", "enum", enum=("433MHz", "915MHz", "2.4GHz"), required=True),
    sf("tx_power_max", "float", unit="dBm", indexed=False),
    sf("range_km", "float", unit="km", indexed=False),
    sf("weight_g", "float", unit="weight_g", indexed=False),
)

_register(
    "osd_module",
    "cots_assembly",
    sf("video_format", "enum", enum=("NTSC", "PAL", "digital"), required=True),
    sf("weight_g", "float", unit="weight_g", indexed=False),
    sf("voltage_range", "enum", enum=("5-36V", "3.3-5V"), required=True),
)


# ---------------------------------------------------------------------------
# Pydantic model generation
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, type[BaseModel]] = {}
_PY_TYPES: dict[str, type] = {"float": float, "int": int, "str": str, "bool": bool}


def spec_model(category: str) -> type[BaseModel]:
    """Lazily build and cache a real Pydantic v2 model for one category's specs.

    Generated, not hand-written — see module docstring. Raises ``KeyError``
    for an unregistered category.
    """
    cached = _MODEL_CACHE.get(category)
    if cached is not None:
        return cached
    spec = CATEGORY_REGISTRY.get(category)
    if spec is None:
        raise KeyError(f"unknown catalog category {category!r}")

    field_defs: dict[str, Any] = {}
    for f in spec.fields:
        py_type: Any = Literal[f.enum_values] if f.type == "enum" else _PY_TYPES[f.type]
        annotation = py_type if f.required else (py_type | None)
        default = ... if f.required else None
        field_defs[f.name] = (annotation, Field(default=default, description=f.description))

    model_name = "".join(part.capitalize() for part in category.split("_")) + "Specs"
    model: type[BaseModel] = create_model(model_name, **field_defs)
    _MODEL_CACHE[category] = model
    return model


def categories_by_purchase_unit(purchase_unit: PurchaseUnit) -> tuple[str, ...]:
    return tuple(
        name for name, spec in CATEGORY_REGISTRY.items() if spec.purchase_unit == purchase_unit
    )


__all__ = [
    "CATEGORY_REGISTRY",
    "CategorySpec",
    "FieldType",
    "PurchaseUnit",
    "SpecField",
    "categories_by_purchase_unit",
    "sf",
    "spec_model",
]
