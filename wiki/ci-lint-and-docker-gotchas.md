# CI, Lint & Docker Gotchas

## `ruff check` passing locally does not mean CI passes

CI's Lint & Format job runs `ruff check` **and** `ruff format --check` as two separate steps. A clean `ruff check` locally does not predict the format-check step — it independently catches line-wrapping, quote-style, and spacing issues that the linter doesn't flag. Before pushing any Python change, run both: `ruff check <files>` and `ruff format --check <files>` (or `ruff format <files>` to auto-fix). If CI's Lint & Format job fails despite a clean local `ruff check`, check `ruff format --check .` first before assuming something environment-specific is going on.

## Forcing a genuinely different Docker internal IP

`docker compose up -d --force-recreate <svc>` often hands back the **same** internal IP if nothing else claimed it in between — Docker's IPAM allocator gives out the lowest free address, and on a quiet network that's usually the one just released. This makes "goes stale on IP change" bugs hard to verify, because a recreate that looks like a test can silently be a no-op.

**To force a real IP change**: stop the service, start a throwaway container on the same network to occupy the freed address (`docker run -d --rm --name ip-decoy --network <net> alpine:3 sleep 30`), then start the real service — it gets forced onto a new address. Confirm with `docker inspect <container> --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}}{{end}}'` before and after. The decoy self-removes.

## WSL2 file watching

File watchers need polling mode on WSL2 — inotify doesn't cross the Windows/Linux filesystem boundary, so a bare hot-reload setup silently stops picking up changes made from the Windows side.
