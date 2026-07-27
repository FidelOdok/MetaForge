# scaffold_driver

Scaffold a peripheral driver -- header, source, and register map -- for a firmware project.

## What it does

1. Takes the firmware work_product and the target peripheral/interface
2. Emits a driver skeleton: header, source stub, and a register map for the peripheral
3. Records the scaffolded driver onto the work_product in the twin

## Tools Required

- (none) -- deterministic scaffolding from the peripheral/interface spec

## Input

- `work_product_id` -- twin work_product id for the firmware project
- peripheral + communication interface (e.g. I2C, SPI, UART)

## Output

- `work_product_id` -- the firmware work_product the driver was written to
- generated header/source/register-map files + `interface_type`

## Limitations

- Generates a skeleton with register definitions, not a functional/tested driver
- Register map coverage depends on the peripheral definition provided
