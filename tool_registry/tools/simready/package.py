"""Fetching a SimReady package and checking every byte of it (FORGE-711).

A package is a folder in a Hugging Face dataset, addressed by the URL of its
``com.nvidia.simready.packaging.json`` at a fixed commit. That manifest names
the package's licence and the digest of its bill of materials, and the bill
of materials lists every file with its size and sha256. Nothing is written
until it has been checked against those digests, so what lands on the
workspace is exactly what the publisher listed.

Only the OpenUSD layers are fetched by default. A typical asset's layers are
~60 MB; its textures and source assets can add more than a gigabyte, and the
preview, the mass and the physics need none of them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

PACKAGING_FILE = "com.nvidia.simready.packaging.json"
ROOT_USDS_FILE = ".metadata/com.nvidia.simready.root_usds.json"
LICENCE_FILE = ".metadata/LICENSE.txt"

_PACKAGE_URL = re.compile(
    r"^https://huggingface\.co/datasets/(?P<namespace>[A-Za-z0-9][\w.-]*)/(?P<dataset>[\w.-]+)"
    r"/resolve/(?P<commit>[^/]+)/(?P<asset>[^?#]+)/" + re.escape(PACKAGING_FILE) + r"$"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_LAYER_SUFFIXES = (".usd", ".usda", ".usdc")


class PackageError(ValueError):
    """The package cannot be used as asked; the message says why."""


class FetchError(PackageError):
    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        self.url = url
        self.status = status
        super().__init__(f"could not fetch {url}: {status or detail}")


class Fetcher(Protocol):
    async def get(self, url: str) -> bytes: ...


class HttpFetcher:
    """httpx GET with redirects followed (Hugging Face serves LFS files from a CDN)."""

    def __init__(self, timeout_s: float = 60.0) -> None:
        self._timeout = timeout_s

    async def get(self, url: str) -> bytes:
        import httpx

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=self._timeout) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(url, None, str(exc)) from exc
        if response.status_code != 200:
            raise FetchError(url, response.status_code)
        return response.content


@dataclass(frozen=True)
class PackageRef:
    namespace: str
    dataset: str
    commit: str
    asset: str

    @property
    def dataset_id(self) -> str:
        return f"{self.namespace}/{self.dataset}"

    @property
    def url(self) -> str:
        return self.file_url(PACKAGING_FILE)

    @property
    def browse_url(self) -> str:
        return f"https://huggingface.co/datasets/{self.dataset_id}/tree/{self.commit}/{self.asset}"

    def file_url(self, relative: str) -> str:
        return (
            f"https://huggingface.co/datasets/{self.dataset_id}/resolve/"
            f"{self.commit}/{self.asset}/{relative}"
        )


def parse_package_url(url: str) -> PackageRef:
    """A package URL pinned to a commit; ``main`` or a tag is refused.

    A branch name resolves to whatever the publisher pushes next, so the same
    URL could later name different files under the same digest check. The
    SimReady Central index only lists commit-pinned URLs for the same reason.
    """
    match = _PACKAGE_URL.match((url or "").strip())
    if not match:
        raise PackageError(
            "package_url must be https://huggingface.co/datasets/<namespace>/<dataset>/"
            f"resolve/<commit>/<asset>/{PACKAGING_FILE}"
        )
    commit = match["commit"]
    if not _COMMIT.match(commit):
        raise PackageError(
            f"package_url is pinned to '{commit}', not a commit; use the 40-character "
            "commit the SimReady Central index lists (simready.search returns it)"
        )
    return PackageRef(match["namespace"], match["dataset"], commit, match["asset"])


@dataclass
class Manifest:
    ref: PackageRef
    package_id: str
    licence: str | None
    package_sha256: str
    items: list[dict[str, Any]]
    root_usd: str
    licence_text: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(int(i.get("size") or 0) for i in self.items)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data)
    except ValueError as exc:
        raise PackageError(f"{what} is not JSON: {exc}") from exc


async def read_manifest(ref: PackageRef, fetcher: Fetcher) -> Manifest:
    """The package's manifest, its verified bill of materials and its root layer."""
    packaging = _json(await fetcher.get(ref.url), PACKAGING_FILE)
    bom_entry = next(
        (m for m in packaging.get("metadata") or [] if str(m.get("name", "")).endswith("bom.json")),
        None,
    )
    if bom_entry is None:
        raise PackageError("the package manifest lists no bill of materials")
    bom_bytes = await fetcher.get(ref.file_url(f".metadata/{bom_entry['name']}"))
    expected = str((bom_entry.get("hash") or {}).get("sha256") or "")
    if _sha256(bom_bytes) != expected:
        raise PackageError("the bill of materials does not match the digest in the manifest")
    items = _json(bom_bytes, "the bill of materials").get("items") or []
    if not items:
        raise PackageError("the bill of materials lists no files")

    roots = _json(await fetcher.get(ref.file_url(ROOT_USDS_FILE)), ROOT_USDS_FILE)
    entries = roots.get("entries") or []
    listed = {i["relative_path"] for i in items}
    root = next((e for e in entries if e in listed), None)
    if root is None:
        raise PackageError("the package names no root USD layer that its file list contains")

    try:
        licence_text: str | None = (await fetcher.get(ref.file_url(LICENCE_FILE))).decode(
            "utf-8", "replace"
        )
    except FetchError as exc:
        if exc.status != 404:
            raise
        licence_text = None

    package_sha = str((packaging.get("package_hash") or {}).get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", package_sha):
        package_sha = _sha256(bom_bytes)
    return Manifest(
        ref=ref,
        package_id=str(packaging.get("package_id") or f"{ref.dataset_id}/{ref.asset}"),
        licence=packaging.get("license"),
        package_sha256=package_sha,
        items=items,
        root_usd=root,
        licence_text=licence_text,
        raw=packaging,
    )


def select_items(manifest: Manifest, *, include_textures: bool = False) -> list[dict[str, Any]]:
    """The files an import needs: every USD layer, plus textures when asked.

    Thumbnails and the ``source_assets`` folder (the publisher's own working
    files) are never fetched.
    """
    chosen = []
    for item in manifest.items:
        path = str(item["relative_path"])
        parts = path.split("/")
        if any(p.startswith(".") for p in parts[:-1]) or "source_assets" in parts:
            continue
        if path.lower().endswith(_LAYER_SUFFIXES) or (include_textures and "Textures" in parts):
            chosen.append(item)
    return chosen


def _safe_target(dest: Path, relative: str) -> Path:
    target = (dest / relative).resolve()
    if dest.resolve() not in target.parents:
        raise PackageError(f"the package lists a path outside itself: {relative}")
    return target


async def download(
    manifest: Manifest,
    items: list[dict[str, Any]],
    dest: Path,
    fetcher: Fetcher,
    *,
    max_bytes: int,
) -> list[str]:
    """Fetch ``items`` into ``dest``, refusing any file whose sha256 differs."""
    total = sum(int(i.get("size") or 0) for i in items)
    if total > max_bytes:
        raise PackageError(
            f"the selected files are {total / 1e6:.0f} MB, over the {max_bytes / 1e6:.0f} MB "
            "limit (raise max_package_mb, or leave textures out)"
        )
    written = []
    for item in items:
        relative = str(item["relative_path"])
        target = _safe_target(dest, relative)
        data = await manifest_fetch(manifest, relative, fetcher)
        expected = str((item.get("hash") or {}).get("sha256") or "")
        if _sha256(data) != expected:
            raise PackageError(f"{relative} does not match its sha256 in the bill of materials")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(relative)
    logger.info(
        "simready_package_downloaded",
        package_id=manifest.package_id,
        files=len(written),
        bytes=total,
    )
    return written


async def manifest_fetch(manifest: Manifest, relative: str, fetcher: Fetcher) -> bytes:
    return await fetcher.get(manifest.ref.file_url(relative))
