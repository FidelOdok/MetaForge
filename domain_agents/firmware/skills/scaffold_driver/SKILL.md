# scaffold_driver

Generate a register-level driver from the part's datasheet registers (FORGE-545).

## What it does

1. Takes the part's register list (name, address, access, reset, expected value) from its datasheet
2. Checks addresses fit the address width and no name or address repeats
3. Returns `<name>_regs.h`, `<name>.h` and `<name>.c`: register read/write over bus callbacks the board supplies, and an `init` that checks the identity register when one has an `expected` value

## Tools Required

- (none) -- deterministic generation from the register list

## Input

- `work_product_id` -- twin work_product id for the firmware project
- `peripheral_type`, `interface` (spi, i2c, uart, parallel), `driver_name`
- `registers` -- required; the skill refuses rather than using a generic map
- `address_bits` (default 8), `source` (datasheet and revision, cited in the files)

## Output

- `files` -- each generated file's path and content
- `driver_files`, `interface_type`, `register_map`

## Limitations

- Registers are taken as 8 bits wide; bit fields and configuration sequences are not generated
- The files are returned, not written or recorded; the caller stages them
