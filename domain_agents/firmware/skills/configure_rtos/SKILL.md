# configure_rtos

Generate an RTOS configuration from task definitions and memory constraints.

## What it does

1. Takes the firmware work_product, the target RTOS, task definitions, and memory limits
2. Generates the RTOS configuration (task table, priorities, stack sizes, heap, tick rate)
3. Records the generated configuration onto the work_product in the twin

## Tools Required

- (none) -- deterministic configuration generation

## Input

- `work_product_id` -- twin work_product id for the firmware project
- RTOS name (e.g. FreeRTOS, Zephyr, ChibiOS)
- `tasks` -- task definitions (name, priority, stack_size)
- `heap_size_kb`, `tick_rate_hz` -- memory + timing constraints

## Output

- `work_product_id` -- the firmware work_product configured
- `config_file` -- path to the generated RTOS configuration
- `tasks_configured` -- number of tasks configured

## Limitations

- Generates configuration only; it does not validate schedulability or stack sizing
- RTOS coverage bounded by the supported configuration templates
