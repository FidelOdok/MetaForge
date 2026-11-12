#!/usr/bin/env bash
# Give every MetaForge image a :<sha> tag for one commit on main (FORGE-852).
#
# Images are rebuilt only when their inputs change, so most commits produce a
# new gateway and nothing else. A host that pins METAFORGE_ADAPTER_TAG to a
# commit then finds no adapter with that tag. This script fills the gap: for
# each image the compose file pulls by `:latest`, an image that was not built
# at this commit gets the digest its nearest tagged ancestor carries, so
# `<image>:<sha>` always names exactly what `:latest` named once that commit's
# builds finished. One SHA then pins the whole stack.
#
# Nothing is rebuilt and nothing is re-signed: a signature belongs to a digest,
# and the tag points at an already-signed digest. Each digest is checked against
# this repository's CI identity before it is tagged.
#
# Usage: tag_commit_images.sh <sha>       (run from a full-history checkout)
# Env:   OWNER (default fidelodok), REPO (default $GITHUB_REPOSITORY),
#        DEPTH  ancestors to search (default 100), DRY_RUN=1 to only print,
#        REQUIRE_BUILT  space-separated image names (e.g. metaforge-gateway)
#                       that must have been built at this commit, not carried.
set -euo pipefail

sha="${1:?usage: tag_commit_images.sh <sha>}"
OWNER="${OWNER:-fidelodok}"
REPO="${REPO:-${GITHUB_REPOSITORY:-FidelOdok/MetaForge}}"
DEPTH="${DEPTH:-100}"
IDENTITY="^https://github\\.com/${REPO}/\\.github/workflows/"
ISSUER="https://token.actions.githubusercontent.com"

digest_of() {
  docker buildx imagetools inspect "$1" --format '{{json .Manifest.Digest}}' 2>/dev/null | tr -d '"'
}

# Images the compose file pulls at a moving `latest` default; anything pinned
# to an upstream version (seaweedfs) is already reproducible.
mapfile -t images < <(
  grep -oE "image: ghcr\.io/${OWNER}/metaforge-[a-z0-9-]+:\\\$\{METAFORGE_[A-Z_]+_TAG:-latest\}" docker-compose.yml \
    | sed -E "s#image: (ghcr\.io/${OWNER}/metaforge-[a-z0-9-]+):.*#\1#" | sort -u
)
[ "${#images[@]}" -gt 0 ] || { echo "no images found in docker-compose.yml" >&2; exit 1; }

mapfile -t ancestors < <(git rev-list --first-parent --max-count="$DEPTH" "${sha}~1")

failed=0
for image in "${images[@]}"; do
  if d=$(digest_of "${image}:${sha}") && [ -n "$d" ]; then
    echo "built     ${image}:${sha:0:12} = ${d}"
    continue
  fi
  if [[ " ${REQUIRE_BUILT:-} " == *" ${image##*/} "* ]]; then
    echo "NOT BUILT ${image}:${sha:0:12} must be built at this commit; not carried" >&2
    failed=1
    continue
  fi
  digest="" from=""
  for c in "${ancestors[@]}"; do
    if d=$(digest_of "${image}:${c}") && [ -n "$d" ]; then
      digest="$d" from="${c:0:12}"
      break
    fi
  done
  if [ -z "$digest" ]; then
    # Bootstrap: an image untouched since before this workflow existed has no
    # commit tag in reach. Its signed :latest is, by construction, what every
    # commit since its last build has run.
    digest=$(digest_of "${image}:latest") from="latest"
  fi
  if [ -z "$digest" ]; then
    echo "MISSING   ${image}: no :latest and no tagged ancestor" >&2
    failed=1
    continue
  fi
  if ! cosign verify --certificate-identity-regexp "$IDENTITY" \
      --certificate-oidc-issuer "$ISSUER" "${image}@${digest}" >/dev/null 2>&1; then
    # A key-signed digest (Rekor outage fallback) is accepted by hosts holding
    # cosign.pub; check that too when the key is available.
    if [ -n "${COSIGN_PUBKEY:-}" ] && cosign verify --key env://COSIGN_PUBKEY \
        --insecure-ignore-tlog=true "${image}@${digest}" >/dev/null 2>&1; then
      :
    else
      echo "UNSIGNED  ${image}@${digest} (from ${from}); not tagged" >&2
      failed=1
      continue
    fi
  fi
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would tag ${image}:${sha:0:12} -> ${digest} (from ${from})"
    continue
  fi
  docker buildx imagetools create --tag "${image}:${sha}" "${image}@${digest}" >/dev/null
  tagged=$(digest_of "${image}:${sha}")
  [ "$tagged" = "$digest" ] || { echo "${image}:${sha} is ${tagged}, not ${digest}" >&2; failed=1; continue; }
  echo "carried   ${image}:${sha:0:12} = ${digest} (from ${from})"
done

exit "$failed"
