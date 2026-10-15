# check_cmf_compatibility

Checks that each part's chosen appearance (colour, material and finish) suits the part's material.

## What it does

1. Takes the parts (material and chosen appearance) and the appearances as `library.get_appearance` returns them
2. Passes a part whose material is one the appearance was recorded for, fails one that is not, and marks unknown a part with no appearance, no material, or an appearance that records no materials
3. Lists the finishing processes the chosen appearances need, and any of their values still marked as assumptions
4. Gives a verdict: incompatible (any fail), incomplete (any unknown) or compatible

## Tools Required

None -- pure computation over library records. Read the appearances with `library.get_appearance` first.

## Input

- `parts` -- `{name, material, appearance}`
- `appearances` -- library appearance records

## Output

`verdict`, `findings`, `passed`, `failed`, `unknown`, `finishing_processes`, `needs_review`

## Limitations

- Uses only what the library records; there is no built-in table of which finishes suit which materials
