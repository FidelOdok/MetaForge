"""``image.fetch``: a component photo from a URL or a distributor part number (FORGE-923, R1).

A client cannot load a distributor's product photo itself (Mouser and Digi-Key pages
refuse readers), so a photo-based model needed a person to supply the photo. This
fetches the image server-side under the same network limits as ``web.fetch``:

* public hosts only, checked on the first URL and every redirect;
* the bytes must be an image of the type the server declared (JPEG, PNG, WebP or
  GIF, sniffed from the bytes, never trusted from the header); SVG is refused,
  since it is a document that can carry script, not a photo;
* at most :data:`MAX_IMAGE_BYTES`, and at most :data:`MAX_PIXELS` pixels, so a
  decompression bomb is refused before it is decoded.

Images are kept by checksum (``image:<sha256>``) under the storage root, so a
reading can be repeated on exactly the same bytes, and a URL fetched before is
answered from the cache. A placeholder ("no image available") is reported as no
photo, never returned as one.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.web.refusals import SiteUnreachableError, explain
from tool_registry.tools.web.safety import (
    MAX_REDIRECTS,
    UnsafeUrlError,
    assert_public_host,
    validate_url,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.web.image_fetch")

MAX_IMAGE_BYTES = 10_000_000
#: A 50-megapixel ceiling: far above any product photo, far below a decompression bomb.
MAX_PIXELS = 50_000_000
#: A photo smaller than this on its longest side is a thumbnail or a placeholder icon.
MIN_PHOTO_SIDE_PX = 48
_TIMEOUT_S = 20.0

_USER_AGENT = "MetaForge/0.1 (+https://github.com/FidelOdok/MetaForge)"
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "image/avif,image/webp,image/*;q=0.9,*/*;q=0.5"}

#: Words a placeholder image's address carries on distributor sites.
_PLACEHOLDER_URL = re.compile(
    r"no[-_ ]?(image|photo|picture|pic|img|goods)|image[-_ ]?not[-_ ]?available|placeholder|"
    r"coming[-_ ]?soon|default[-_ ]?(image|product)",
    re.IGNORECASE,
)


class ImageFetchError(ValueError):
    """The image could not be fetched or is not an acceptable image; the message says why."""


@dataclass
class FetchedImage:
    image_ref: str
    sha256: str
    mime: str
    width_px: int
    height_px: int
    source_url: str
    retrieved_at: str
    bytes_len: int
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "image_ref": self.image_ref,
            "sha256": self.sha256,
            "mime": self.mime,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "bytes": self.bytes_len,
            "cached": self.cached,
        }


def sniff_mime(data: bytes) -> str | None:
    """The image type the bytes themselves say, or ``None`` for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _declared_ok(declared: str, sniffed: str) -> bool:
    """A declared type agrees with the bytes when it names the same image type, or none."""
    base = declared.split(";", 1)[0].strip().lower()
    if base in ("", "application/octet-stream", "binary/octet-stream"):
        return True
    if base == "image/jpg":
        base = "image/jpeg"
    return base == sniffed


def placeholder_reason(url: str, image: Any) -> str | None:
    """Why ``image`` looks like a placeholder rather than a photo, or ``None``."""
    if _PLACEHOLDER_URL.search(url):
        return "its address names a placeholder image"
    width, height = image.size
    if max(width, height) < MIN_PHOTO_SIDE_PX:
        return f"it is only {width} x {height} pixels"
    try:
        from PIL import ImageStat

        grey = image.convert("L")
        grey.thumbnail((128, 128))
        spread = ImageStat.Stat(grey).stddev[0]
    except Exception:  # noqa: BLE001 - a statistic that fails decides nothing
        return None
    if spread < 4.0:
        return "it is a single flat colour"
    return None


class ImageStore:
    """Images by checksum under ``<storage root>/images``, with a URL index."""

    def __init__(self, root: str | Path | None = None) -> None:
        base = root or os.environ.get("METAFORGE_IMAGE_ROOT")
        if base is None:
            storage = os.environ.get("METAFORGE_STORAGE_ROOT") or os.path.join(
                os.path.expanduser("~"), ".metaforge", "work_products"
            )
            base = os.path.join(storage, "images")
        self.root = Path(base)

    def _path(self, sha: str, mime: str) -> Path:
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
        return self.root / f"{sha}.{ext.get(mime, 'bin')}"

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _index(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self._index_path().read_text()))
        except (OSError, ValueError):
            return {}

    def lookup_url(self, url: str) -> FetchedImage | None:
        entry = self._index().get(url)
        if not isinstance(entry, dict):
            return None
        if not self._path(entry["sha256"], entry["mime"]).exists():
            return None
        return FetchedImage(**{**entry, "cached": True})

    def put(self, data: bytes, image: FetchedImage, *, key: str | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(image.sha256, image.mime)
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        index = self._index()
        record = image.as_dict()
        for field in ("status", "bytes", "cached"):
            record.pop(field, None)
        record["bytes_len"] = image.bytes_len
        index[key or image.source_url] = record
        tmp_index = self._index_path().with_suffix(".tmp")
        tmp_index.write_text(json.dumps(index, indent=1, sort_keys=True))
        tmp_index.replace(self._index_path())

    def read(self, image_ref: str) -> tuple[bytes, str]:
        """``(bytes, mime)`` for an ``image:<sha256>`` ref; raises :class:`ImageFetchError`."""
        sha = image_ref.removeprefix("image:")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ImageFetchError(f"{image_ref!r} is not an image ref (image:<sha256>)")
        for path in self.root.glob(f"{sha}.*"):
            if path.suffix == ".tmp":
                continue
            data = path.read_bytes()
            return data, sniff_mime(data) or "application/octet-stream"
        raise ImageFetchError(f"no stored image {image_ref}; fetch it again with image.fetch")


async def _download(url: str, client: httpx.AsyncClient) -> tuple[str, str, bytes]:
    """``(final url, declared type, bytes)``, every hop checked, at most the byte cap."""
    current = validate_url(url)
    for hop in range(MAX_REDIRECTS + 1):
        await assert_public_host(current)
        try:
            async with client.stream(
                "GET", current, headers=_HEADERS, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("redirect response carried no Location header")
                    if hop >= MAX_REDIRECTS:
                        raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS})")
                    current = validate_url(str(response.url.join(location)))
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-type", "")
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > MAX_IMAGE_BYTES:
                    raise ImageFetchError(
                        f"the image is {int(length) / 1e6:.1f} MB, over the "
                        f"{MAX_IMAGE_BYTES / 1e6:.0f} MB limit"
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ImageFetchError(
                            f"the image is over the {MAX_IMAGE_BYTES / 1e6:.0f} MB limit"
                        )
                    chunks.append(chunk)
                return current, declared, b"".join(chunks)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            raise SiteUnreachableError(explain(current, exc)) from exc
    raise UnsafeUrlError(f"too many redirects (>{MAX_REDIRECTS})")


def _decode(data: bytes) -> Any:
    """The image, opened and verified, or :class:`ImageFetchError`."""
    from PIL import Image

    try:
        probe = Image.open(io.BytesIO(data))
        width, height = probe.size
        if width * height > MAX_PIXELS:
            raise ImageFetchError(
                f"the image is {width} x {height} pixels, over the {MAX_PIXELS:,} pixel limit"
            )
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except ImageFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decode failure is a malformed image
        raise ImageFetchError(f"the bytes are not a readable image: {exc}") from exc
    return image


async def fetch_image(
    url: str,
    *,
    store: ImageStore | None = None,
    client: httpx.AsyncClient | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch, check, store and describe one image; ``status`` is ``ok`` or ``no_photo``."""
    store = store or ImageStore()
    with tracer.start_as_current_span("image.fetch") as span:
        checked = validate_url(url)
        if not refresh:
            cached = store.lookup_url(checked)
            if cached is not None:
                span.set_attribute("image.cached", True)
                return cached.as_dict()
        owns = client is None
        http = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
        started = time.monotonic()
        try:
            final, declared, data = await _download(checked, http)
        finally:
            if owns:
                await http.aclose()
        sniffed = sniff_mime(data)
        if sniffed is None:
            kind = declared.split(";", 1)[0] or "unknown"
            if b"<svg" in data[:2048].lower():
                raise ImageFetchError("SVG is not accepted: it is a document, not a photo")
            raise ImageFetchError(
                f"the address returned {kind}, not a JPEG, PNG, WebP or GIF image"
            )
        if not _declared_ok(declared, sniffed):
            raise ImageFetchError(
                f"the server declared {declared.split(';', 1)[0]} but the bytes are {sniffed}"
            )
        image = _decode(data)
        why = placeholder_reason(final, image)
        if why is not None:
            logger.info("image_fetch_placeholder", url=final[:200], reason=why)
            span.set_attribute("image.placeholder", True)
            return {"status": "no_photo", "source_url": final, "reason": why}
        sha = hashlib.sha256(data).hexdigest()
        fetched = FetchedImage(
            image_ref=f"image:{sha}",
            sha256=sha,
            mime=sniffed,
            width_px=image.size[0],
            height_px=image.size[1],
            source_url=final,
            retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            bytes_len=len(data),
        )
        store.put(data, fetched)
        if final != checked:
            # The address asked for answers from the cache next time too.
            store.put(data, fetched, key=checked)
        span.set_attribute("image.bytes", len(data))
        logger.info(
            "image_fetched",
            url=final[:200],
            mime=sniffed,
            width=fetched.width_px,
            height=fetched.height_px,
            bytes=len(data),
            seconds=round(time.monotonic() - started, 2),
        )
        return fetched.as_dict()
