---
name: mesh-from-prompt
description: Generate a 3D mesh from a text prompt or a reference image with Tripo AI or Meshy AI, bring it into MetaForge and keep it in the twin, optionally converted to STL or 3MF for printing. Use when the user asks for an organic or decorative shape, a figurine, a concept or look-alike model, or "make a 3D model of this picture". Do not use for a part that needs exact dimensions, hole patterns or mating faces (that is CAD: generate_cad_script or generate_cad), or for an FEA mesh (that is generate_mesh).
---

# Mesh from a prompt or an image

Tripo and Meshy are cloud services that turn a prompt or a picture into a
textured triangle mesh in one to three minutes. MetaForge reaches them through
the `tripo.*` and `meshy.*` tools when the deployment has `TRIPO_API_KEY` or
`MESHY_API_KEY` set. A generated mesh is triangles, not parametric CAD: it
cannot be dimensioned, filleted or toleranced afterwards.

## Rules

1. **Is it the right tool?** If the part has to fit something (a bolt
   pattern, a PCB, a mating face, a wall thickness), say that a mesh cannot
   hold those and use CAD instead. Use a mesh for shapes nobody would draw in
   CAD: characters, sculpted handles, organic covers, concept models.
2. **Generation spends credits.** `text_to_3d`, `image_to_3d`, `convert`
   and Meshy's `refine_texture` all charge the person's account. Say so before
   the first job, and check `tripo.get_balance` before starting several.
   `get_task`, `download` and `get_balance` are free.
3. **One job, then look.** Start one job, wait for it, show the result
   (`include_preview` on `tripo.download` saves the preview image). Do not
   start variants in parallel unless the person asks.
4. **A failed job says why.** A `failed`, `banned` or `FAILED` status
   carries the service's reason. Tell the person; do not resubmit the same
   prompt or image unchanged.
5. **Download before the link expires.** The services' own links expire
   within days. A mesh nobody downloaded is lost.
6. **Scale is arbitrary.** A generated mesh has no real size. Ask what size it
   should be. Tripo's `auto_size` sets a real-world size in metres; otherwise
   scale it before slicing (check the bounding box with `zoo.mass_properties`).

## Which service

Use whichever is configured. If both are, prefer Tripo: it can also decimate,
convert to STL or 3MF and flatten the underside for a print bed
(`tripo.convert`). Meshy's preview is untextured; `meshy.refine_texture`
textures it, which a single-colour print does not need.

## Steps (Tripo)

1. `tripo.text_to_3d` with `prompt` (and `negative_prompt` if useful), or
   `tripo.image_to_3d` with `file_path` (a PNG, JPEG or WebP on the workspace)
   or a public https `image_url`. It returns a `task_id` at once.
2. `tripo.get_task` with the `task_id`. It waits up to 60 seconds; call it
   again while it is still `running`.
3. For printing, `tripo.convert` with the `task_id`, `format` `stl` or `3mf`,
   and `flatten_bottom: true`; then `tripo.get_task` on the new `task_id`.
4. `tripo.download` with the final `task_id`. It returns a workspace
   `file_path` and `format`.
5. Keep it: `twin.commit_geometry` with that `file_path` unchanged, the
   returned `format`, and `source_tool` `tripo.download`.

## Steps (Meshy)

1. `meshy.text_to_3d` with `prompt`, or `meshy.image_to_3d` with an
   `image_url` (public https or a `data:image/...` URI).
2. `meshy.get_task` with `task_id` and `kind` until `SUCCEEDED`.
3. `meshy.download` with the `task_id`, `kind` and the formats wanted (for
   example `stl`).
4. `twin.commit_geometry` with the returned `file_path`, its format, and
   `source_tool` `meshy.download`.

## Next

To print it, follow `slice-for-printing`: slice it for the person's printer,
or have Slant 3D price it.

## Checklist

- [ ] a mesh, not CAD, is right for this part
- [ ] the person knows generation spends credits
- [ ] downloaded and committed to the twin
- [ ] real-world size set or confirmed
