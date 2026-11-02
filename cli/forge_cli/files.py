"""`forge upload`, `forge uploads`, `forge download` and `forge link`.

Files in and out of the twin from a terminal. ``forge upload`` sends files
and folders to ``/v1/uploads``; they wait for a person to approve them
(``forge approvals``), or are imported at once with ``--now``, which is the
person's own approval. ``forge download`` saves a work product's file;
``forge link`` prints a signed link someone without a login can use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from cli.forge_cli.client import ForgeClient
from cli.forge_cli.formatters import format_output

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_INVALID = 5
EXIT_AUTH = 6

#: Folders a folder upload never walks into.
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".forge", ".pio"})
_SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def _headers() -> dict[str, str]:
    headers = {"X-MetaForge-Surface": "cli"}
    token = os.environ.get("METAFORGE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fail(code: int, message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _check(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = resp.text
    message = f"{resp.status_code}: {detail}"
    if resp.status_code == 404:
        _fail(EXIT_NOT_FOUND, message)
    if resp.status_code in (401, 403):
        _fail(EXIT_AUTH, f"{message} (set METAFORGE_AUTH_TOKEN)")
    if resp.status_code in (413, 422):
        _fail(EXIT_INVALID, message)
    _fail(EXIT_ERROR, message)


def collect(paths: list[str]) -> list[tuple[str, Path]]:
    """``(name in the upload, file)`` for each file; a folder keeps its own name."""
    out: list[tuple[str, Path]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            out.append((path.name, path))
        elif path.is_dir():
            root = path.resolve()
            for item in sorted(root.rglob("*")):
                rel = item.relative_to(root)
                if (
                    item.is_file()
                    and not set(rel.parts[:-1]) & _SKIP_DIRS
                    and item.name not in _SKIP_FILES
                ):
                    out.append((f"{root.name}/{rel.as_posix()}", item))
        else:
            _fail(EXIT_USAGE, f"{raw} does not exist")
    return out


def _upload(args: argparse.Namespace, client: ForgeClient) -> Any:
    files = collect(args.paths)
    if not files:
        _fail(EXIT_USAGE, "no files to upload")
    data: dict[str, str] = {"approve": "true" if args.now else "false"}
    if args.project:
        data["project_id"] = args.project
    if args.description:
        data["description"] = args.description
    handles = [(name, path.open("rb")) for name, path in files]
    try:
        with httpx.Client(base_url=client.base_url, timeout=600.0) as http:
            resp = http.post(
                "/v1/uploads",
                data=data,
                files=[("files", (name, fh)) for name, fh in handles],
                headers=_headers(),
            )
    finally:
        for _, fh in handles:
            fh.close()
    _check(resp)
    upload = resp.json()
    if args.json:
        print(json.dumps(upload, indent=2))
        return None
    for f in upload["files"]:
        if f.get("error"):
            print(f"  left out  {f['name']}: {f['error']}")
        elif f.get("work_product_id"):
            print(f"  imported  {f['name']} -> {f['wp_type']} {f['work_product_id']}")
        else:
            print(f"  staged    {f['name']} -> {f['wp_type']}")
    if upload["status"] == "pending":
        print(
            f"Waiting for approval: forge approvals approve upload:{upload['id']} "
            "(or the dashboard's Approvals page)"
        )
    return None


def _list(args: argparse.Namespace, client: ForgeClient) -> Any:
    params = {k: v for k, v in (("project_id", args.project), ("status", args.status)) if v}
    with httpx.Client(base_url=client.base_url, timeout=client.timeout) as http:
        resp = http.get("/v1/uploads", params=params, headers=_headers())
    _check(resp)
    rows = [
        {
            "id": u["id"],
            "status": u["status"],
            "source": u["source"],
            "files": len(u["files"]),
            "created_at": u["created_at"],
        }
        for u in resp.json()
    ]
    print(format_output(rows, "json" if args.json else "table"))
    return None


def _download(args: argparse.Namespace, client: ForgeClient) -> Any:
    wp = quote(args.work_product_id, safe="")
    url = (
        f"/v1/twin/nodes/{wp}/files/{quote(args.path)}"
        if args.path
        else f"/v1/twin/nodes/{wp}/file?download=true"
    )
    with httpx.Client(base_url=client.base_url, timeout=600.0) as http:
        resp = http.get(url, headers=_headers())
    _check(resp)
    name = args.path.rsplit("/", 1)[-1] if args.path else _filename(resp) or args.work_product_id
    dest = Path(args.output) if args.output else Path(name)
    if dest.is_dir():
        dest = dest / name
    if dest.exists() and not args.force:
        _fail(EXIT_USAGE, f"{dest} exists; pass --force to replace it")
    dest.write_bytes(resp.content)
    print(f"Saved {dest} ({len(resp.content)} bytes)")
    return None


def _filename(resp: httpx.Response) -> str | None:
    disposition = resp.headers.get("content-disposition", "")
    if "filename=" not in disposition:
        return None
    raw = disposition.split("filename=", 1)[1].strip().strip('"')
    return Path(raw).name or None


def _link(args: argparse.Namespace, client: ForgeClient) -> Any:
    body: dict[str, Any] = {"path": args.path or ""}
    if args.lifetime:
        body["lifetime_s"] = args.lifetime
    with httpx.Client(base_url=client.base_url, timeout=client.timeout) as http:
        resp = http.post(
            f"/v1/files/{quote(args.work_product_id, safe='')}/link",
            json=body,
            headers=_headers(),
        )
    _check(resp)
    link = resp.json()
    url = link.get("url") or f"{client.base_url.rstrip('/')}{link['path']}"
    if args.json:
        print(json.dumps({**link, "url": url}, indent=2))
    else:
        print(url)
        print(f"Works until {link['expires_at']} for anyone who has it.", file=sys.stderr)
    return None


def handle_files(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge upload|uploads|download|link`; each prints its own output."""
    command = args.command
    if command == "upload":
        return _upload(args, client)
    if command == "uploads":
        return _list(args, client)
    if command == "download":
        return _download(args, client)
    if command == "link":
        return _link(args, client)
    _fail(EXIT_USAGE, f"unknown command {command}")
    return None


def register_subparsers(subparsers: Any) -> None:
    """Register ``upload``, ``uploads``, ``download`` and ``link``."""
    up = subparsers.add_parser(
        "upload", help="Send files or folders into the twin (they wait for approval)"
    )
    up.add_argument("paths", nargs="+", help="Files and folders")
    up.add_argument("--project", default=None, help="Project id")
    up.add_argument("--description", default="", help="What the files are")
    up.add_argument("--now", action="store_true", help="Import at once; you are the approval")
    up.add_argument("--json", action="store_true", help="JSON output")

    ls = subparsers.add_parser("uploads", help="List uploads and their state")
    ls.add_argument("--project", default=None, help="Project id")
    ls.add_argument("--status", default=None, help="open, pending, approved, rejected, expired")
    ls.add_argument("--json", action="store_true", help="JSON output")

    dl = subparsers.add_parser("download", help="Save a work product's file")
    dl.add_argument("work_product_id", help="Work product id")
    dl.add_argument("--path", default=None, help="A named file (e.g. a KiCad project file)")
    dl.add_argument("-o", "--output", default=None, help="File or folder to save to")
    dl.add_argument("--force", action="store_true", help="Replace an existing file")

    ln = subparsers.add_parser("link", help="Print a signed download link for a file")
    ln.add_argument("work_product_id", help="Work product id")
    ln.add_argument("--path", default=None, help="A named file")
    ln.add_argument("--lifetime", type=int, default=None, help="Seconds the link works")
    ln.add_argument("--json", action="store_true", help="JSON output")
