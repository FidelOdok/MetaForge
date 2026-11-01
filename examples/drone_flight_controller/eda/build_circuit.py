"""Build the drone flight controller's circuit description (FORGE-685).

The design is written here by pin *name* (``U1:PA5``) as the datasheet names
them; this script resolves each name to the pin number in KiCad's own symbol
and writes ``circuit.json``, the input to ``kicad.generate_schematic`` /
``generate_pcb`` / ``autoroute_pcb``. A name the symbol does not have, or has
more than once where the net is meant for one pin, stops it -- no pin number
is typed by hand.

Run where the KiCad libraries are (the kicad-adapter image)::

    python examples/drone_flight_controller/eda/build_circuit.py

Design notes (recorded in ../decisions.md):
- STM32F405RGT6, 8 MHz HSE crystal, USB FS on PA11/PA12 behind a USBLC6 ESD
  array, SWD on a 1.27 mm header.
- MPU-6000 on SPI1 (PA4..PA7) with its interrupt on PC4; BMP280 on I2C1
  (PB6/PB7, 4.7 k pull-ups) at address 0x76 (SDO low, CSB high selects I2C).
- AP2112K-3.3 LDO from 5 V (the ESC's BEC on J2, or USB VBUS).
- Motor outputs M1..M4 on PB0, PB1 (TIM3 CH3/CH4) and PA0, PA1 (TIM5 CH1/CH2),
  as firmware/src/pinmap.json assigns them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tool_registry.tools.kicad.schematic_gen import SymbolLibrary, symbol_pins  # noqa: E402

C0402, R0402 = "Capacitor_SMD:C_0402_1005Metric", "Resistor_SMD:R_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"

# fmt: off
PARTS: list[dict] = [
    {"ref": "U1", "symbol": "MCU_ST_STM32F4:STM32F405RGTx",
     "footprint": "Package_QFP:LQFP-64_10x10mm_P0.5mm", "value": "STM32F405RGT6",
     "fields": {"MPN": "STM32F405RGT6"}},
    {"ref": "U2", "symbol": "Sensor_Motion:MPU-6000",
     "footprint": "Sensor_Motion:InvenSense_QFN-24_4x4mm_P0.5mm", "value": "MPU-6000",
     "fields": {"MPN": "MPU-6000"},
     # KiCad's footprint names an EP2.7x2.6 model the library does not ship.
     "model": "${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/"
              "QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm.step"},
    {"ref": "U3", "symbol": "Sensor_Pressure:BMP280",
     "footprint": "Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering",
     "value": "BMP280", "fields": {"MPN": "BMP280"}},
    {"ref": "U4", "symbol": "Regulator_Linear:AP2112K-3.3",
     "footprint": "Package_TO_SOT_SMD:SOT-23-5", "value": "AP2112K-3.3",
     "fields": {"MPN": "AP2112K-3.3TRG1"}},
    {"ref": "U5", "symbol": "Power_Protection:USBLC6-2SC6",
     "footprint": "Package_TO_SOT_SMD:SOT-23-6", "value": "USBLC6-2SC6",
     "fields": {"MPN": "USBLC6-2SC6"}},
    {"ref": "J1", "symbol": "Connector:USB_C_Receptacle_USB2.0_16P",
     "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
     "value": "USB-C", "fields": {"MPN": "USB4105-GF-A"}},
    {"ref": "J2", "symbol": "Connector_Generic:Conn_01x02",
     "footprint": "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", "value": "5V_IN"},
    {"ref": "J3", "symbol": "Connector_Generic:Conn_01x05",
     "footprint": "Connector_JST:JST_PH_B5B-PH-K_1x05_P2.00mm_Vertical", "value": "MOTORS"},
    {"ref": "J4", "symbol": "Connector_Generic:Conn_01x04",
     "footprint": "Connector_PinHeader_1.27mm:PinHeader_1x04_P1.27mm_Vertical", "value": "SWD"},
    {"ref": "Y1", "symbol": "Device:Crystal_GND24",
     "footprint": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "value": "8MHz"},
    {"ref": "D1", "symbol": "Device:LED", "footprint": "LED_SMD:LED_0603_1608Metric",
     "value": "LED_GREEN"},
]
CAPS = {  # ref: (value, footprint, net on pin 1); pin 2 is GND
    "C1": ("100nF", C0402, "+3V3"), "C2": ("100nF", C0402, "+3V3"),
    "C3": ("100nF", C0402, "+3V3"), "C4": ("100nF", C0402, "+3V3"),
    "C5": ("4.7uF", C0603, "+3V3"), "C6": ("1uF", C0402, "+3V3"),
    "C7": ("2.2uF", C0402, "VCAP1"), "C8": ("2.2uF", C0402, "VCAP2"),
    "C9": ("100nF", C0402, "NRST"), "C10": ("1uF", C0402, "+5V"),
    "C11": ("1uF", C0402, "+3V3"), "C12": ("20pF", C0402, "OSC_IN"),
    "C13": ("20pF", C0402, "OSC_OUT"), "C14": ("100nF", C0402, "IMU_REGOUT"),
    "C15": ("2.2nF", C0402, "IMU_CPOUT"), "C16": ("100nF", C0402, "+3V3"),
    "C17": ("100nF", C0402, "+3V3"),
}
RES = {  # ref: (value, pin-1 net, pin-2 net)
    "R1": ("10k", "BOOT0", "GND"), "R2": ("5.1k", "USB_CC1", "GND"),
    "R3": ("5.1k", "USB_CC2", "GND"), "R4": ("4.7k", "+3V3", "I2C1_SCL"),
    "R5": ("4.7k", "+3V3", "I2C1_SDA"), "R6": ("1k", "+3V3", "LED_A"),
}
# Net -> pins by name ("REF:NAME", or "REF#NUMBER" for a pin a name cannot pick).
NETS: dict[str, list[str]] = {
    "+5V": ["J2#1", "J1:VBUS*", "U4:VIN", "U4:EN", "U5:VBUS"],
    "GND": ["J2#2", "J3#5", "J4#4", "J1:GND*", "J1:SHIELD", "U1:VSSA", "U1:VSS*", "U4:GND",
            "U5:GND", "U2:GND", "U2:FSYNC", "U2:CLKIN", "U3:GND*", "U3:SDO", "Y1#2", "Y1#4"],
    "+3V3": ["U4:VOUT", "U1:VBAT", "U1:VDDA", "U1:VDD*", "U2:VDD", "U3:VDDIO", "U3:VDD",
             "U3:CSB", "J4#1"],
    "VCAP1": ["U1:VCAP_1"], "VCAP2": ["U1:VCAP_2"],
    "NRST": ["U1:NRST"], "BOOT0": ["U1:BOOT0"],
    "OSC_IN": ["U1:PH0", "Y1#1"], "OSC_OUT": ["U1:PH1", "Y1#3"],
    "USB_DP": ["U1:PA12", "J1:D+*", "U5#1", "U5#6"],
    "USB_DM": ["U1:PA11", "J1:D-*", "U5#3", "U5#4"],
    "USB_CC1": ["J1:CC1"], "USB_CC2": ["J1:CC2"],
    "SPI1_SCK": ["U1:PA5", "U2:SCL/SCLK"], "SPI1_MISO": ["U1:PA6", "U2:AD0/MISO"],
    "SPI1_MOSI": ["U1:PA7", "U2:SDA/MOSI"], "IMU_CS": ["U1:PA4", "U2:~{CS}"],
    "IMU_INT": ["U1:PC4", "U2:INT"],
    "IMU_REGOUT": ["U2:REGOUT"], "IMU_CPOUT": ["U2:CPOUT"],
    "I2C1_SCL": ["U1:PB6", "U3:SCK"], "I2C1_SDA": ["U1:PB7", "U3:SDI"],
    "M1": ["U1:PB0", "J3#1"], "M2": ["U1:PB1", "J3#2"], "M3": ["U1:PA0", "J3#3"],
    "M4": ["U1:PA1", "J3#4"],
    "SWDIO": ["U1:PA13", "J4#2"], "SWCLK": ["U1:PA14", "J4#3"],
    "LED_A": ["D1:A"], "LED_K": ["D1:K", "U1:PC13"],
}
BOARD = {
    "width_mm": 40, "height_mm": 40, "layers": 4, "clearance_mm": 0.15, "track_mm": 0.15,
    # +3V3 and GND are planes (In2/In1); only the 5 V input runs as wide track.
    "power_nets": ["+5V"], "power_track_mm": 0.4,
    "via_diameter_mm": 0.5, "via_drill_mm": 0.25,
    # GCT's USB4105 land pattern puts its SMD pads 0.19 mm from the shell holes.
    "hole_clearance_mm": 0.15, "ground_pour": "GND", "mounting_holes_mm": 3.2,
    "inner_planes": ["GND", "+3V3"],
}
# fmt: on


def build() -> dict:
    lib = SymbolLibrary()
    comps = list(PARTS)
    for ref, (val, fp, _net) in CAPS.items():
        comps.append({"ref": ref, "symbol": "Device:C", "footprint": fp, "value": val})
    for ref, (val, _a, _b) in RES.items():
        comps.append({"ref": ref, "symbol": "Device:R", "footprint": R0402, "value": val})
    pins = {c["ref"]: symbol_pins(lib.symbol(c["symbol"])) for c in comps}
    nets: dict[str, list[str]] = {name: [] for name in NETS}
    for name, specs in NETS.items():
        for spec in specs:
            if "#" in spec:
                ref, number = spec.split("#")
                assert any(p.number == number for p in pins[ref]), spec
                nets[name].append(f"{ref}.{number}")
                continue
            ref, pin_name = spec.split(":", 1)
            many = pin_name.endswith("*")
            want = pin_name.rstrip("*")
            hits = sorted({p.number for p in pins[ref] if p.name == want})
            if not hits or (len(hits) > 1 and not many):
                raise SystemExit(f"{spec}: symbol pins named {want!r}: {hits}")
            nets[name].extend(f"{ref}.{n}" for n in hits)
    for ref, (_v, _fp, net) in CAPS.items():
        nets[net].append(f"{ref}.1")
        nets["GND"].append(f"{ref}.2")
    for ref, (_v, a, b) in RES.items():
        nets[a].append(f"{ref}.1")
        nets[b].append(f"{ref}.2")
    return {
        "name": "drone-fc",
        "components": comps,
        "nets": [{"name": n, "nodes": nodes} for n, nodes in nets.items()],
        "board": BOARD,
    }


if __name__ == "__main__":
    circuit = build()
    out = Path(__file__).with_name("circuit.json")
    out.write_text(json.dumps(circuit, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {len(circuit['components'])} components, {len(circuit['nets'])} nets")
