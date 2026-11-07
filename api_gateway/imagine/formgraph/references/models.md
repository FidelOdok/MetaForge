# Models

A model is a set of formulas Claude writes for the product's own physics, stored in `context/models.json`. The page evaluates models instantly as tier 1 checks, and runs them as a Monte Carlo over input spreads as a tier 2 background job. Anything that needs a field solve (FEA, CFD, full-wave EM) is tier 3 and stays a cloud estimate.

```json
{ "models": [
  { "id": "hover", "name": "Hover power and flight time",
    "source": "momentum theory with a figure of merit; k from the motor maker's thrust table",
    "valid": "multirotor, 2 to 5 inch props, below 1,000 m",
    "inputs": {
      "mass_g":  { "from": "bom.mass_g" },
      "frame_g": { "value": 140, "unit": "g", "source": "airframe mass budget", "spread": 0.05 },
      "rotors":  { "value": 4, "unit": "" },
      "prop_d":  { "value": 0.0762, "unit": "m", "source": "3 inch props" },
      "fm":      { "value": 0.6, "unit": "", "source": "typical figure of merit, small props", "spread": 0.1 },
      "wh":      { "value": 7.4, "unit": "Wh", "source": "2S 1000 mAh cell datasheet", "spread": 0.05 },
      "rho":     { "value": 1.2, "unit": "kg/m3", "source": "ISA sea level" }
    },
    "outputs": {
      "auw_kg":     { "expr": "(mass_g+frame_g)/1000", "unit": "kg" },
      "p_hover_w":  { "expr": "(auw_kg*g)**1.5/sqrt(2*rho*rotors*pi*(prop_d/2)**2)/fm", "unit": "W" },
      "flight_min": { "expr": "wh*0.8/p_hover_w*60", "unit": "min" }
    } }
] }
```

## Grammar

- Numbers, input and output names, `+ - * / % **`, parentheses, unary minus.
- Functions: min, max, sqrt, pow, abs, exp, log, log10, sin, cos, tan, atan, floor, ceil, round, clamp.
- Constants: `pi`, `g` (9.81), `e`.
- An output may use inputs and earlier outputs of the same model.
- Nothing else: no attribute access, no indexing, no strings. The builder rejects anything outside this.

## Inputs

- `value` with a `unit` and a `source`, or `from` a page quantity: bom.mass_g, bom.cost_gbp, form.W, form.D, form.H, form.t, form.volume_cm3, power.avg_mW, power.peak_mA, battery.wh, battery.mah, env.opMin, env.opMax, env.altitude_m, process.qty.
- `spread`: a fraction for tier 2 (0.1 means plus or minus 10 %, uniform). Leave it out for exact values.

## Binding to requirements

A custom requirement is proved by a model output:

```json
{ "id": "endurance", "text": "While hovering in still air, the aircraft shall fly at least 15 minutes.",
  "model": "hover.flight_min", "op": ">=", "limit": 15, ... }
```

Tier 1 gives a provisional result. The tier 2 job reports the share of runs that pass, and the requirement is met when at least 95 % pass. A requirement with no model shows "no model" and keeps gate 3 open.

## Rules

- Every formula needs a model `source`; every input value needs its own `source`.
- Write `valid` so the reader knows where the model stops being true.
- Prefer simple, standard relationships with a named basis over clever ones.
