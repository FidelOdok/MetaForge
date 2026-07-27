#!/usr/bin/env bash
# Compute the next release version from Conventional-Commit subjects since the
# last tag, for the auto-release workflow.
#
#   feat            -> minor        fix, perf       -> patch
#   <type>!, BREAKING CHANGE -> minor  (0.x: breaking stays a minor bump)
#   only docs/chore/ci/test/style/refactor/build -> no release
#
# When triggered by a tag push, that tag is the version verbatim.
# Emits `version`, `should_release`, and `bump` to $GITHUB_OUTPUT (and stderr).
set -euo pipefail

emit() {
  echo "last=${last:-none} bump=${bump:-none} version=$new should_release=$should" >&2
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
      echo "version=$new"
      echo "should_release=$should"
      echo "bump=${bump:-none}"
    } >>"$GITHUB_OUTPUT"
  fi
}

# Manual tag push: use it as-is.
if [[ "${GITHUB_REF:-}" == refs/tags/* ]]; then
  new="${GITHUB_REF#refs/tags/}"
  should="true"
  bump="explicit"
  last=""
  emit
  exit 0
fi

last="$(git describe --tags --abbrev=0 2>/dev/null || echo "")"
range="HEAD"
[ -n "$last" ] && range="${last}..HEAD"
subjects="$(git log --format='%s' ${range})"

bump=""
if printf '%s\n' "$subjects" | grep -qE '^[a-z]+(\([^)]*\))?!:' ||
  printf '%s\n' "$subjects" | grep -qiE 'BREAKING CHANGE'; then
  bump="minor" # 0.x: breaking is a minor bump, not major
elif printf '%s\n' "$subjects" | grep -qE '^feat(\([^)]*\))?:'; then
  bump="minor"
elif printf '%s\n' "$subjects" | grep -qE '^(fix|perf)(\([^)]*\))?:'; then
  bump="patch"
fi

if [ -z "$bump" ]; then
  should="false"
  new="${last:-v0.0.0}"
  emit
  exit 0
fi

base="${last#v}"
[ -z "$base" ] && base="0.0.0"
IFS=. read -r MA MI PA <<<"$base"
MA=${MA:-0}
MI=${MI:-0}
PA=${PA:-0}
case "$bump" in
  minor)
    MI=$((MI + 1))
    PA=0
    ;;
  patch) PA=$((PA + 1)) ;;
esac
new="v${MA}.${MI}.${PA}"
should="true"
emit
