#!/bin/sh
# Install the MetaForge package for opencode.
#
# Shipped in integrations/opencode by scripts/build_integrations.py. It copies
# the commands, agents, skills and working rules into an opencode config
# directory, then adds the `metaforge` MCP server and the rules file to that
# directory's opencode.json. Existing settings are kept: nothing is replaced,
# a backup is written before any change, and a file the script cannot rewrite
# safely (JSONC with comments) is left alone with the lines to add printed.
#
# No credential is written anywhere. Sign in afterwards with
#   opencode mcp auth metaforge
set -eu

usage() {
    cat <<'EOF'
Usage: install.sh [--project DIR | --global]

  --project DIR   install into DIR/.opencode (default: the current directory)
  --global        install into ~/.config/opencode (or $XDG_CONFIG_HOME/opencode)
EOF
}

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
MODE=project
DIR=$(pwd)
while [ $# -gt 0 ]; do
    case "$1" in
        --global) MODE=global; shift ;;
        --project)
            [ $# -ge 2 ] || { usage >&2; exit 2; }
            MODE=project; DIR=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [ "$MODE" = global ]; then
    TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
else
    [ -d "$DIR" ] || { echo "install.sh: no such directory: $DIR" >&2; exit 2; }
    DIR=$(CDPATH='' cd -- "$DIR" && pwd)
    TARGET="$DIR/.opencode"
fi

command -v python3 >/dev/null 2>&1 || {
    echo "install.sh: python3 is needed to merge opencode.json" >&2
    exit 2
}

mkdir -p "$TARGET/commands" "$TARGET/agents" "$TARGET/skills"
# Only MetaForge's own folders are replaced, so a removed command or skill
# does not linger after an upgrade and nothing else in the directory is touched.
rm -rf "$TARGET/commands/metaforge" "$TARGET/skills/metaforge"
cp -R "$HERE/commands/metaforge" "$TARGET/commands/metaforge"
cp -R "$HERE/skills/metaforge" "$TARGET/skills/metaforge"
cp "$HERE"/agents/metaforge-*.md "$TARGET/agents/"
cp "$HERE/metaforge-rules.md" "$TARGET/metaforge-rules.md"

status=0
python3 - "$HERE/opencode.json" "$TARGET" "$MODE" <<'PY' || status=$?
import json
import os
import shutil
import sys

package_config, target, mode = sys.argv[1:4]
with open(package_config, encoding="utf-8") as handle:
    ours = json.load(handle)
server = ours["mcp"]["servers"]["metaforge"]
# A project keeps a relative entry so the directory can be committed and
# shared; the global config needs the absolute path to find the file.
if mode == "project":
    rules = ".opencode/metaforge-rules.md"
else:
    rules = os.path.join(target, "metaforge-rules.md")

snippet = json.dumps(
    {"mcp": {"servers": {"metaforge": server}}, "instructions": [rules]}, indent=2
)


def by_hand(path, reason):
    print(f"install.sh: left {path} unchanged: {reason}.", file=sys.stderr)
    print("Add these entries to it yourself:", file=sys.stderr)
    print(snippet, file=sys.stderr)
    sys.exit(3)


existing = [
    os.path.join(target, name)
    for name in ("opencode.jsonc", "opencode.json")
    if os.path.exists(os.path.join(target, name))
]
if len(existing) > 1:
    by_hand(" and ".join(existing), "both files exist, so it is not clear which one to edit")
path = existing[0] if existing else os.path.join(target, "opencode.json")

if existing:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        by_hand(path, "it is not plain JSON (comments or trailing commas), and rewriting it would drop them")
else:
    data = {"$schema": "https://opencode.ai/config.json"}
if not isinstance(data, dict):
    by_hand(path, "its top level is not an object")

notes = []
mcp = data.setdefault("mcp", {})
if not isinstance(mcp, dict):
    by_hand(path, "its `mcp` entry is not an object")
# V1 configs put servers straight under `mcp`; V2 puts them under
# `mcp.servers`. Follow whichever shape the file already uses.
legacy = any(key not in ("servers", "timeout") for key in mcp)
servers = mcp if legacy else mcp.setdefault("servers", {})
current = servers.get("metaforge")
if current is None:
    servers["metaforge"] = dict(server)
elif current.get("url") != server["url"]:
    notes.append(
        f"kept your existing `metaforge` server ({current.get('url')}); "
        f"this package's address is {server['url']}"
    )

instructions = data.setdefault("instructions", [])
if not isinstance(instructions, list):
    by_hand(path, "its `instructions` entry is not a list")
if rules not in instructions:
    instructions.append(rules)

if existing:
    shutil.copyfile(path, path + ".bak")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(data, indent=2) + "\n")
print(f"updated {path}" + (f" (backup: {path}.bak)" if existing else ""))
for note in notes:
    print(f"note: {note}")
PY

echo "installed MetaForge commands, agents, skills and rules into $TARGET"
if [ "$status" -ne 0 ]; then
    echo "opencode.json was not changed; add the entries printed above, then sign in." >&2
    exit 1
fi
echo "next: opencode mcp auth metaforge   (sign in with your MetaForge email and password)"
