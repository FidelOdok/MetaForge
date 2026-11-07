# The product's own view and readiness

Nothing on the page should be generic. Every product gets a `view` block in `product.json`, written for what that product's design hinges on, and readiness starts from its parts.

## Readiness: parts first, then the system

Each part passes four stages before the system gates count:

| Stage | Bought part | Made part | Fuselage shell |
|---|---|---|---|
| Defined | in an options decision with a reason | in `manufacturing.json` | from the form |
| 3D | geometry from its drawing | generated from the design | generated |
| Sourced or makeable | order code, datasheet and price | its picked supplier can make it | its maker's DFM rules pass |
| Integrated | wired with no failing electrical check, or placed (payload) | airframe checks pass | contents fit, bosses clear |

- **Airframe parts bought but not yet chosen** (motors, props) stay "not sourced" until a real part is picked in Options.
- **Wiring not modelled yet** (a companion computer, a receiver) shows as not applicable.
- **Gate 1, Intent:** needs every part defined.
- **Gate 2, Concept:** needs every part ready as well as no blocking problems.

## The view block

```json
"view": {
  "keyQuestions": [
    { "label": "Hovers for 40 minutes?", "source": "req:endurance", "section": "Models and scenarios" },
    { "label": "Balanced over its thrust centre?", "source": "check:cg", "section": "Airframe and CAD" },
    { "label": "Survives a 50 C dock roof?", "source": "env:worst", "section": "Where it lives" },
    { "label": "Every part ready?", "source": "parts", "section": "Readiness" }
  ],
  "summary": [ { "label": "Take-off", "source": "model:hover.auw_g", "unit": "g", "dec": 0 },
               { "label": "Hover", "source": "model:hover.flight_min", "unit": "min" },
               { "label": "Span", "source": "af:span", "unit": "mm" }, { "label": "Set cost", "source": "bom:cost", "unit": "£" } ],
  "sections": ["Readiness", "Models and scenarios", "Airframe and CAD", "Options"],
  "hide": ["Product tests"],
  "hideNa": true,
  "studio": ["prop", "arm", "motor", "leg", "shell"],
  "tryit": "mission",
  "mission": { "reserve": 0.2,
    "legs": [ { "name": "Launch", "min": 1, "power": 1.5 }, { "name": "Inspection hover", "min": 25, "power": 1.0 } ],
    "events": [ { "at": 18, "kind": "lte_loss", "label": "LTE lost, radio takes over" } ],
    "conditions": [ { "id": "calm", "label": "Calm" }, { "id": "wind", "label": "12 m/s wind", "power": "hover.wind_factor" } ] }
}
```

- **keyQuestions:** the five to ten questions the product must answer yes to, pinned at the top. Sources:
  - `req:<id>`: a built-in or model-bound requirement
  - `model:<model.output>` with `op` and `limit`
  - `check:<id>`: an airframe, assembly, reference, electrical, manufacturing or BOM check
  - `env:worst`: the worst environment check
  - `parts`: part readiness
  - `af:<field>` and `bom:cost|mass`

  `section` is the section a click opens.
- **summary:** the product's headline numbers in the viewport bar, in place of shell size and mass.
- **sections:** the right panel order, most important first. **hide** removes sections that do not apply. **hideNa** hides rows that are not applicable.
- **studio:** the order of the part sections in the left panel.
- **tryit:**
  - `device`: press the button, plug in USB-C. For small connected devices.
  - `mission`: the product flies its mission against its models. Each leg's power is a multiple of hover power, and conditions scale power or energy by model inputs. Events mark moments such as a link loss. The result is the battery left against the reserve.

Choose questions, order and mode from the brief and the scenarios, and never copy another product's view. The builder warns when a product with a context has no view.
