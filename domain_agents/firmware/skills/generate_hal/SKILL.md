# generate_hal

Generate a Hardware Abstraction Layer (HAL) for a target MCU and its peripherals.

## What it does

1. Takes the firmware project's twin work_product plus the target MCU/peripheral set
2. Generates the HAL sources that abstract the MCU's peripherals behind a stable interface
3. Records the generated HAL back onto the firmware work_product in the twin

## Tools Required

- (none) -- deterministic code generation from the MCU/peripheral spec

## Input

- `work_product_id` -- twin work_product id for the firmware project
- target MCU + peripheral configuration

## Output

- `work_product_id` -- the firmware work_product the HAL was written to
- generated HAL source set + `hal_version`

## Limitations

- Bounded to the MCU families/peripherals the generator templates cover
- Produces scaffolding, not verified drivers -- peripheral bring-up still required
