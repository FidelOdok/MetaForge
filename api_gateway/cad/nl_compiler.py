"""Natural-language → assembly-spec compiler (MET-10).

Closes the text→3D gap: the reliable geometry path (``POST /v1/cad/assembly``)
only takes a hand-authored JSON spec, while the natural-language path (the chat
agent) is LLM-planned and less deterministic. This module bridges them — an LLM
translates a plain-English description into the *same* declarative spec, which
then runs through the deterministic builder. Natural language in, a reviewable
spec in the middle, manufacturable geometry out.

The LLM call itself is injected as a ``translate`` callable so everything here is
pure and unit-testable without a live model.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

Translate = Callable[[str], Awaitable[str]]

# The spec contract, handed to the model verbatim. Kept terse but complete: the
# kinds, the per-kind parameters, the optional fields, and the one footgun
# (edge-treatment radius vs. thickness) the deterministic validator also checks.
_SPEC_CONTRACT = """\
You translate a hardware part/assembly description into a MetaForge CAD assembly \
spec. Output ONLY a single JSON object, no prose, no markdown fences.

Schema:
{
  "name": "<assembly name>",
  "parts": [
    {
      "name": "<part name>",              // required, meaningful (becomes the STEP product name)
      "kind": "box|cylinder|cone|sphere", // required
      "parameters": { ... },              // dimensions in MILLIMETRES, per kind:
                                          //   box:      width, length, height
                                          //   cylinder: radius, height
                                          //   cone:     radius1, radius2, height
                                          //   sphere:   radius
      "position": [x, y, z],              // optional placement in mm (default origin)
      "holes": [ { "x": <mm>, "y": <mm>, "diameter": <mm>, "depth": <mm optional> } ],
      "fillet": <mm optional>,            // round all edges; MUST be < half the thinnest dimension
      "chamfer": <mm optional>            // bevel all edges; use fillet OR chamfer, not both
    }
  ]
}

Rules:
- Every dimension is in millimetres. Infer sensible values when the user is vague.
- Give every part a descriptive name; never "Part 1".
- Holes are in the part's own local frame (before it is positioned).
- A fillet/chamfer radius must be strictly less than half the part's smallest
  dimension, or the geometry fails — keep them modest.
- Prefer the fewest primitives that capture the intent.

Example — "a 100x100x6 base plate with a 40mm diameter, 45mm tall boss on top":
{
  "name": "Base with boss",
  "parts": [
    {"name": "Base plate", "kind": "box",
     "parameters": {"width": 100, "length": 100, "height": 6}},
    {"name": "Boss", "kind": "cylinder",
     "parameters": {"radius": 20, "height": 45}, "position": [50, 50, 6]}
  ]
}
"""


def build_prompt(description: str, name: str | None = None) -> str:
    """The full translation prompt: the contract + the user's description."""
    ask = _SPEC_CONTRACT + "\n\nDescription:\n" + description.strip()
    if name:
        ask += f'\n\nUse exactly this assembly name: "{name}".'
    return ask


def extract_spec(text: str) -> dict[str, Any]:
    """Parse the model's reply into a spec dict, tolerating fences/prose.

    Raises ``ValueError`` with a clear message when no valid spec object can be
    recovered — the caller surfaces this as a 400 rather than a mystery 502.
    """
    if not text or not text.strip():
        raise ValueError("the model returned an empty response")

    raw = text.strip()
    # Strip a ```json … ``` (or plain ```) fence if present.
    if raw.startswith("```"):
        body = raw[3:]
        if body[:4].lower() == "json":
            body = body[4:]
        end = body.rfind("```")
        raw = (body[:end] if end != -1 else body).strip()

    # Fall back to the outermost { … } span if there's still surrounding prose.
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in the model response")
        raw = raw[start : end + 1]

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model response was not valid JSON: {exc}") from exc

    if not isinstance(spec, dict):
        raise ValueError("the spec must be a JSON object")
    if "name" not in spec or not str(spec.get("name") or "").strip():
        raise ValueError("the spec is missing a 'name'")
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("the spec must have a non-empty 'parts' list")
    return spec


async def compile_spec(
    description: str,
    *,
    translate: Translate,
    name: str | None = None,
) -> dict[str, Any]:
    """Translate *description* into an assembly spec via *translate* (the LLM)."""
    prompt = build_prompt(description, name)
    text = await translate(prompt)
    spec = extract_spec(text)
    if name:
        spec["name"] = name  # honour an explicit override regardless of the model
    logger.info("cad_nl_compiled", parts=len(spec.get("parts", [])), name=spec.get("name"))
    return spec
