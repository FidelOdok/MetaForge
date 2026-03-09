# Design Decisions Log

## DD-001: MCU Selection -- STM32F405RGT6 over ESP32

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The flight controller requires a microcontroller capable of running an 8 kHz PID loop with real-time sensor fusion, DShot motor output, and multiple UART channels. The two leading candidates were the STM32F405RGT6 and the ESP32-S3.

**Decision**: STM32F405RGT6

**Rationale**:
- Deterministic interrupt latency (< 10 us) -- critical for 8 kHz control loops. The ESP32 FreeRTOS scheduler introduces non-deterministic jitter.
- Native DShot600 timer support via TIM1/TIM8 with DMA, enabling zero-CPU-overhead motor output.
- 6x hardware UARTs vs. 3 on ESP32, avoiding the need for software serial or UART multiplexing.
- Mature ecosystem: Betaflight, INAV, and ArduPilot all have first-class STM32F4 support. ESP32 support is experimental.
- Hardware FPU (Cortex-M4F) with single-cycle multiply-accumulate for sensor fusion math.

**Trade-offs**: ESP32 offers built-in Wi-Fi/BLE (useful for configuration apps), but this can be added via an external ESP-01 module on a spare UART when needed. The STM32F405 is also more expensive ($5-7 vs. $3-4), but the performance and ecosystem advantages justify the cost for a prosumer product.

---

## DD-002: 4-Layer PCB Stack-up

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The 36x36mm board must route a 168 MHz MCU, high-speed SPI (IMU at 20 MHz), USB 2.0 Full Speed, switching regulator, and analog sensor signals in a compact area.

**Decision**: 4-layer PCB (Signal - Ground - Power - Signal)

**Rationale**:
- Dedicated ground plane (Layer 2) provides low-impedance return paths, critical for EMC compliance (FCC Part 15 Class B).
- Dedicated power plane (Layer 3) reduces voltage drop across the board and simplifies power distribution.
- Controlled impedance for USB D+/D- traces (90 ohm differential) is straightforward with a known ground plane distance.
- Two signal layers allow routing the dense LQFP-64 MCU breakout without excessive vias.
- JLCPCB 4-layer pricing is now under $2/board at quantity 10, making 2-layer cost savings negligible.

**Trade-offs**: A 2-layer board would be cheaper at very high volumes (100k+), but at prototype and low-volume quantities (100-1000 units), the 4-layer premium is minimal and the EMC/signal-integrity benefits are substantial. A 6-layer board would offer even better isolation but is unnecessary for this design complexity.

---

## DD-003: IMU Selection -- MPU6050 as Primary

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The flight controller requires a 6-axis IMU (3-axis gyro + 3-axis accelerometer) with at least 8 kHz sampling and SPI interface for the primary flight control loop.

**Decision**: MPU6050 as primary IMU, with PCB footprint compatible with ICM-42688-P as a drop-in upgrade.

**Rationale**:
- MPU6050 is the most widely supported IMU in the drone firmware ecosystem (Betaflight, ArduPilot).
- Well-characterized noise profile and established filtering parameters in community firmware.
- Available from multiple distributors with short lead times.
- Unit cost of ~$2.50 keeps BOM cost low for the target market.

**Trade-offs**: The ICM-42688-P offers superior noise density (70 mdps/sqrt(Hz) vs. 5 mdps/sqrt(Hz) for MPU6050) and lower power consumption. The PCB footprint is designed to accept either part (LGA-14 compatible), allowing a BOM swap for a premium variant without a board respin. For the initial release targeting hobby pilots, the MPU6050 provides adequate performance.

---

## DD-004: Switching Regulator for 5V BEC

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The board must step down 10.8V-25.2V input to 5V for peripherals (GPS, servos, LEDs) with up to 3A output capability.

**Decision**: TPS54302 synchronous buck converter (Texas Instruments)

**Rationale**:
- Input range (4.5V-28V) covers the full 3S-6S LiPo range with margin.
- 3A continuous output handles GPS module (~50 mA), video transmitter (up to 1A), and LED strips.
- 92% typical efficiency at 12V-to-5V, 1A -- minimizes heat generation within the 36x36mm board area.
- SOT-23-6 package with minimal external components (22 uH inductor, input/output caps) fits the tight layout.
- Switching frequency of 400 kHz keeps inductor size small while staying well below the IMU sampling frequency.

**Trade-offs**: An LDO would be simpler (fewer components, no switching noise) but would dissipate up to 60W as heat at 25.2V input and 3A -- completely impractical. The switching noise is managed through proper PCB layout (tight switching loop, ground plane, input/output bulk capacitors) and locating the regulator on the opposite side of the board from the IMU.

---

## DD-005: USB-C Connector over Micro-USB

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The board needs a USB connector for firmware flashing, configuration, and blackbox data download.

**Decision**: USB Type-C receptacle (USB 2.0 Full Speed only)

**Rationale**:
- Reversible connector eliminates user frustration during field configuration (often done with gloves or in awkward orientations).
- Mechanically stronger than Micro-USB: rated for 10,000 mating cycles vs. 5,000 for Micro-USB.
- USB-C is becoming the standard connector for drone peripherals (radio receivers, VTX configuration).
- Only USB 2.0 Full Speed (12 Mbps) is needed -- no high-speed PHY required, keeping implementation simple.
- CC resistors (5.1k to GND) for proper USB-C UFP identification are the only additional components.

**Trade-offs**: The USB-C receptacle has a slightly larger footprint than Micro-USB (8.9mm vs. 7.5mm width), but this is manageable on the 36mm board edge. Cost difference is negligible at ~$0.10 per unit.

---

## DD-006: ENIG Surface Finish

**Date**: 2026-03-09
**Status**: Accepted

**Context**: The PCB surface finish affects solderability, shelf life, and pad reliability for the fine-pitch LQFP-64 MCU and LGA IMU packages.

**Decision**: ENIG (Electroless Nickel Immersion Gold)

**Rationale**:
- Excellent coplanarity for the LQFP-64 (0.5mm pitch) and LGA-14 IMU pads -- critical for reliable reflow soldering.
- Long shelf life (>12 months) compared to HASL (6 months), important for small-batch production with irregular ordering cycles.
- Lead-free, satisfying RoHS compliance without additional considerations.
- Consistent pad surface for automated optical inspection (AOI) during production.

**Trade-offs**: ENIG adds approximately $0.30-0.50 per board compared to HASL at prototype quantities. OSP (Organic Solderability Preservative) would be cheaper but has a shorter shelf life (3-6 months) and worse performance with fine-pitch components. The reliability gain justifies the modest cost increase.
