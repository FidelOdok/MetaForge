# Airframe and CAD

For a multirotor (a quad, hexa or octo), add an `airframe` block to `product.json`. The page builds the aircraft in 3D around the fuselage, which stays the part held to manufacturing limits:
- arms in an X or plus layout, with hinge blocks
- motors and pods, props with accent tips, landing legs
- the picked payload under the nose, the battery bay
- nose sensor apertures and a top light

```json
"airframe": {
  "type": "multirotor", "rotors": 4, "layout": "x",
  "prop_d": { "from": "hover.prop_d" }, "blades": 2,
  "arm_d": 22, "motor": [46, 30], "legs": { "h": 170, "d": 18 },
  "payload": { "decision": "gim", "fallback": [120, 100, 120] },
  "battery": { "decision": "battery", "at": "balance", "fallback": [135, 70, 78] },
  "nose_sensors": 5, "top_light": true, "accent": "#E8762D", "arms": "#2B2D30",
  "dock": [1200, 1200]
}
```

| Field | Meaning |
|---|---|
| `prop_d` | prop diameter in mm, or `from` a model input in metres, so a prop variant resizes the aircraft |
| `payload.decision` | the options decision whose picked part gives the payload size (its geometry envelope) |
| `battery.decision` | a variant decision whose picked variant has `dims` [L, W, H] in mm |
| `battery.at` | `balance` places the pack so the centre of gravity sits over the thrust centre, `rear` hangs it at the back, a number places it in mm from the centre |
| `prop_above` | how far the prop plane sits above the body top, 15 mm by default |
| `dock` | the dock's inner size, to check the span |

The arm length comes from the prop size: tips at least 8 % of the prop apart, props above the body.

## Checks (they count towards gate 2, Concept)

- props clear each other
- props clear the fuselage (above it, or 10 mm clear in plan)
- the battery bay fits inside the fuselage
- the centre of gravity is within 5 % of the arm length of the thrust centre
- the size across the props fits the dock, when a dock size is given

Masses come from the models: `hover.batt_g`, `hover.payload_g`, `hover.frame_g`, and the fuselage from the BOM.

## Design variants

A decision of kind `variant` picks a set of model inputs. On the page, each candidate shows what the requirements bound to models would read before it is picked:

```json
{ "id": "battery", "label": "Battery pack", "kind": "variant", "pick": "6s3p", "why": "...",
  "candidates": ["6s2p", "6s3p"],
  "variants": [
    { "id": "6s2p", "label": "6S2P, 216 Wh", "set": { "hover.wh": 216, "hover.batt_g": 950 }, "dims": [135, 50, 78], "source": "cell datasheet ..." },
    { "id": "6s3p", "label": "6S3P, 324 Wh", "set": { "hover.wh": 324, "hover.batt_g": 1410 }, "dims": [135, 70, 78], "source": "..." } ] }
```

A part can carry `sets` too, for example a gimbal's published mass and power: `"sets": { "hover.payload_g": 197, "hover.payload_w": 13.5 }`. When it is picked, the models use those values.

## CAD

The page's **Export CAD** button saves:
- the assembly as GLB, in metres, with y up
- the whole aircraft as STL
- each part as STL, in millimetres, with z up

Publish with the `downloads` capability so the saves work inside claude.ai. To write the same files into the product folder:

```bash
python "<skill dir>/scripts/export_cad.py" formgraph-products/<id>/<id>.html
```

They go to `formgraph-products/<id>/cad/`. Openings are drawn on the shell, not cut into the STL. Arms, motors and props are concept geometry for checks and layout. STEP and production surfaces come with the cloud design stage.

## Parameters for every part

The studio's Simple panel is sectioned by part name:
- **Fuselage shell:** size, shape, wall, draft, vents, bosses and ribs, held to the shell maker's limits.
- **Made and designed parts:**

  | Part | Parameters |
  |---|---|
  | Props | diameter, blades |
  | Arms | tube diameter, wall, prop tip gap |
  | Arm hinges | size against the tube, wall |
  | Motors | diameter, height |
  | Landing legs | height, diameter, wall |
  | Motor mounts, battery tray, gimbal mount | thickness |
  | Nose sensors | count |

- **Bought parts:** each with its datasheet size; change them under Options.

Each slider drives the 3D, the made part's size, cost and mass at its picked supplier, and the models. The prop diameter is the `hover.prop_d` model input, so flight time follows. Red bands show the picked supplier's limits, for example its minimum wall. Defaults come from the `airframe` block and `context/manufacturing.json`.
