# Hardware Bring-Up Checklist

## DroneFC v1.0 - Board Bring-Up Procedure

### Prerequisites

- [ ] Assembled PCB (visually inspected, no solder bridges)
- [ ] Bench power supply (current-limited to 500 mA initially)
- [ ] Multimeter
- [ ] Oscilloscope (100 MHz+ bandwidth)
- [ ] USB-C cable
- [ ] ST-Link V2 debugger (SWD)
- [ ] 3S LiPo battery (for final power test)
- [ ] 4x test ESC + motors (for motor output verification)

---

### Phase 1: Power-On / Smoke Test

- [ ] **Visual inspection**: No solder bridges on QFP-64 MCU pads, BGA-like LGA pads on IMU and barometer
- [ ] **Resistance check** (power off): Measure resistance between VIN and GND -- should be > 100 kohm
- [ ] **Apply 12V via bench supply** (current limit: 200 mA)
- [ ] **Verify no excessive current draw**: Quiescent should be < 50 mA with no firmware
- [ ] **Measure 5V rail**: Should read 5.0V +/- 150 mV (U5 TPS54302 output)
- [ ] **Measure 3.3V rail**: Should read 3.3V +/- 66 mV (U6 AMS1117-3.3 output)
- [ ] **Check for hot components**: No component should exceed 50 C with no firmware running
- [ ] **Ripple check** (oscilloscope): 5V rail ripple < 50 mV peak-to-peak at no load

### Phase 2: MCU Programming

- [ ] **Connect ST-Link** to SWD header (SWDIO, SWCLK, GND, 3.3V)
- [ ] **Verify MCU detection**: `st-info --probe` should detect STM32F405
- [ ] **Read MCU ID**: Device ID should be 0x413 (STM32F405/407)
- [ ] **Flash test firmware** (LED blink): Status LED (PC13) should toggle at 1 Hz
- [ ] **Verify BOOT0 pin**: Confirm BOOT0 is held low (normal boot from flash)

### Phase 3: USB-C Verification

- [ ] **Connect USB-C cable** to host PC
- [ ] **Verify enumeration**: Device should appear as USB CDC (Virtual COM Port)
- [ ] **Check USB voltage**: VBUS should read 5.0V +/- 250 mV on USB connector
- [ ] **CC resistor check**: Both CC lines should show 5.1 kohm to GND
- [ ] **Loopback test**: Send data via CDC, verify echo on terminal

### Phase 4: Sensor Verification

#### IMU (MPU6050)
- [ ] **SPI communication**: Read WHO_AM_I register (0x75) -- should return 0x68
- [ ] **Gyroscope zero-rate**: Static board should read < 5 deg/s on all axes
- [ ] **Accelerometer gravity**: Z-axis should read approximately 1g (+/- 0.05g), X/Y near 0g
- [ ] **Sample rate**: Verify 8 kHz data-ready interrupt on IMU_INT (PC4) with oscilloscope
- [ ] **Vibration isolation**: Tap board, verify soft-mount attenuates high-frequency content

#### Barometer (BMP280)
- [ ] **I2C communication**: Read chip ID register (0xD0) -- should return 0x58
- [ ] **Pressure reading**: Should read approximately 1013.25 hPa at sea level (+/- 12 hPa)
- [ ] **Temperature reading**: Should match ambient temperature +/- 2 C
- [ ] **Altitude stability**: Static reading should be stable within +/- 1 m over 10 seconds

### Phase 5: Motor Output Test

- [ ] **Connect 4x ESC** to motor output pads/connectors
- [ ] **Verify DShot600 waveform** (oscilloscope): Bit period approximately 1.67 us
- [ ] **Arm sequence**: Send DShot arm command, verify ESC beep sequence
- [ ] **Individual motor spin**: Spin each motor at 10% throttle, verify correct motor mapping
- [ ] **Motor direction**: Verify CW/CCW rotation matches quadcopter X configuration
- [ ] **All-motor test**: All 4 motors spinning simultaneously, no control glitches

### Phase 6: GPS Lock Test

- [ ] **Connect GPS module** (NEO-M8N) to JST-SH connector (J2)
- [ ] **Verify UART communication**: NMEA sentences should appear on USART1 at 115200 baud
- [ ] **Cold start fix**: Place board near window / outdoors, verify 3D fix within 60 seconds
- [ ] **Fix quality**: Verify HDOP < 2.0 and satellite count >= 6

### Phase 7: Peripheral Verification

- [ ] **Battery voltage ADC**: Apply known voltage to VIN, verify ADC reading matches within 2%
- [ ] **Buzzer**: Activate buzzer output (PB0), verify audible tone
- [ ] **Status LED**: Verify blue LED (PC13) toggles on command
- [ ] **SD card**: Insert micro-SD, verify SDIO initialization and file write/read
- [ ] **Telemetry UART**: Loopback test on USART2 (PA2/PA3)
- [ ] **RC receiver UART**: Connect CRSF receiver to USART3, verify channel data

### Phase 8: Environmental and Stress

- [ ] **Full-load thermal**: Run all peripherals + 4 motors at 50% for 30 minutes, verify no component > 70 C
- [ ] **Voltage range test**: Verify operation at 10.8V (3S min) and 25.2V (6S max)
- [ ] **Current measurement**: Total board draw should be < 2.0A under full load
- [ ] **Power budget**: Total power consumption should be < 3.5W

---

### Sign-Off

| Test Phase | Pass/Fail | Tester | Date | Notes |
|------------|-----------|--------|------|-------|
| Power-On   |           |        |      |       |
| Programming|           |        |      |       |
| USB-C      |           |        |      |       |
| Sensors    |           |        |      |       |
| Motors     |           |        |      |       |
| GPS        |           |        |      |       |
| Peripherals|           |        |      |       |
| Stress     |           |        |      |       |
