# configure_rtos

Generate an RTOS configuration and task table from task definitions (FORGE-545).

## What it does

1. Takes the target RTOS and each task's name, priority (higher is more urgent) and stack size in bytes
2. FreeRTOS: returns `FreeRTOSConfig.h` and `app_tasks.c` (an `xTaskCreate` table, depths in stack words). Zephyr: returns `prj.conf` and `app_threads.c` (`K_THREAD_DEFINE` per task, priority inverted to Zephyr's lower-is-higher order)
3. Computes the RAM estimate exactly: heap plus every stack, rounded up to whole KB

## Tools Required

- (none) -- deterministic configuration generation

## Input

- `work_product_id` -- twin work_product id for the firmware project
- `rtos_name` -- FreeRTOS or Zephyr; others are refused
- `task_definitions` -- name, priority and `stack_size` (bytes) are all required; nothing is defaulted
- `heap_size_kb`, `tick_rate_hz`, `stack_word_bytes` (4 on a 32-bit MCU)

## Output

- `files` -- each generated file's path and content
- `config_file`, `tasks_configured`, `memory_estimate_kb`

## Limitations

- Does not validate schedulability or measure real stack use
- The files are returned, not written or recorded; the caller stages them
