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
      x86_64) asset="forge-macos-x64" ;;
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

echo "Installed. Ensure $BIN_DIR is on your PATH, then run: forge --help"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  (add it:  export PATH=\"$BIN_DIR:\$PATH\")" ;;
esac
