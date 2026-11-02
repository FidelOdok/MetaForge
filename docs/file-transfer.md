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

## From an MCP client: `files.*`

Every plugin (Claude Code, its cloud and local variants, Codex) and any other
MCP client reaches files through one adapter. In the gateway and the local
plugin it runs in-process; in the sidecar it calls the gateway's routes.

| Tool | Does | Classified |
|------|------|-----------|
| `files.import` | Stages small files sent as content in the call: `text`, or `content_base64`, up to 8 MB in all. A zip is a folder. | Producing; not held, because the upload is itself an approval |
| `files.request_upload` | Opens an upload and returns a signed `url` that takes files of any size, and the `curl` lines to use it. | Producing; not held, for the same reason |
| `files.upload_status` | An upload's state and each file's outcome. | Read-only |
| `files.list` | A work product's files: the primary one and any named ones. | Read-only |
| `files.read` | A file's content: text for text formats, base64 otherwise. Up to 2 MB; a larger binary is refused with its size, a longer text is cut and says so. | Read-only |
| `files.link` | A signed download `url` for a file. | Read-only |
| `files.import_local` | The local plugin only: stages files or folders from the project folder. | Producing; not held |
| `files.save_local` | The local plugin only: writes a work product's file into the project folder, never over an existing file unless asked. | Additive |

Which way in suits which client:

| Client | Small files | Large files and folders |
|--------|-------------|-------------------------|
| Claude Code, Codex | `files.import` | `files.request_upload`, then `curl -T` and `curl -X POST <url>/finish` |
| claude.ai and other chat-only clients | `files.import` | `files.request_upload`; the person opens the `url`, picks files or a folder, and presses Finish |
| The local plugin | `files.import_local` | `files.import_local` |
| The dashboard | the import area (`POST /v1/twin/import`) or `POST /v1/uploads` | the same, with folders |

The plugin skill `move-files` tells the model which to use. From a
terminal, `forge upload`, `forge download` and `forge link` do the same
([CLI reference](cli-reference.md#files)).

### Files as resources

A work product's file is also an MCP resource,
`metaforge://files/{work_product_id}` (its primary file) or
`metaforge://files/{work_product_id}/{path}` (a named one), for clients that
attach resources, such as Claude Code's `@` mentions. Text formats come back
as `text`; images, PDFs, meshes and other binaries as a base64 `blob` with
their MIME type (`image/png`, `model/step`, ...). The limit is 2 MB, as for
`files.read`; past it the read fails and names `files.link`. How each client
presents a binary resource is the client's own: this follows the MCP
specification, and has been checked against the protocol, not yet against
every client.

In the dashboard, the Files page uploads files and folders (dropped
folders keep their structure), the Approvals page shows each staged file
with a **view** button, and a work product's Preview has **Copy link**.

Twin file routes (`GET /v1/twin/nodes/{id}/file` and `/files/{name}`)
serve SVG and HTML with `Content-Security-Policy: sandbox allow-scripts`,
since files now arrive from outside: opened directly, such a file runs in
an opaque origin and cannot use the gateway's cookies, storage or API.

## Signed links

A link is its own credential, so a person's browser or `curl` can use it
without the gateway's token, and the plugin's OAuth token stays where it is.
A token names what it is for (a download or an upload, and which work
product, file or upload) and when it ends, signed with HMAC-SHA256. Nothing is
stored per link: it cannot be listed or revoked one by one, it ends at its
expiry, and changing the secret ends them all.

| Route | Purpose |
|-------|---------|
| `POST /v1/files/{id}/link` | Body `{path, lifetime_s}`. A download link: `url` for an object-store link, else `path` (`/v1/links/<token>`). |
| `GET /v1/files/{id}` | The work product's files. |
| `GET /v1/files/{id}/read?path=&max_bytes=` | Inline content, as `files.read`. |
| `POST /v1/uploads/link` | Body `{project_id, description, expected_bytes, lifetime_s}`. Opens an upload and returns its link `path`, `upload_id`, `approval_id` and `expires_at`. |
| `POST /v1/uploads/inline` | Body `{files: [{name, text \| content_base64, wp_type}], project_id, description}`. Stages files, as `files.import`. |
| `GET /v1/links/{token}` | A download link's file (images and PDFs inline, everything else as an attachment, always with `Content-Security-Policy: sandbox`), or an upload link's page. |
| `POST /v1/links/{token}` | Multipart `files`: adds files to the upload. |
| `PUT /v1/links/{token}/{name}` | A raw body as one file: `curl -T part.step <url>/part.step`. |
| `POST /v1/links/{token}/finish` | Closes the upload; it then waits in `/v1/approvals`. |

`/v1/links/` is the one path prefix the auth middleware lets through without a
bearer token (`PUBLIC_PREFIXES`); every route under it checks the link
instead, and refuses a forged, expired or wrong-kind link with 403.

**Lifetime.** A link lives 15 minutes plus 20 seconds per megabyte of the
file (or of `expected_bytes` for an upload), at most 24 hours, unless
`lifetime_s` asks for another value within those bounds. An upload that is
still open when its link expires is discarded.

**The secret.** `METAFORGE_LINK_SECRET`, else a random secret kept in
`~/.metaforge/link_secret` (`METAFORGE_LINK_SECRET_PATH`), so a restart does
not break links already handed out.

**Where a link points.** The person reaches the sidecar, not the gateway, so
the sidecar serves `/v1/links/*` itself, passing each request to the gateway
(`METAFORGE_GATEWAY_URL`), and makes links absolute on the address the MCP
request came in on (the same address its OAuth metadata uses), or on
`METAFORGE_MCP_PUBLIC_URL` when set. A link from the gateway's own tools uses
`METAFORGE_GATEWAY_PUBLIC_URL`, else stays a path the dashboard resolves.

**Object-store links.** With `METAFORGE_LINK_OBJECT_STORE_ENDPOINT` set to
the object store's address as a browser reaches it, a download link for a
file kept in the store is a presigned URL on that address, and the bytes do
not pass through the gateway. Set `MINIO_REGION` too, or presigning first
asks the store for its region. Without the setting, or if presigning fails,
the link goes through the gateway.

## The local plugin

`metaforge-local` runs on the person's own machine over stdio, with no
gateway and no HTTP server, so a link would point nowhere. Instead it reads
and writes the project folder directly: `METAFORGE_LOCAL_FILES_ROOT`, else the
folder the client started it in. Nothing outside that folder is read or
written, and `.git`, `node_modules`, `__pycache__`, `.venv` and `.forge` are
skipped when a folder is imported.

## G-code to a printer

`slicer.slice` writes G-code to the adapter workspace.
`twin.record_design_files` with `work_product_type: gcode` records the run
folder (the G-code and the `config.ini` it was sliced with) as a `gcode`
work product, linked `GENERATED_FROM` the part; `library.dispatch_job` then
sends it to an in-house printer. An uploaded `.gcode`, `.gco` or `.bgcode`
file becomes the same type.
