#!/usr/bin/env bash
# Compute the next release version from Conventional-Commit subjects since the
# last tag, for the auto-release workflow. Versioning is "scheme B": while on
# 0.x the minor slot is the breaking signal and features/fixes are patches; the
# standard semver mapping only kicks in after 1.0.
#
#   0.x (major == 0):   <type>!/BREAKING -> minor    feat, fix, perf -> patch
#   >= 1.0:             <type>!/BREAKING -> major    feat -> minor    fix, perf -> patch
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

# Highest-precedence change type in the range: breaking > feat > fix/perf.
level=""
if printf '%s\n' "$subjects" | grep -qE '^[a-z]+(\([^)]*\))?!:' ||
  printf '%s\n' "$subjects" | grep -qiE 'BREAKING CHANGE'; then
  level="breaking"
elif printf '%s\n' "$subjects" | grep -qE '^feat(\([^)]*\))?:'; then
  level="feat"
elif printf '%s\n' "$subjects" | grep -qE '^(fix|perf)(\([^)]*\))?:'; then
  level="fix"
fi

if [ -z "$level" ]; then
  should="false"
  new="${last:-v0.0.0}"
  bump="none"
  emit
  exit 0
fi

base="${last#v}"
[ -z "$base" ] && base="0.0.0"
IFS=. read -r MA MI PA <<<"$base"
MA=${MA:-0}
MI=${MI:-0}
PA=${PA:-0}

if [ "$MA" -eq 0 ]; then
  # 0.x: minor is the breaking signal; feat/fix are patches.
  case "$level" in
    breaking)
      MI=$((MI + 1))
      PA=0
      bump="minor"
      ;;
    *)
      PA=$((PA + 1))
      bump="patch"
      ;;
  esac
else
  # >= 1.0: standard semver.
  case "$level" in
    breaking)
      MA=$((MA + 1))
      MI=0
      PA=0
      bump="major"
      ;;
    feat)
      MI=$((MI + 1))
      PA=0
      bump="minor"
      ;;
    fix)
      PA=$((PA + 1))
      bump="patch"
      ;;
  esac
fi
new="v${MA}.${MI}.${PA}"
should="true"
emit
