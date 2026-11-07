# References

References are images, sketches, video and meshes the user gives as intent: how it should look, how big it is, where it must fit. They go in `context/references.json`, with files under `context/refs/`. The page shows them and checks the design against them.

## Kinds and roles

| kind | What | How it is used |
|---|---|---|
| `image` | photo, render, mood image | thumbnail and full view; values you read from it |
| `sketch` | hand or digital sketch | same as an image; proportions are usually estimated |
| `frame` | a still pulled from a video (`frameOf` the video id, `time_s`) | same as an image |
| `video` | a clip | never embedded; list its frames in `frames` |
| `mesh` | STL, OBJ or GLB | size and volume are read; shown see-through in 3D |

| role | Meaning | Check on the page |
|---|---|---|
| `look` | inspiration only | none |
| `target` | the design should match it | each form value within tolerance; a miss is a warning |
| `envelope` | the design must fit inside it | width, height, depth no larger; a miss blocks gate 2 (Concept) |
| `context` | what it sits on or next to | shown in 3D only |

## Reading an image, sketch or frame

Open each image with the Read tool and record what you can see, in this order:

1. **Scale.** Look for a stated dimension, a ruler, a hand, a known part (a USB-C port is 8.94 mm wide). Record it as `scale: {px, mm, basis}`. Without a scale, every size is `estimated`.
2. **Proportions and sizes:** `form.W`, `form.D`, `form.H`, `form.R` (corner radius), `form.F` (top fillet), `form.P` (depth over width).
3. **Features:** `feature.<name>` (button, display, port, vent, light, handle, foot) with where it is.
4. **Colour and finish:** `color.<part>` as a hex value; `finish.<part>` (gloss, matte, texture).
5. **Use and mounting:** `use.<what>`, `mount.<how>` (held, wall, desk, strapped).

Every value gets:
- `confidence`:
  - `measured`: scaled from a known length
  - `estimated`: judged by eye
  - `read`: written on the reference
- `basis`: how you got it

Never mark a size `measured` without a scale. The builder rejects it.

Then use the values. Set form params from target references and note the reference id as their source. Add features as functions where they map (a button becomes `e_btn`). Record the colour. Add a ledger line per reference so nothing it implies is dropped.

An image only seen in the chat (pasted, with no file) can still be recorded with `"source": "chat"` and no `file`; the page lists it without a thumbnail.

## Video, capped to save compute

Never embed video. Pull a few frames:

```bash
python "<skill dir>/scripts/ref_tools.py" frames context/refs/clip.mp4 --out context/refs --count 6
```

The script caps every run:
- at most 12 frames, 6 by default
- only the first 60 s is read, never more than 300 s
- frames are no wider or taller than 1,280 px
- frames are saved as JPEG

It uses ffmpeg when installed, otherwise headless Chrome on a temporary copy. List the frames as `frame` references and the clip as a `video` with `frames: [ids]`. Read only the frames you need. The builder rejects more than 12 frames per video.

## Meshes

```bash
python "<skill dir>/scripts/ref_tools.py" mesh context/refs/part.glb
```

This prints the size, triangle count, area and volume.
- **Units:** GLB defaults to metres with y up; STL and OBJ default to millimetres with z up. Set `units` (mm, cm, m, in) and `up` (y or z) when the defaults are wrong. If the size looks implausible, ask the user for one real dimension before using the mesh as a target or envelope.
- **Display:** large meshes are reduced for display. The checks always use the full mesh's size.
- **Placement:** `at` [x, y, z] in mm and `rotate` [x, y, z] in degrees place it relative to the design. It is centred on the footprint and sits on the ground by default.
- **Limits:** Draco-compressed GLB is not supported; export without compression.

## Size limits

Images over 2 MB are rejected. Shrink them with:

```bash
python "<skill dir>/scripts/ref_tools.py" shrink context/refs/photo.jpg
```

All references together must stay under 10 MB in the page.

## Example

```json
{ "schemaVersion": 1, "references": [
  { "id": "front", "kind": "image", "file": "refs/front.jpg", "title": "Front", "view": "front", "role": "target",
    "scale": { "px": 140, "mm": 140, "basis": "height stated on the drawing" },
    "extracted": [
      { "what": "form.H", "value": 140, "unit": "mm", "confidence": "measured", "basis": "140 px at 1 mm per px" },
      { "what": "feature.button", "value": "round, front face, right of centre", "confidence": "read", "basis": "visible in the image" },
      { "what": "color.body", "value": "#30343B", "confidence": "read", "basis": "dominant body tone" } ] },
  { "id": "slot", "kind": "mesh", "file": "refs/slot.stl", "title": "Shelf slot", "role": "envelope", "units": "mm", "up": "z" }
] }
```
