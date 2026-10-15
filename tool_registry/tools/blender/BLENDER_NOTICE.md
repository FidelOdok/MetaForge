# Blender in the MetaForge Blender adapter image

The `metaforge-blender-adapter` image contains **Blender 5.2.2 LTS**, the
official Linux x64 build from blender.org, unmodified:

- Binary: https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz
  (SHA-256 `84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168`,
  checked when the image is built)
- Corresponding source: https://download.blender.org/source/blender-5.2.2.tar.xz
- Licence: Blender is free software under the GNU General Public License,
  version 2 or later; its binaries are distributed under GPL version 3
  (https://www.blender.org/about/license/). Blender's own copyright and
  licence files are kept in the image at `/opt/blender/` as shipped.

MetaForge does not modify Blender. It runs it as a separate program
(`blender --background --python ...`) and exchanges files with it.

## What is GPL, and what is not

| Part | Licence |
|---|---|
| Blender (in the image) | GPL-2.0-or-later, distributed as GPL-3.0 |
| `bpy_scripts/` (runs inside Blender, imports `bpy`) | GPL-3.0-or-later, see `bpy_scripts/LICENSE` |
| The rest of this adapter and MetaForge | MetaForge's own licence; it never imports `bpy` |
| Renders and .blend files the adapter produces | The user's ("What you create with Blender is your sole property", blender.org) |

A test (`tests/unit/test_blender_adapter.py`) fails if any MetaForge file
outside `bpy_scripts/` imports `bpy`, so the boundary cannot drift.
