#!/usr/bin/env sh
#
# Install the standalone `forge` CLI binary from GitHub Releases (MET-555).
#
#   curl -fsSL https://raw.githubusercontent.com/FidelOdok/MetaForge/main/scripts/install.sh | sh
#
# Install a specific version:
#   curl -fsSL .../install.sh | sh -s v0.1.0
#
# Override the install dir with FORGE_BIN_DIR (default: ~/.local/bin).
# Windows: download forge-windows-x64.exe from the Releases page directly.

set -eu

REPO="FidelOdok/MetaForge"
BIN_DIR="${FORGE_BIN_DIR:-$HOME/.local/bin}"
VERSION="${1:-latest}"

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Linux)
    case "$arch" in
      x86_64 | amd64) asset="forge-linux-x64" ;;
      *) echo "Unsupported Linux arch: $arch (build from source: scripts/build_forge_binary.sh)" >&2; exit 1 ;;
    esac
    ;;
  Darwin)
    case "$arch" in
      arm64 | aarch64) asset="forge-macos-arm64" ;;
      x86_64)
        echo "No prebuilt Intel-Mac binary is published. Options:" >&2
        echo "  - Apple Silicon Mac: this installer works there." >&2
        echo "  - Intel Mac: build from source (scripts/build_forge_binary.sh)" >&2
        echo "    or run 'pip install -e .' and use the 'forge' console script." >&2
        exit 1
        ;;
      *) echo "Unsupported macOS arch: $arch" >&2; exit 1 ;;
    esac
    ;;
  *)
    echo "Unsupported OS: $os. On Windows, download forge-windows-x64.exe from:" >&2
    echo "  https://github.com/$REPO/releases" >&2
    exit 1
    ;;
esac

if [ "$VERSION" = "latest" ]; then
  url="https://github.com/$REPO/releases/latest/download/$asset"
else
  url="https://github.com/$REPO/releases/download/$VERSION/$asset"
fi

echo "Installing forge ($asset, $VERSION) -> $BIN_DIR/forge"
mkdir -p "$BIN_DIR"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$url" -o "$BIN_DIR/forge"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$BIN_DIR/forge" "$url"
else
  echo "Need curl or wget to download." >&2
  exit 1
fi
chmod +x "$BIN_DIR/forge"

echo "Installed forge to $BIN_DIR/forge"

# Make sure BIN_DIR is on PATH. If it already is, we're done. Otherwise append
# the export to the shell profile (idempotent). Opt out with FORGE_NO_MODIFY_PATH=1.
path_line="export PATH=\"$BIN_DIR:\$PATH\""
case ":$PATH:" in
  *":$BIN_DIR:"*)
    echo "Run: forge --help"
    ;;
  *)
    if [ "${FORGE_NO_MODIFY_PATH:-0}" = "1" ]; then
      echo "$BIN_DIR is not on your PATH. Add it manually:"
      echo "  $path_line"
    else
      # Choose a profile based on the login shell.
      case "$(basename "${SHELL:-sh}")" in
        zsh) profile="$HOME/.zshrc" ;;
        bash)
          if [ "$(uname -s)" = "Darwin" ]; then
            profile="$HOME/.bash_profile"
          else
            profile="$HOME/.bashrc"
          fi
          ;;
        *) profile="$HOME/.profile" ;;
      esac
      touch "$profile" 2>/dev/null || true
      if grep -qF "$path_line" "$profile" 2>/dev/null; then
        echo "PATH already configured in $profile."
      else
        printf '\n# Added by the MetaForge forge installer\n%s\n' "$path_line" >> "$profile"
        echo "Added $BIN_DIR to PATH in $profile."
      fi
      echo "Restart your shell (or run: $path_line) then: forge --help"
    fi
    ;;
esac
