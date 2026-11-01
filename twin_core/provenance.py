"""Where an engineering value came from (FORGE-586).

A design value (a table height, a tube section, a top thickness) is only as
good as its source. A number with no source and a number from a standard look
the same in a model and in a gate, so the twin records the source alongside
the value and refuses a value whose source it cannot identify.

The source kinds, from strongest to weakest:

``user``
    The person said it. A choice only they can make (budget, quantity, look)
    can only come from here.
``project_document``
    A document ingested into the project (a PRD, a drawing, a datasheet),
    found with ``knowledge.search`` / ``knowledge.extract``.
``standard``
    A published standard or reference (EN 12521, an ergonomics handbook).
``past_work``
    An earlier project's recorded decision or result in the twin or memory.
``component``
    A real purchasable part or stock size (``component.search_parametric``,
    distributors, step.parts).
``analysis``
    Worked out from other values by calculation or simulation (FEA, an
    optimiser, a design loop).
``measurement``
    Measured from an existing object or model (an imported STEP file).
``assumption``
    Nothing above applies; a stated default the person must review.

Each kind names the fields that let a reviewer find the source again. Pure
data and checks: no I/O, so the gateway, the MCP adapter and tests share it.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SOURCE_FIELDS",
    "SOURCE_KINDS",
    "describe_source",
    "source_errors",
]

#: The kinds, strongest first. The order is the order an agent should try.
SOURCE_KINDS: tuple[str, ...] = (
    "user",
    "project_document",
    "standard",
    "past_work",
    "component",
    "analysis",
    "measurement",
    "assumption",
)

#: For each kind, the field sets any one of which identifies the source.
#: ``user`` needs nothing more: the record already says who recorded it.
SOURCE_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "user": ((),),
    "project_document": (("document",),),
    "standard": (("standard",),),
    "past_work": (("node_id",), ("project_id", "ref")),
    "component": (("mpn",), ("stock",)),
    "analysis": (("evidence",), ("method", "inputs")),
    "measurement": (("artefact",),),
    "assumption": (("rationale",),),
}

#: What each field is for, so a refusal can say what to pass.
_FIELD_HINTS: dict[str, str] = {
    "document": "the ingested document's name or node id",
    "standard": "the standard or reference, e.g. 'EN 12521:2015'",
    "node_id": "the earlier decision or result's node id",
    "project_id": "the earlier project's id",
    "ref": "what in that project the value came from",
    "mpn": "the part number",
    "stock": "the stock item, e.g. '25x25x2 mm steel box section'",
    "evidence": "the twin.record_evidence node id of the result",
    "method": "how it was worked out",
    "inputs": "the values it was worked out from",
    "artefact": "the measured file or node",
    "rationale": "why this default is reasonable",
}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict | tuple):
        return bool(value)
    return True


def source_errors(source: Any) -> list[str]:
    """What is wrong with ``source``; empty when it identifies where a value came from."""
    if not isinstance(source, dict):
        return [
            "'source' must be an object with a 'kind' "
            f"(one of {', '.join(SOURCE_KINDS)}) and the fields that kind needs"
        ]
    kind = source.get("kind")
    if kind not in SOURCE_FIELDS:
        return [f"source 'kind' must be one of {', '.join(SOURCE_KINDS)}; got {kind!r}"]
    options = SOURCE_FIELDS[kind]
    if any(all(_present(source.get(f)) for f in fields) for fields in options):
        return []
    wanted = " or ".join(
        " + ".join(f"'{f}' ({_FIELD_HINTS[f]})" for f in fields) for fields in options
    )
    return [f"a '{kind}' source needs {wanted}"]


def describe_source(source: dict[str, Any]) -> str:
    """One line a reviewer reads, e.g. ``standard: EN 12521:2015 (clause 5.3)``."""
    kind = str(source.get("kind") or "unknown")
    parts = [
        str(source[f])
        for fields in SOURCE_FIELDS.get(kind, ())
        for f in fields
        if _present(source.get(f))
    ]
    for extra in ("clause", "locator", "url"):
        if _present(source.get(extra)):
            parts.append(f"{extra} {source[extra]}")
    return f"{kind}: {', '.join(parts)}" if parts else kind
