---
name: move-files
description: Bring a person's files into MetaForge (images, photos, sketches, STEP, STL, OBJ, 3MF, G-code, KiCad projects, firmware, PDFs, spreadsheets, whole folders) and give files back to them (a download link, the content, or a copy in their project folder). Use when the user attaches or mentions a file they want MetaForge to use, asks to upload, import or send something, or asks to download, export or get a copy of a part, board, G-code or document.
---

# Move files in and out

Files from outside are **staged**: MetaForge checks each one, says what it
will become, and waits for a person to approve the upload before anything
enters the twin. Files going out come from work products already in the twin.

## Rules

1. **A person approves every upload.** Each way in returns an `approval_id`
   (`upload:<id>`). Tell the person it is waiting, and where: the dashboard's
   Approvals page, `forge approvals`, or `approvals.decide` if this client
   can ask them. Never decide it yourself, and never say the files are in the
   twin before `files.upload_status` shows `approved` with a
   `work_product_id` for each.
2. **Say what was left out.** A file with an unsupported type, an empty one,
   one over 100 MB, or an unknown requested type is listed with its `error`.
   Name each one; do not drop it silently.
3. **Do not read large files into the conversation.** Inline content goes
   through this chat. Above a few megabytes, use a link.
4. **Links are credentials.** Anyone with a link can use it until it
   expires. Give it to the person it is for and nobody else.

## Bringing files in

Pick by what this client can do and how big the files are:

| Situation | Use |
|---|---|
| Small files whose content you have (text you can read, or bytes you can base64), up to 8 MB in all | `files.import` with `files: [{name, text}]` or `[{name, content_base64}]` |
| Anything larger, or files you cannot read, and you have a shell (Claude Code, Codex) | `files.request_upload` with `expected_bytes`, then `curl -T <file> '<url>/<name>'` per file, then `curl -X POST '<url>/finish'` |
| A chat-only client (claude.ai), or the person has the files | `files.request_upload` with a `description` of what to send, and give the person the `url`: it opens a page where they choose files or a whole folder and press Finish |
| The local plugin (`metaforge-local`), files in the project folder | `files.import_local` with `paths` (files or folders, relative to the project folder) |

A folder goes in as a zip (`files.import` with a `.zip`, or a zip sent to an
upload link) or as several files whose names keep their folders
(`board/board.kicad_pcb`). Set `wp_type` on a file whose extension says too
little: a `.json` that is a BOM (`bom`), a `.md` that is the PRD (`prd`).

What each becomes: STEP, IGES and FreeCAD files `cad_model`; GLB, glTF,
OBJ and PLY `cad_model`; STL and 3MF `manufacturing_file`; G-code `gcode`;
images `image` (with their pixel size); KiCad schematics and boards
`schematic` and `pcb_layout`; Gerber and drill files `gerber` and
`manufacturing_file`; firmware source `firmware_source`; Markdown, text,
PDF and JSON `documentation`; CSV `bom`.

After the person approves, `files.upload_status` gives each file's
`work_product_id`. An image can then go to `tripo.image_to_3d` (see
`mesh-from-prompt`); a STEP to the CAD tools; G-code to
`library.dispatch_job` (see `manage-machines`).

## Giving files out

| Situation | Use |
|---|---|
| The person wants a file (any size) | `files.link`, and give them the `url` to click, or run `curl -o <name> '<url>'` with a shell |
| You need to look at a small file (G-code header, a BOM, a netlist) | `files.read`; text comes back as text, other files as base64; a large binary is refused with its size |
| The local plugin, and the person wants it in their project folder | `files.save_local` with a `destination`; it will not overwrite unless they say so |
| Which files a work product holds (a KiCad project, a render's views) | `files.list`, then pass `path` to the others |
| The client attaches MCP resources | `metaforge://files/<work_product_id>` (or `/<path>`), up to 2 MB |

A person at a terminal can do the same with `forge upload`, `forge download`
and `forge link`.

## Checklist

- [ ] the way in fits this client and the file sizes
- [ ] the person knows the upload waits for their approval, and where
- [ ] every file left out is named with its reason
- [ ] a link went only to the person who asked for it
