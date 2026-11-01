# generate_hal

Generate the board's pin definitions for a target MCU from its real pin map (FORGE-545).

## What it does

1. Takes the MCU family and the board's pin map (signal, pin, peripheral), e.g. from `kicad.get_pin_mapping`
2. Checks every pin name is valid for the family and no pin is assigned twice
3. Returns `board_pins.h` (the vendor's own macros per signal: `GPIOA`/`GPIO_PIN_4` on STM32, `GPIO_NUM_17` on ESP32, `NRF_GPIO_PIN_MAP(0, 13)` on nRF52, the pin number on RP2040, `PIN_PA05` on ATSAMD) and `board_peripherals.h` (peripheral instances in use), with full content

## Tools Required

- (none) -- deterministic code generation from the pin map

## Input

- `work_product_id` -- twin work_product id for the firmware project
- `mcu_family` -- STM32F4, STM32H7, ESP32, nRF52, RP2040 or ATSAMD
- `pin_map` -- required; without it the skill refuses rather than inventing pins
- `peripherals` -- optional; each must have pins in `pin_map`
- `source` -- where the pin map came from, cited in the header

## Output

- `files` -- each generated file's path and content
- `generated_files`, `pin_mappings` (signal to pin), `hal_version`

## Limitations

- Pin definitions only; peripheral drivers and vendor HAL init are not generated
- The files are returned, not written or recorded; the caller stages them
