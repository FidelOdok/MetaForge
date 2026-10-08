"""Package the MetaForge plugin as a portable bundle (FORGE-575).

ChatGPT cloud installs a plugin by **uploading an archive**, not by adding a
marketplace the way the Codex CLI does. It accepts ``.zip``, ``.tar.gz`` and
``.tgz``, and expects the *portable* Agent Plugins layout:

    metaforge/
      plugin.json          <- at the ROOT, not .codex-plugin/
      mcp.json             <- no leading dot, and each server needs a `type`
      skills/<name>/SKILL.md
      .codex-plugin/plugin.json   <- documented compatibility fallback

``integrations/codex/`` is written in the older Codex-specific shape, which
the current CLI rejects outright -- probing ``codex-cli 0.161.0`` with scratch
layouts gives ``missing plugin.json`` for a manifest only under
``.codex-plugin/``. So this transforms rather than copies, and the transform is
the part worth testing.

Why a build step instead of a second committed tree: the bundle is 43 skills.
Committing them twice would mean two copies drifting apart, and the archive is
a build artefact, not source.

    python scripts/package_plugin_bundle.py            # -> dist/
    python scripts/package_plugin_bundle.py --format zip
"""

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "integrations" / "codex"
DIST = REPO_ROOT / "dist"

#: Agent Plugins schemas the portable format declares. Pinned to 1.0.0
#: because that is the revision whose shape was verified against the CLI.
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

#: Transport for a remote MCP server. The portable schema requires `type`;
#: the Codex-specific `.mcp.json` omitted it, and an entry without one is
#: not a valid portable server definition.
MCP_TRANSPORT = "streamable-http"


def to_portable_manifest(codex_manifest: dict) -> dict:
    """The Codex manifest, rewritten into the portable shape.

    Two structural moves, both required rather than cosmetic:

    * ``interface`` becomes ``extensions["com.openai"]["interface"]``. The
      portable schema reserves the root for vendor-neutral fields and puts
      every host-specific presentation detail behind a namespaced extension.
    * ``skills`` and ``mcpServers`` path declarations are dropped. A portable
      package *discovers* skills from ``skills/`` and servers from
      ``mcp.json``; declaring paths that the host does not read is noise that
      reads as configuration.
    """
    dropped = {"interface", "skills", "mcpServers"}
    manifest = {k: v for k, v in codex_manifest.items() if k not in dropped}
    openai: dict = {}
    if interface := codex_manifest.get("interface"):
        openai["interface"] = interface
    out = {"$schema": PLUGIN_SCHEMA, **manifest}
    if openai:
        out["extensions"] = {"com.openai": openai}
    return out


def retarget(mcp: dict, gateway_url: str) -> dict:
    """Point every server at ``gateway_url``, keeping its query string.

    The committed default is ``http://localhost:8765/mcp``, which is right for
    a local harness and unreachable from ChatGPT cloud -- it runs in someone
    else's datacentre. A bundle uploaded with that URL installs and then
    connects to nothing, which presents as the plugin being broken.

    ``?profile=core`` is carried across rather than rebuilt: it caps the served
    tool set, and a host with a hard cap truncates a full list without saying so.
    """
    from urllib.parse import urlsplit, urlunsplit

    target = urlsplit(gateway_url)
    out = {**mcp, "mcpServers": {}}
    for name, entry in (mcp.get("mcpServers") or {}).items():
        existing = urlsplit(entry.get("url", ""))
        query = target.query or existing.query
        out["mcpServers"][name] = {
            **entry,
            "url": urlunsplit((target.scheme, target.netloc, target.path, query, "")),
        }
    return out


def to_portable_mcp(codex_mcp: dict) -> dict:
    """``.mcp.json`` rewritten as ``mcp.json``, with a transport on each server.

    The URL is carried through untouched, ``?profile=core`` included. That
    query caps the served tool set; without it a host loads every tool, and
    one with a hard cap truncates the list without saying so.
    """
    servers = {}
    for name, entry in (codex_mcp.get("mcpServers") or {}).items():
        servers[name] = {"type": MCP_TRANSPORT, **entry}
    return {"$schema": MCP_SCHEMA, "mcpServers": servers}


def build_bundle(
    source: Path = SOURCE,
    out_root: Path | None = None,
    gateway_url: str | None = None,
) -> Path:
    """Lay the portable bundle out on disk and return its root."""
    codex_manifest = json.loads((source / ".codex-plugin" / "plugin.json").read_text())
    codex_mcp = json.loads((source / ".mcp.json").read_text())
    name = codex_manifest["name"]

    staging = (out_root or DIST) / "bundle" / name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    (staging / "plugin.json").write_text(
        json.dumps(to_portable_manifest(codex_manifest), indent=2) + "\n"
    )
    portable_mcp = to_portable_mcp(codex_mcp)
    if gateway_url:
        portable_mcp = retarget(portable_mcp, gateway_url)
    (staging / "mcp.json").write_text(json.dumps(portable_mcp, indent=2) + "\n")

    # Kept alongside the portable manifest, not instead of it: the docs name
    # this as the compatibility fallback for hosts that predate the portable
    # layout, and carrying both costs one small file.
    (staging / ".codex-plugin").mkdir()
    (staging / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(codex_manifest, indent=2) + "\n"
    )

    if (source / "skills").is_dir():
        shutil.copytree(
            source / "skills",
            staging / "skills",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for extra in ("AGENTS.md", "README.md"):
        if (source / extra).is_file():
            shutil.copy2(source / extra, staging / extra)
    return staging


def archive(bundle: Path, fmt: str, out_dir: Path, version: str) -> Path:
    """Write the bundle as one archive. The plugin directory is the root
    entry, because a host unpacks the archive and looks for a single plugin
    folder -- a flat archive of loose files has no plugin to install."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{bundle.name}-plugin-{version}"
    if fmt == "zip":
        path = out_dir / f"{stem}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(bundle.rglob("*")):
                if item.is_file():
                    zf.write(item, Path(bundle.name) / item.relative_to(bundle))
    else:
        path = out_dir / f"{stem}.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            tf.add(bundle, arcname=bundle.name)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["zip", "tar.gz", "both"],
        default="both",
        help="Archive format. ChatGPT accepts all of them; default writes both.",
    )
    parser.add_argument("--out", type=Path, default=DIST, help="Output directory (default: dist/)")
    parser.add_argument(
        "--gateway-url",
        default=None,
        help=(
            "Point the bundled MCP server here instead of the committed default. "
            "Required in practice for ChatGPT cloud, which cannot reach localhost."
        ),
    )
    args = parser.parse_args()

    bundle = build_bundle(out_root=args.out, gateway_url=args.gateway_url)
    version = json.loads((bundle / "plugin.json").read_text())["version"]
    skills = (
        len(list((bundle / "skills").glob("*/SKILL.md"))) if (bundle / "skills").is_dir() else 0
    )

    formats = ["zip", "tar.gz"] if args.format == "both" else [args.format]
    server_url = next(iter(json.loads((bundle / "mcp.json").read_text())["mcpServers"].values()))[
        "url"
    ]
    print(f"bundle: {bundle}  ({skills} skills)")
    print(f"  mcp server: {server_url}")
    for fmt in formats:
        path = archive(bundle, fmt, args.out, version)
        print(f"  {path.name}  {path.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
