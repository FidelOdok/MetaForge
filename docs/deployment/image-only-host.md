---
title: Running a host from images
---

# Running a host from images

A MetaForge host does not need the source. Every image the stack runs is
published to GitHub Container Registry and signed (FORGE-593):

| Image | Published by |
|---|---|
| `metaforge-gateway` (also runs the MCP server and both workers) | `release.yml`, every push to `main` |
| `metaforge-freecad-adapter`, `-calculix-adapter`, `-cadquery-adapter`, `-kicad-adapter`, `-spice-adapter` | `adapter-images.yml`, when their code changes |
| `metaforge-occt`, `metaforge-dashboard` | `adapter-images.yml`, when their code changes |

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
checkout must not do.

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
   with `cosign` against this repository's CI identity. A failed check stops
   the update before anything restarts. `--no-verify` skips the check and says
   so for every image.
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
