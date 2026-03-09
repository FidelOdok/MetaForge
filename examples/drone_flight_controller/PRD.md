# Product Requirements Document: Quadcopter Flight Controller Board

## Product Overview

**Product Name**: MetaForge DroneFC
**Version**: 1.0
**Date**: 2026-03-09
**Author**: MetaForge Example Project

### Purpose

A compact, high-performance flight controller board targeting hobby and prosumer quadcopter drones in the 250-500mm frame class. The board integrates an IMU, barometer, GPS receiver, and motor control outputs into a standard 30.5x30.5mm mounting pattern form factor.

### Target Market

- FPV racing and freestyle pilots upgrading from budget boards
- Prosumer aerial photography platforms (sub-2kg AUW)
- Educational and research drone platforms
- DIY drone builders seeking open-architecture controllers

---

## Functional Requirements

### FR-1: Flight Control Processing

- **MCU**: STM32F405RGT6 (168 MHz ARM Cortex-M4F, 1 MB Flash, 192 KB SRAM)
- Control loop rate: 8 kHz PID loop minimum
- Support for Betaflight / ArduPilot firmware (hardware-compatible pinout)
- Hardware FPU for real-time sensor fusion (complementary / Kalman filter)

### FR-2: Inertial Measurement

- **Primary IMU**: MPU6050 (3-axis gyroscope + 3-axis accelerometer)
- Gyroscope range: +/-2000 deg/s
- Accelerometer range: +/-16g
- IMU sampling rate: 8 kHz (SPI interface)
- Soft-mounted IMU footprint for vibration isolation

### FR-3: Barometric Altitude

- **Barometer**: BMP280
- Altitude resolution: +/-1 m
- Interface: I2C (secondary bus, isolated from IMU)
- Foam cover pad for wind noise isolation

### FR-4: GPS Navigation

- **GPS Module**: u-blox NEO-M8N (external module via UART)
- Update rate: 10 Hz
- Sensitivity: -167 dBm (tracking)
- UART interface at 115200 baud
- Connector: JST-SH 4-pin

### FR-5: Motor Control Outputs

- 4x PWM outputs for ESC control
- DShot600 digital protocol support (timer-based)
- Dedicated timer channels (TIM1 or TIM8) for jitter-free output
- Connector: solder pads + JST-SH 4-pin header option

### FR-6: Serial Interfaces

- UART1: GPS (115200 baud)
- UART2: Telemetry / OSD (115200 baud)
- UART3: Serial receiver (SBUS/CRSF, inverted UART support)
- UART6: Spare / peripheral expansion
- USB-C: Configuration, firmware flash, blackbox download

### FR-7: Power Input

- Input voltage: 3S-6S LiPo (10.8V - 25.2V)
- Onboard 5V/3A BEC (switching regulator) for peripherals
- Onboard 3.3V/500mA LDO for MCU and sensors
- Reverse polarity protection via P-channel MOSFET
- Battery voltage sensing via resistor divider on ADC input

### FR-8: Additional Peripherals

- I2C bus (external): Magnetometer, OLED, rangefinder
- SPI bus (external): Reserved for secondary IMU or OSD chip
- Buzzer output: Active buzzer drive (open-drain, 5V tolerant)
- LED outputs: 1x status LED (blue), 1x WS2812 addressable LED pad
- SD card slot: Micro-SD for blackbox logging (SDIO interface)

---

## Non-Functional Requirements

### NFR-1: Physical

| Parameter | Specification |
|-----------|--------------|
| Board dimensions | 36 x 36 mm |
| Mounting pattern | 30.5 x 30.5 mm, M3 holes |
| Max thickness | 8 mm (including components) |
| Max weight | 15 g (bare board, populated) |
| PCB layers | 4-layer (signal-ground-power-signal) |
| PCB thickness | 1.6 mm |
| Copper weight | 1 oz outer, 1 oz inner |
| Surface finish | ENIG (for solder pad reliability) |

### NFR-2: Environmental

| Parameter | Specification |
|-----------|--------------|
| Operating temperature | -10 C to +60 C |
| Storage temperature | -40 C to +85 C |
| Humidity | Up to 85% RH, non-condensing |
| Vibration | Withstand 5g RMS, 20-2000 Hz |
| IP rating | IP20 (indoor/sheltered use) |

### NFR-3: Electrical

| Parameter | Specification |
|-----------|--------------|
| Input voltage range | 10.8V - 25.2V DC |
| Quiescent current | < 150 mA at 12V (no GPS) |
| Max board current draw | 2.0 A |
| Power budget | 3.5 W total |
| EMC class | Class B (residential) |

### NFR-4: Reliability

- MTBF target: > 10,000 hours
- Conformal coating on production units (optional)
- All connectors rated for 50+ mating cycles
- ESD protection on USB and external interfaces (TVS diodes)

---

## Compliance Targets

| Standard | Scope |
|----------|-------|
| FCC Part 15, Class B | Unintentional radiator, USA |
| CE Marking (EN 55032) | EMC, European Union |
| RoHS 3 (EU 2015/863) | Hazardous substance restriction |
| REACH | Chemical safety |
| UL 94 V-0 | PCB flame retardancy |

---

## Acceptance Criteria

1. Board passes power-on smoke test with 3S and 6S LiPo input
2. All voltage rails within 5% of nominal under full load
3. IMU data stream at 8 kHz with < 1% sample loss
4. GPS achieves 3D fix within 60 seconds (open sky, cold start)
5. DShot600 motor outputs verified with oscilloscope
6. USB-C enumeration and firmware flash successful
7. All UARTs verified with loopback test
8. Barometer altitude reading stable within +/-1 m (static)
9. Board temperature < 70 C after 30 minutes full-load operation
10. Weight verified < 15 g on calibrated scale
