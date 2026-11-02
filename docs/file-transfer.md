# Files in and out

How a file gets from a person's computer into MetaForge, and back out again,
from every surface: the dashboard, the CLI, Claude Code, Codex and chat-only
clients such as claude.ai.

## Staged uploads

Anything that arrives from outside, except a person importing in the
dashboard, is **staged** first. Staging checks every file the way the import
will, opens a zip into its files, and records what each file would become.
Nothing is in the twin until a person approves the upload. Approving imports
every accepted file; rejecting deletes them.

An upload is an approval of kind `upload` (id `upload:<id>`) in
[`/v1/approvals`](approvals.md), so the dashboard's Approvals page, `forge
approvals` and the plugin's `approvals.decide` all show and decide it. It is a
human-authority kind: the agent that sent the files cannot approve them.

| Route | Purpose |
|-------|---------|
| `POST /v1/uploads` | Multipart `files` (one or more, or zips of folders), `project_id`, `description`, `wp_types` (a JSON object of file name to work product type) and `approve`. Returns the staged upload. `approve=true` imports at once and is refused (403) for `X-MetaForge-Surface: agent`. |
| `GET /v1/uploads?project_id=&status=` | Uploads, newest first. `status` is `open`, `pending`, `approved`, `rejected` or `expired`. |
| `GET /v1/uploads/{id}` | One upload, with each file's name, size, SHA-256, format, the type it becomes, its error if it cannot be imported, and its work product id once approved. |
| `GET /v1/uploads/{id}/files/{path}` | A staged file's bytes, for the person reviewing it. Images, PDFs and text are served inline; anything else, including SVG and HTML, downloads, and every response carries `Content-Security-Policy: sandbox`. Gone once the upload is decided. |

A file that cannot be imported (an unsupported type, an empty file, one over
100 MB, an unknown requested type) is listed with its reason and left out.
An upload with nothing importable is refused (422). A project id that is not
a UUID is refused before anything is staged.

### Folders

A folder arrives as a zip, or as several files in one upload whose names keep
their relative paths (the dashboard's folder picker sends `webkitRelativePath`).
A zip's files are staged under the zip's own name, so `board.zip` holding
`board.kicad_pcb` becomes `board/board.kicad_pcb`. Entries that are not the
person's files (`__MACOSX`, `.DS_Store`, `._*`, `Thumbs.db`) are skipped. A
name that would leave the upload's folder (`..`, an absolute path, a drive
letter) refuses the whole upload. One upload holds at most 500 files and 1 GB
once its zips are opened.

### Where staged files live

Records are SQLite rows in `~/.metaforge/uploads.db`
(`METAFORGE_UPLOAD_STORE_PATH`), so a restart does not lose an upload nobody
has looked at yet. The bytes are plain files under `~/.metaforge/staging/<id>/`
(`METAFORGE_UPLOAD_STAGING_DIR`), deleted once the upload is decided or
expires.

## What can be imported

`POST /v1/twin/import`, an approved upload and every later path share one
import (`api_gateway.twin.import_commit`): the same list, the same 100 MB
limit, the same metadata extraction and the same blob store.

| Kind | Extensions | Becomes |
|------|-----------|---------|
| CAD | `.step` `.stp` `.iges` `.igs` `.fcstd` | `cad_model` |
| Meshes | `.stl` (`manufacturing_file`), `.glb` `.gltf` `.obj` `.ply` | `cad_model` |
| Print projects | `.3mf` | `manufacturing_file` |
| G-code | `.gcode` `.gco` `.bgcode` | `gcode` |
| Images | `.png` `.jpg` `.jpeg` `.webp` `.gif` `.svg` | `image`, with `width_px` and `height_px` read from the header (not SVG) |
| Electronics | `.kicad_sch` `.kicad_pcb` `.kicad_pro` `.gbr` `.drl` `.xln` | `schematic`, `pcb_layout`, `documentation`, `gerber`, `manufacturing_file` |
| Firmware | `.c` `.h` `.cpp` `.hpp` `.hex` `.map` | `firmware_source` |
| Documents and data | `.md` `.txt` `.pdf` `.json` `.csv` | `documentation`, or `bom` for `.csv` |

`wp_type` (or `wp_types` on an upload) overrides the type a file's extension
implies, for example a `.json` that is a BOM or a `.md` that is the PRD.

A person importing through `POST /v1/twin/import` in the dashboard is the
approval, so that route imports directly, as it always has.
