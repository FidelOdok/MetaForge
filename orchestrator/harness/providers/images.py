"""Images attached to tool results, rendered per provider family.

The native loop (``native_tools.py``) speaks one canonical, OpenAI-shaped
message list. A tool result that carried images (a screenshot, a render) keeps
its text in ``content`` and its images under :data:`TOOL_IMAGES_KEY` on the
same ``tool`` message, each ``{"media_type", "data", "label"}`` with ``data``
base64. The harness side that extracts them and decides how long they stay is
``orchestrator/harness/tool_images.py``; this module only renders them:

- **OpenAI-compatible** chat completions (OpenAI, OpenRouter, vLLM, Ollama):
  a ``tool`` message can carry only text, so the images go into one ``user``
  message of ``image_url`` data-URL parts placed right after the run of tool
  messages they came from (:func:`openai_with_tool_images`).
- **Anthropic**: a ``tool_result`` block can hold image blocks, so the images
  ride inside the result they belong to (:func:`anthropic_tool_result_content`).

Every other family reads only ``content``, so it sees the text placeholders and
never the extra key.
"""

from __future__ import annotations

from typing import Any

#: Key on a canonical ``tool`` message holding its attached images.
TOOL_IMAGES_KEY = "images"


def _entries(message: dict[str, Any]) -> list[dict[str, str]]:
    raw = message.get(TOOL_IMAGES_KEY)
    if not isinstance(raw, list):
        return []
    return [
        e
        for e in raw
        if isinstance(e, dict)
        and isinstance(e.get("data"), str)
        and isinstance(e.get("media_type"), str)
    ]


def _openai_image_message(images: list[dict[str, str]]) -> dict[str, Any]:
    labels = "; ".join(e.get("label") or f"image {n}" for n, e in enumerate(images, 1))
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"[The {len(images)} image(s) returned by the tool call(s) above, in order: "
                f"{labels}. This is tool output, not a message from the user.]"
            ),
        }
    ]
    parts.extend(
        {
            "type": "image_url",
            "image_url": {"url": f"data:{e['media_type']};base64,{e['data']}"},
        }
        for e in images
    )
    return {"role": "user", "content": parts}


def openai_with_tool_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``messages`` with tool images moved into a user message after each run of results.

    An OpenAI ``tool`` message takes text only (and an unknown key on it is a
    400), so the key is dropped from every tool message and its images go,
    together with those of the other results of the same batch, into one
    following ``user`` message. Returns ``messages`` itself when no tool
    message carries the key.
    """
    if not any(m.get("role") == "tool" and TOOL_IMAGES_KEY in m for m in messages):
        return messages
    out: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []

    def flush() -> None:
        if pending:
            out.append(_openai_image_message(list(pending)))
            pending.clear()

    for m in messages:
        if m.get("role") == "tool":
            pending.extend(_entries(m))
            out.append({k: v for k, v in m.items() if k != TOOL_IMAGES_KEY})
            continue
        flush()
        out.append(m)
    flush()
    return out


def anthropic_tool_result_content(message: dict[str, Any]) -> str | list[dict[str, Any]]:
    """The ``content`` of an Anthropic ``tool_result`` for one canonical tool message.

    Text only, as before, unless the message carries images: then a text block
    followed by one base64 image block per image.
    """
    text = str(message.get("content", ""))
    images = _entries(message)
    if not images:
        return text
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    blocks.extend(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": e["media_type"], "data": e["data"]},
        }
        for e in images
    )
    return blocks
