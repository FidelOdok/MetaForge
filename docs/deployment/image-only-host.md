---
title: Running a host from images
---

# Running a host from images

A MetaForge host does not need the source. Every image the stack runs is
published to GitHub Container Registry and signed:

| Image | Published by |
|---|---|
| `metaforge-gateway` (also runs the MCP server and both workers) | `release.yml`, every push to `main` |
| `metaforge-freecad-adapter`, `-calculix-adapter`, `-cadquery-adapter`, `-kicad-adapter`, `-spice-adapter`, `-blender-adapter`, `-slicer-adapter`, `-render-adapter`, `-openfoam-adapter`, `-firmware-adapter` | `adapter-images.yml`, when their inputs change (see [When an image is rebuilt](#when-an-image-is-rebuilt)) |
| `metaforge-occt`, `metaforge-dashboard` | `adapter-images.yml`, when their folder changes |
| `metaforge-seaweedfs` (the object store) | `mirror-images.yml`: the signed upstream release, copied and re-signed as ours |

Each is tagged `:latest` and `:<commit sha>`. So a host keeps a small deploy
folder and updates by pulling, never by building.

The OpenFOAM adapter is opt-in. Its image is about 4 GB, so it runs only
under the `cfd` compose profile: add `cfd` to `COMPOSE_PROFILES` in the host's `.env`
to have the update pull and start it. Until then the gateway lists
`openfoam.run_internal_flow` but a call says the adapter is not running.

The firmware adapter is opt-in the same way. Its image carries the STM32
and ESP32 toolchains, ESP-IDF, Renode and Espressif QEMU, about 5.5 GB, so it runs only under
the `firmware` compose profile: add `firmware` to `COMPOSE_PROFILES` to have the update pull
and start it. Until then the gateway lists `firmware.build` and `firmware.run_emulated`, and
a call says the adapter is not running.

## When an image is rebuilt

`adapter-images.yml` runs on a push to `main` that touches `mcp_core/`,
`tool_registry/`, `observability/`, `tools/occt-converter/` or `dashboard/`,
but it rebuilds only the images whose own inputs changed. Its first job reads
`.github/adapter-image-filters.yml`, which lists for each image:

- its own folder, which holds its Dockerfile;
- the shared modules its Dockerfile copies, and no others;
- the CI files: the workflow, the filter file, the cosign installer and the
  import smoke test. A change to any of them rebuilds every image.

Each tool adapter copies only the shared code it imports: the top-level
modules of `mcp_core/` (not the harness plugin files beside them), the Python
modules of `observability/` (not its Grafana, Prometheus and Loki
configuration), the top of `tool_registry/`, `tool_registry/mcp_server/` and
its own folder. The FreeCAD and CalculiX adapters also carry
`tool_registry/tools/cadquery/`, for its material table.

| A change to | Rebuilds |
|---|---|
| one adapter's folder, for example `tool_registry/tools/kicad/` | that adapter |
| `tool_registry/tools/cadquery/` | cadquery, freecad and calculix |
| a top-level module of `mcp_core/` or `tool_registry/`, `tool_registry/mcp_server/`, or a Python module of `observability/` | every tool adapter |
| a tool folder no image carries, such as `tool_registry/tools/twin/`, or `mcp_core/plugin_*`, or the observability stack's configuration | nothing |
| `tools/occt-converter/` or `dashboard/` | that image |

An image that is not rebuilt keeps its previous signed `:latest`, so a host
pulling `latest` is unaffected.

Before an image is signed, CI runs it once and imports every module of the
adapter with the image's own interpreter (`scripts/adapter_import_smoke.py`).
A module that needs shared code the image does not carry fails the build
there, rather than on a host. When an adapter starts importing another shared
module, add it to the adapter's `COPY` lines and to its list in the filter
file; a unit test checks that every file a Dockerfile copies is in its list.

A pull request that changes the workflow, the filter file or a Dockerfile
builds the affected images and runs the same import check, and pushes
nothing.

To rebuild by hand, run the workflow from the Actions tab: `images` takes
`all` or a comma-separated list of filter keys, such as `kicad,firmware`. A
publish job that failed is not retried by the next push unless that push
changes its inputs too, so re-run the failed job or dispatch that image.

### Layer cache

Each image's layers are cached in GHCR beside it, under the tag `:buildcache`,
with every stage's layers kept (`mode=max`). The gateway image in
`release.yml` uses the same scheme. The Dockerfiles put the heavy, rarely
changing steps first (system packages, tool downloads, Python dependencies)
and the shared-code `COPY` lines last, so a code change rebuilds only the last
few layers: the tool downloads (Freerouting, Renode, Espressif QEMU, Blender)
do not run again. The tool downloads pin a version and a SHA-256 and retry on
any HTTP error, a 504 from GitHub's release downloads included. When
`github.com/.../releases/download` stays down, the Freerouting, Renode and
Espressif QEMU downloads (and cosign's) fetch the same release asset through
the GitHub API instead; the checksum is checked either way.

`:buildcache` is a build cache, not an image: it is unsigned and cannot be run.
Never point a host at it.

### Signing order

A published image is pushed by digest only, with no tag. CI then runs the
import check against that digest, signs the digest with cosign, and only then
points `:latest` and `:<commit sha>` at it, checking that the tag names exactly
the signed digest. If any step before the tag fails, `:latest` still names the
previous signed image, so `host_update.sh` never meets an unsigned `:latest`.
cosign itself is installed before the build, pinned to a version and a
SHA-256, with retries; when GitHub's release downloads are down it is taken
from the cosign project's own image on GHCR, pinned by digest.

## The deploy folder

| File | Where it comes from |
|---|---|
| `docker-compose.yml` | the repository, fetched by the update |
| `infra/temporal/dynamicconfig/`, the `observability/` config files | the repository, fetched by the update (only the files the stack mounts) |
| `evals/reports/` | created empty; the gateway writes reports here |
| `.env` | yours: secrets, `COMPOSE_PROFILES`, and `COMPOSE_FILE` if you add an override |
| a host-only override, such as `docker-compose.host.yml` | yours, optional |
| `.metaforge-bundle` | written by the update: the commit the bundle came from |

There is no `docker-compose.override.yml` on a host. That file mounts the
source into the containers for hot reload, which is what a host without a
checkout must not do. Everything a host serves is in `docker-compose.yml`,
including the MCP server the harness plugins connect to (`mcp-http`, port
8765), which runs the gateway image.

Put `prod` in `COMPOSE_PROFILES` to run the production dashboard, a static
build behind nginx on `DASHBOARD_PORT` (default 3000). It proxies `/api`,
`/ws` and `/health` to the gateway without buffering, so streamed chat replies
arrive as they are written.

## Updating

```bash
scripts/host_update.sh --dir ~/metaforge            # bundle from main
scripts/host_update.sh --dir ~/metaforge --ref <sha> # bundle from a commit
```

The script is the only file a host needs from the repository; copy it once.
Each run:

1. Fetches the bundle for the ref and replaces the bundle files, leaving
   `.env` and any override alone.
2. Pulls every image, and verifies each MetaForge image's signature by digest
   with `cosign` against this repository's CI identity. A MetaForge image that
   cannot be pulled, or a failed check, stops the update before anything
   restarts. A third-party image the registry no longer serves is only fatal
   when the host has no copy; otherwise the update says it used the local
   copy. `--no-verify` skips the signature check and says so for every image.
3. Starts the stack with `--no-build`, recreates every container whose image
   changed (a re-pulled tag does not move a running container), and waits for
   every service to be healthy.

To pin images to one build instead of `latest`, set `METAFORGE_GATEWAY_TAG`,
`METAFORGE_ADAPTER_TAG` and `METAFORGE_DASHBOARD_TAG` in `.env`.

## Moving a host off a checkout

The stack's data lives in named Docker volumes, which belong to the compose
project, not to the folder. Keep the same project name (`metaforge`, or set
`COMPOSE_PROJECT_NAME=metaforge` in `.env`) and the new folder picks up the
same databases.

1. Create the deploy folder and copy `.env` (and any host-only override) into
   it. Remove `docker-compose.override.yml` from `COMPOSE_FILE` if it is set.
2. Run `scripts/host_update.sh --dir <folder>` once.
3. Check the stack, then delete the old checkout.

## The object store

Blobs (knowledge documents, CAD files, simulation fields) live in the
`object-store` service, SeaweedFS. It replaced MinIO, whose
community images were discontinued and then deleted from Docker Hub, which
broke every host that pulled them. The image is our own GHCR copy of the
signed upstream release, pinned by tag and digest, so an upstream deletion
cannot happen to it.

- It serves S3 on port 9000 and answers to `minio` on the `storage` network,
  so `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` keep working
  unchanged. Only the gateway, `mcp-http`, the design-flow worker and
  Prometheus are on that network.
- Its storage internals listen on loopback inside the container; only the S3
  gateway is reachable, and it refuses a wrong or missing key.
- Bucket versioning works. Lifecycle transitions to a cold storage class do
  not (`NotImplemented`), so `scripts/storage/provision_minio.py` reports its
  lifecycle step as failed on this store. Use an external S3 service that
  supports transitions if you need cold-tier archiving.
- The gateway sets up the `metaforge-kb` bucket itself when it starts
: it creates the bucket, turns versioning on and reads the
  setting back. It runs in the background and retries for about five
  minutes, so an object store that starts after the gateway is fine. If it
  still fails, the gateway logs `kb_storage_init_failed`, counts it on
  `metaforge_kb_storage_init_total{outcome="failed"}`, and the
  `KBStorageInitFailed` alert fires; everything else keeps working. Before
  this, nothing ran that set-up, and the bucket was created by the first blob
  write with versioning off. A host whose bucket already exists gets
  versioning turned on at the next gateway start; objects written before that
  keep only their current version.
- There is no web console, unlike MinIO's on port 9001.

### Moving a host's data from MinIO

The old data stays in the `minio-data` volume until you copy it. Once the new
store is up:

```bash
set -a; . .env; set +a
# The old MinIO, read-only use, from the image the host already has
docker run -d --name minio-old --network metaforge_storage \
  -v metaforge_minio-data:/data \
  -e MINIO_ROOT_USER="${MINIO_ACCESS_KEY:-minioadmin}" -e MINIO_ROOT_PASSWORD="$MINIO_SECRET_KEY" \
  minio/minio server /data
# Copy (safe to re-run; ends by comparing counts and bytes per bucket)
docker run --rm --network metaforge_storage \
  -e SRC_ENDPOINT=minio-old:9000 -e SRC_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}" -e SRC_SECRET_KEY="$MINIO_SECRET_KEY" \
  -e DST_ENDPOINT=object-store:9000 -e DST_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}" -e DST_SECRET_KEY="$MINIO_SECRET_KEY" \
  -v "$PWD/copy_objects.py:/c.py:ro" --entrypoint python \
  ghcr.io/fidelodok/metaforge-gateway:latest /c.py
docker rm -f minio-old
```

`copy_objects.py` is `scripts/storage/copy_objects.py`; fetch it once like
`host_update.sh`. Delete the `minio-data` volume only after checking the
copy.

