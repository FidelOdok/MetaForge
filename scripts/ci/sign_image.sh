#!/usr/bin/env bash
# Sign one published image by digest (FORGE-585, FORGE-823).
#
# Usage: scripts/ci/sign_image.sh <image@sha256:digest>
#
# Every workflow that pushes an image signs it through this script, so the
# signing policy lives in one place.
#
#   1. Keyless first, always: cosign exchanges the job's GitHub OIDC token for
#      a short-lived Fulcio certificate and records the signature in the
#      public Rekor log. Tried up to SIGN_ATTEMPTS times with a growing pause,
#      because Rekor outages are usually short.
#   2. If keyless still fails and COSIGN_PRIVATE_KEY is set, sign with that key
#      instead, without a transparency log entry (Rekor is what is down).
#      Hosts verify that signature against a cosign.pub they hold locally.
#   3. If keyless fails and there is no key, fail, exactly as before.
#
# Environment:
#   COSIGN_PRIVATE_KEY, COSIGN_PASSWORD   the fallback key and its password;
#                                         never printed, never on argv
#   COSIGN                                cosign binary (default: cosign)
#   SIGN_ATTEMPTS                         keyless attempts (default 3)
#   SIGN_RETRY_DELAY                      base pause in seconds (default 15);
#                                         attempt n waits (n - 1) * this
#   GITHUB_OUTPUT                         if set, receives method=keyless|key
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <image@sha256:digest>" >&2
  exit 2
fi
ref="$1"

# A tag is a moving pointer; a signature on it says nothing about tomorrow.
# An empty digest (a renamed build step) also lands here, as "image@".
case "$ref" in
  *@sha256:?*) ;;
  *)
    echo "refusing to sign '$ref': sign a digest (image@sha256:...), not a tag" >&2
    exit 2
    ;;
esac

COSIGN="${COSIGN:-cosign}"
attempts="${SIGN_ATTEMPTS:-3}"
delay="${SIGN_RETRY_DELAY:-15}"

record() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "method=$1" >>"$GITHUB_OUTPUT"; fi
}

for ((n = 1; n <= attempts; n++)); do
  if [ "$n" -gt 1 ]; then
    wait_s=$(((n - 1) * delay))
    echo "keyless signing failed; retrying in ${wait_s}s (attempt $n of $attempts)" >&2
    sleep "$wait_s"
  fi
  if "$COSIGN" sign --yes "$ref"; then
    echo "signed (keyless)  $ref"
    record keyless
    exit 0
  fi
done

if [ -z "${COSIGN_PRIVATE_KEY:-}" ]; then
  echo "keyless signing failed after $attempts attempt(s), and no COSIGN_PRIVATE_KEY is set" >&2
  exit 1
fi

# cosign reads the key from the variable by name and the password from
# COSIGN_PASSWORD itself, so neither value reaches argv or this log.
echo "keyless signing failed after $attempts attempt(s); signing with the fallback key" >&2
if ! "$COSIGN" sign --yes --key env://COSIGN_PRIVATE_KEY --tlog-upload=false "$ref"; then
  echo "key signing failed too" >&2
  exit 1
fi
echo "::warning title=Image key-signed::${ref} was signed with the MetaForge fallback key because Sigstore Rekor was unavailable. It has no transparency log entry; hosts verify it with cosign.pub."
echo "signed (key, no transparency log)  $ref"
record key
