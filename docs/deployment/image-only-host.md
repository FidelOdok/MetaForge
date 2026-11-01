---
title: Running a host from images
---

# Running a host from images

A MetaForge host does not need the source. Every image the stack runs is
published to GitHub Container Registry and signed:

| Image | Published by |
|---|---|
| `metaforge-gateway` (also runs the MCP server and both workers) | `release.yml`, every push to `main` |
| `metaforge-freecad-adapter`, `-calculix-adapter`, `-cadquery-adapter`, `-kicad-adapter`, `-spice-adapter`, `-slicer-adapter`, `-openfoam-adapter`, `-firmware-adapter` | `adapter-images.yml`, when their code changes |

The OpenFOAM adapter is opt-in. Its image is about 4 GB, so it runs only
under the `cfd` compose profile: add `cfd` to `COMPOSE_PROFILES` in the host's `.env`
to have the update pull and start it. Until then the gateway lists
`openfoam.run_internal_flow` but a call says the adapter is not running.

The firmware adapter is opt-in the same way. Its image carries the STM32
and ESP32 toolchains, ESP-IDF, Renode and Espressif QEMU, about 5.5 GB, so it runs only under
the `firmware` compose profile: add `firmware` to `COMPOSE_PROFILES` to have the update pull
and start it. Until then the gateway lists `firmware.build` and `firmware.run_emulated`, and
a call says the adapter is not running.
| `metaforge-occt`, `metaforge-dashboard` | `adapter-images.yml`, when their code changes |
| `metaforge-seaweedfs` (the object store) | `mirror-images.yml`: the signed upstream release, copied and re-signed as ours |

Each is tagged `:latest` and `:<commit sha>`. So a host keeps a small deploy
folder and updates by pulling, never by building.

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

