"""Images in tool results: out of the text, in front of the model (2026-11-14).

A tool such as ``twin.imagine_look`` returns screenshots as base64 inside its
JSON (``{"images": [{"view": "first look", "png_base64": "..."}]}``). Rendered
as text, two screenshots are ~130k characters of base64: the result was turned
into a handle (``result_handles.py``), the model read a summary of a string it
could never see as a picture, and told the user it could not judge the look.

This module takes the images out of a tool result before it is rendered for
the model. Each base64 value is replaced in place by a short placeholder
(``"[image: first look, attached ...]"``), so the remaining text (status,
sizes, parts, errors, measurements) is small enough to stay inline, and the
images go on the canonical ``tool`` message under
:data:`~orchestrator.harness.providers.images.TOOL_IMAGES_KEY`, which each
provider adapter renders as real image input (``providers/images.py``).

Recognised shapes, anywhere in a dict/list result (or a JSON string of one):

- a key ``png_base64`` / ``jpeg_base64`` / ``jpg_base64`` / ``webp_base64`` /
  ``gif_base64`` / ``image_base64``, its label taken from a sibling ``view`` /
  ``label`` / ``name`` / ``title`` / ``caption`` and, for ``image_base64``, its
  type from a sibling ``media_type`` / ``mime_type`` / ``mimeType``;
- an MCP image content block ``{"type": "image", "data": ..., "mimeType": ...}``;
- either of those holding a ``data:image/...;base64,`` URL instead of bare base64.

Limits: at most :data:`MAX_IMAGES_PER_STEP` images are attached per batch of
tool calls and an image over :data:`MAX_IMAGE_BYTES` is not attached; either
way its placeholder says why. The image type is read from the bytes, and only
PNG, JPEG, GIF and WebP are attached. ``METAFORGE_TOOL_RESULT_IMAGES=off``
stops attaching (for a text-only model) but still takes the base64 out.

Images are context-expensive, so they are shown once: :func:`expire_tool_images`
drops them from every tool message older than the latest assistant turn,
leaving the placeholder text. The full result, base64 included, stays on the
recorded trace step.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from typing import Any

import structlog

from orchestrator.harness.providers.images import TOOL_IMAGES_KEY

logger = structlog.get_logger(__name__)

#: Env var: ``off`` (or ``0``/``false``/``no``) stops attaching images.
IMAGES_ENV = "METAFORGE_TOOL_RESULT_IMAGES"
#: Most images attached for one batch of tool calls (one model step).
MAX_IMAGES_PER_STEP = 4
#: An image larger than this (decoded) is not attached; Anthropic's own cap is 5 MB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_KEY_TYPES: dict[str, str | None] = {
    "png_base64": "image/png",
    "jpeg_base64": "image/jpeg",
    "jpg_base64": "image/jpeg",
    "webp_base64": "image/webp",
    "gif_base64": "image/gif",
    "image_base64": None,
}
_LABEL_KEYS = ("view", "label", "name", "title", "caption")
_MEDIA_KEYS = ("media_type", "mime_type", "mimeType", "content_type")
_SUPPORTED = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_DATA_URL_PREFIX = "data:image/"


def images_enabled() -> bool:
    raw = os.environ.get(IMAGES_ENV, "").strip().lower()
    return raw not in {"off", "0", "false", "no"}


@dataclass(frozen=True)
class ToolImage:
    """One image taken out of a tool result."""

    media_type: str
    data: str
    label: str

    def as_entry(self) -> dict[str, str]:
        return {"media_type": self.media_type, "data": self.data, "label": self.label}


@dataclass
class ImageBudget:
    """What one batch of tool calls may still attach."""

    remaining: int = MAX_IMAGES_PER_STEP
    max_bytes: int = MAX_IMAGE_BYTES
    enabled: bool = field(default_factory=images_enabled)


def _sniff(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _label(container: dict[str, Any], fallback: str) -> str:
    for key in _LABEL_KEYS:
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return fallback


def _declared_type(container: dict[str, Any]) -> str | None:
    for key in _MEDIA_KEYS:
        value = container.get(key)
        if isinstance(value, str) and value.startswith("image/"):
            return value.lower()
    return None


class _Extractor:
    def __init__(self, budget: ImageBudget) -> None:
        self.budget = budget
        self.images: list[ToolImage] = []
        self.omitted = 0
        self.counter = 0

    def take(self, value: str, label: str, declared: str | None) -> str:
        """Placeholder for one base64 value, attaching the image when allowed."""
        self.counter += 1
        data = value.strip()
        if data.startswith(_DATA_URL_PREFIX) and ";base64," in data:
            head, _, data = data.partition(";base64,")
            declared = declared or head[len("data:") :].lower()
        if not self.budget.enabled:
            self.omitted += 1
            return f"[image: {label}, not shown: image attachments are turned off]"
        approx = len(data) * 3 // 4
        if approx > self.budget.max_bytes:
            self.omitted += 1
            return (
                f"[image: {label}, not attached: {approx / 1e6:.1f} MB is over the "
                f"{self.budget.max_bytes / 1e6:.0f} MB limit]"
            )
        if self.budget.remaining <= 0:
            self.omitted += 1
            return (
                f"[image: {label}, not attached: only {MAX_IMAGES_PER_STEP} images are "
                "attached per step; ask for fewer views to see this one]"
            )
        try:
            raw = base64.b64decode("".join(data.split()), validate=True)
        except (binascii.Error, ValueError):
            self.omitted += 1
            return f"[image: {label}, not attached: not valid base64]"
        media_type = _sniff(raw) or declared
        if media_type not in _SUPPORTED:
            self.omitted += 1
            return f"[image: {label}, not attached: unsupported image type {media_type!r}]"
        self.budget.remaining -= 1
        encoded = base64.b64encode(raw).decode("ascii")
        self.images.append(ToolImage(media_type=media_type, data=encoded, label=label))
        return (
            f"[image: {label}, attached as image {len(self.images)} of this result; "
            "you see it in this step only, so note what you observe]"
        )

    def walk(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        is_block = value.get("type") == "image" and isinstance(value.get("data"), str)
        for key, item in value.items():
            if isinstance(item, str) and key in _KEY_TYPES:
                fallback = f"image {self.counter + 1}"
                out[key] = self.take(
                    item, _label(value, fallback), _KEY_TYPES[key] or _declared_type(value)
                )
            elif is_block and key == "data":
                out[key] = self.take(
                    str(item), _label(value, f"image {self.counter + 1}"), _declared_type(value)
                )
            else:
                out[key] = self.walk(item)
        return out


def _maybe_json(value: str) -> Any | None:
    text = value.lstrip()
    if not text.startswith(("{", "[")) or ("base64" not in value and '"image"' not in value):
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def extract_images(value: Any, budget: ImageBudget) -> tuple[Any, list[ToolImage]]:
    """``value`` with every image replaced by a placeholder, and the images attached.

    Returns ``value`` itself, and no images, when it holds none, so a result
    without images renders exactly as it did before.
    """
    target = value
    if isinstance(value, str):
        parsed = _maybe_json(value)
        if parsed is None:
            return value, []
        target = parsed
    if not isinstance(target, (dict, list)):
        return value, []
    extractor = _Extractor(budget)
    stripped = extractor.walk(target)
    if extractor.counter == 0:
        return value, []
    logger.info(
        "tool_result_images_extracted",
        found=extractor.counter,
        attached=len(extractor.images),
        omitted=extractor.omitted,
        attached_bytes=sum(len(i.data) * 3 // 4 for i in extractor.images),
    )
    return stripped, extractor.images


def expire_tool_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``messages`` with images dropped from every tool result the model has already seen.

    A tool message is "seen" once an assistant message follows it, so the
    images of a batch go to exactly the next model call. Returns ``messages``
    itself when nothing is dropped; never changes it in place.
    """
    last_assistant = max(
        (i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1
    )
    stale = [
        i
        for i, m in enumerate(messages[:last_assistant])
        if m.get("role") == "tool" and TOOL_IMAGES_KEY in m
    ]
    if not stale:
        return messages
    out = list(messages)
    for i in stale:
        out[i] = {k: v for k, v in messages[i].items() if k != TOOL_IMAGES_KEY}
    logger.info("tool_result_images_expired", results=len(stale))
    return out
