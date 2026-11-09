# shellcheck shell=bash
# Verify one MetaForge image signature by digest (FORGE-593, FORGE-823).
#
# Sourced by scripts/host_update.sh, which sets COSIGN, IDENTITY, ISSUER and
# PUBKEY.
#
# Keyless first: the signature must come from this repository's CI identity,
# certified by GitHub's OIDC issuer. Only if that fails, and only if the
# operator has placed a public key at PUBKEY, is a key signature accepted
# instead. CI makes one of those only when Sigstore's public log was down
# (scripts/ci/sign_image.sh), so it carries no transparency log entry.
#
# PUBKEY is a file the operator put on the host, never one fetched with the
# bundle: a key that arrived alongside what it vouches for would vouch for
# nothing.
#
# Prints one "verified ..." line and returns 0, or prints the reason to
# stderr and returns 1.
verify_signature() {
  local digest="$1" err
  err=$(mktemp)
  if "$COSIGN" verify --certificate-identity-regexp "$IDENTITY" \
    --certificate-oidc-issuer "$ISSUER" "$digest" >/dev/null 2>"$err"; then
    rm -f "$err"
    echo "verified  $digest"
    return 0
  fi
  if [ -n "${PUBKEY:-}" ] && [ -f "$PUBKEY" ]; then
    if "$COSIGN" verify --key "$PUBKEY" --insecure-ignore-tlog=true \
      "$digest" >/dev/null 2>>"$err"; then
      rm -f "$err"
      echo "verified (key, no transparency log)  $digest"
      return 0
    fi
  fi
  echo "SIGNATURE CHECK FAILED for $digest; nothing restarted" >&2
  tail -3 "$err" >&2
  rm -f "$err"
  return 1
}
