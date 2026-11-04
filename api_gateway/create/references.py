"""Reference roles, grounding facts and conflicts (FORGE-794, REF-002..007).

A reference is a work product the Create journey was given: a photo, a
sketch, a datasheet, a drawing. :class:`~api_gateway.create.models.ReferenceRecord`
says what it is *for* (its role), what it is about (associations), whether
the next instruction includes it, what must not change (protected features),
which file version it was (``source_hash``) and how that changed.

Facts are candidates, never truth (REF-005):

* An image gives only ``estimated`` facts. A vision model's reading of a
  photo or sketch (``sketch_reading``) is turned into facts whose basis is
  ``estimated`` and whose uncertainty is ``high``, whatever the model said.
* A text reference (a specification, a datasheet, a rule sheet) gives a
  ``stated`` fact only when the reference's role is authoritative
  (``specification`` or ``manufacturing_rule``) **and** the line names a
  value with a unit. Anything else read from text is ``estimated``.
* Every fact cites its reference, the region (``line 12``, ``whole image``),
  the reference's source hash and the excerpt it came from.

Two authoritative references that state different values for the same
parameter conflict (REF-006); :func:`detect_conflicts` finds them, and the
route raises a ``review_required`` operation event naming both citations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import structlog

from api_gateway.create.models import (
    AUTHORITATIVE_ROLES,
    FactCitation,
    ReferenceFact,
    ReferenceRecord,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.create.references")

#: Text a fact may be read from. PDF text needs an extractor this slice does not add.
TEXT_MIME_PREFIXES = ("text/",)
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json")
#: The most text read from one reference for facts.
MAX_TEXT_BYTES = 512 * 1024
MAX_FACTS = 200

#: Length units, to one base (mm), so ``20 mm`` and ``2 cm`` agree.
_LENGTH_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, '"': 25.4}
_MASS_TO_G = {"g": 1.0, "kg": 1000.0}
#: Units a stated value may carry. A bare number is not a stated fact.
_UNITS = (
    "mm|cm|m|in|inch|kg|g|n|nm|n·m|v|mv|a|ma|w|mw|°c|degc|c|hz|khz|mhz|ghz|mah|wh|pa|kpa|"
    "mpa|bar|psi|%|deg|°|rpm|s|ms|l|ml"
)
#: ``Parameter: 20 mm``, ``Parameter = 20mm``, ``Parameter - 20 mm`` (one per line).
_LINE = re.compile(
    r"^\s*[-*]?\s*(?P<param>[A-Za-z][A-Za-z0-9 ()/_.,'-]{0,80}?)\s*[:=]\s*"
    r"(?P<value>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>" + _UNITS + r")(?![a-z])\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalise_parameter(name: str) -> str:
    """``Max. width (outer)`` and ``max width outer`` are one parameter."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _fact_id(reference_id: str, region: str, parameter: str) -> str:
    raw = f"{reference_id}|{region}|{normalise_parameter(parameter)}"
    return "fact-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_text_reference(mime: str, name: str) -> bool:
    mime = (mime or "").lower()
    if mime.startswith(TEXT_MIME_PREFIXES) or mime == "application/json":
        return True
    return name.lower().endswith(TEXT_EXTENSIONS)


def facts_from_text(
    text: str,
    *,
    reference_id: str,
    source_hash: str,
    role: str,
    extracted_by: str = "text_parser",
) -> list[ReferenceFact]:
    """Facts from ``Parameter: value unit`` lines of a text reference.

    Stated only when ``role`` is authoritative; estimated otherwise. Lines
    without an explicit unit are not facts at all.
    """
    stated = role in AUTHORITATIVE_ROLES
    facts: list[ReferenceFact] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if len(facts) >= MAX_FACTS:
            break
        match = _LINE.match(line)
        if not match:
            continue
        param = match.group("param").strip(" -*.,")
        if not param:
            continue
        region = f"line {number}"
        fid = _fact_id(reference_id, region, param)
        if fid in seen:
            continue
        seen.add(fid)
        facts.append(
            ReferenceFact(
                id=fid,
                parameter=param[:200],
                value=float(match.group("value")),
                unit=match.group("unit").lower(),
                text=line.strip()[:300],
                basis="stated" if stated else "estimated",
                uncertainty="low" if stated else "medium",
                citation=FactCitation(
                    reference_id=reference_id,
                    region=region,
                    source_hash=source_hash,
                    excerpt=line.strip()[:300],
                ),
                extracted_by=extracted_by,
            )
        )
    return facts


def facts_from_reading(
    reading: dict[str, Any],
    *,
    reference_id: str,
    source_hash: str,
    extracted_by: str,
) -> list[ReferenceFact]:
    """Facts from a vision reading of an image: every one ``estimated``, uncertainty high.

    ``reading`` is a :class:`~api_gateway.twin.sketch_reading.SketchReading`
    as a dict. Its proportions are the only numbers; parts become text facts
    (what the image shows), with no value.
    """
    facts: list[ReferenceFact] = []
    for prop in reading.get("proportions") or []:
        if not isinstance(prop, dict):
            continue
        subject = str(prop.get("subject") or "").strip()
        if not subject:
            continue
        value = prop.get("value")
        relative = prop.get("relative_to")
        note = str(prop.get("note") or "")
        region = "whole image"
        facts.append(
            ReferenceFact(
                id=_fact_id(reference_id, region, subject),
                parameter=subject[:200],
                value=float(value) if isinstance(value, int | float) else None,
                unit=str(prop.get("unit") or "")[:32],
                text=(f"relative to {relative}; " if relative else "") + note[:250],
                basis="estimated",
                uncertainty="high",
                citation=FactCitation(
                    reference_id=reference_id,
                    region=region,
                    source_hash=source_hash,
                    excerpt=note[:300],
                ),
                extracted_by=extracted_by,
            )
        )
    for part in reading.get("parts") or []:
        if not isinstance(part, dict) or not part.get("name"):
            continue
        name = str(part["name"])
        region = f"part: {name}"[:200]
        facts.append(
            ReferenceFact(
                id=_fact_id(reference_id, region, name),
                parameter=f"{name} (shown)"[:200],
                value=float(part.get("count") or 1),
                unit="count",
                text=str(part.get("description") or "")[:300],
                basis="estimated",
                uncertainty="high",
                citation=FactCitation(
                    reference_id=reference_id,
                    region=region,
                    source_hash=source_hash,
                    excerpt=str(part.get("description") or "")[:300],
                ),
                extracted_by=extracted_by,
            )
        )
    return facts[:MAX_FACTS]


def _comparable(value: float, unit: str) -> tuple[str, float]:
    u = unit.lower().strip()
    if u in _LENGTH_TO_MM:
        return "length", value * _LENGTH_TO_MM[u]
    if u in _MASS_TO_G:
        return "mass", value * _MASS_TO_G[u]
    return u, value


@dataclass(frozen=True)
class ReferenceConflict:
    """Two authoritative references stating different values for one parameter."""

    parameter: str
    first: ReferenceFact
    second: ReferenceFact

    @property
    def key(self) -> str:
        ids = sorted([self.first.id, self.second.id])
        raw = f"{normalise_parameter(self.parameter)}|{ids[0]}|{ids[1]}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def citation_text(self, names: dict[str, str]) -> str:
        def one(f: ReferenceFact) -> str:
            name = names.get(f.citation.reference_id) or f.citation.reference_id
            value = f"{f.value:g}" if f.value is not None else f.text
            return f"{name} ({f.citation.region}): {value} {f.unit}".strip()

        return f"{one(self.first)} vs {one(self.second)}"

    def as_dict(self, names: dict[str, str]) -> dict[str, Any]:
        return {
            "key": self.key,
            "parameter": self.parameter,
            "citations": [
                self.first.citation.model_dump(),
                self.second.citation.model_dump(),
            ],
            "values": [
                {"value": self.first.value, "unit": self.first.unit},
                {"value": self.second.value, "unit": self.second.unit},
            ],
            "summary": self.citation_text(names),
        }


def detect_conflicts(records: list[ReferenceRecord]) -> list[ReferenceConflict]:
    """Stated facts from different authoritative references that disagree (REF-006).

    Only ``stated`` facts of ``specification`` and ``manufacturing_rule``
    references are compared: an estimate never conflicts with anything, it
    is only an estimate. Values are compared in one base unit per quantity
    (lengths in mm, masses in g); a different quantity is not a conflict.
    """
    by_param: dict[str, list[ReferenceFact]] = {}
    for record in records:
        if record.role not in AUTHORITATIVE_ROLES:
            continue
        for fact in record.facts:
            if fact.basis != "stated" or fact.value is None:
                continue
            by_param.setdefault(normalise_parameter(fact.parameter), []).append(fact)
    conflicts: list[ReferenceConflict] = []
    for facts in by_param.values():
        for i, a in enumerate(facts):
            for b in facts[i + 1 :]:
                if a.citation.reference_id == b.citation.reference_id:
                    continue
                qa, va = _comparable(a.value or 0.0, a.unit)
                qb, vb = _comparable(b.value or 0.0, b.unit)
                if qa != qb:
                    continue
                if abs(va - vb) <= 1e-9 * max(1.0, abs(va), abs(vb)):
                    continue
                conflicts.append(ReferenceConflict(parameter=a.parameter, first=a, second=b))
    return conflicts


def publish_conflicts(project_id: str, records: list[ReferenceRecord]) -> list[dict[str, Any]]:
    """Find conflicts and raise each as a ``review_required`` operation event.

    The operation id derives from the conflict, so the same conflict found
    again is the same event (the store refuses the duplicate) rather than a
    new prompt. Returns the conflicts as dicts.
    """
    from api_gateway.operations import publisher as operations

    names = {r.work_product_id: r.name or r.work_product_id for r in records}
    found = detect_conflicts(records)
    out: list[dict[str, Any]] = []
    with tracer.start_as_current_span("create.references.conflicts") as span:
        span.set_attribute("project.id", project_id)
        span.set_attribute("references.conflicts", len(found))
        for conflict in found:
            item = conflict.as_dict(names)
            out.append(item)
            operations.publish(
                project_id,
                f"job:reference-conflict-{conflict.key}",
                "review_required",
                "waiting_for_user",
                event_id=f"reference-conflict-{conflict.key}",
                domain="concept",
                title="References disagree",
                message=(
                    f"Two authoritative references give different values for "
                    f"{conflict.parameter}: {conflict.citation_text(names)}. "
                    "Choose which one holds."
                )[:500],
                severity="warning",
                artifact_refs=[
                    {
                        "kind": "work_product",
                        "id": conflict.first.citation.reference_id,
                        "label": names.get(conflict.first.citation.reference_id, "Reference"),
                    },
                    {
                        "kind": "work_product",
                        "id": conflict.second.citation.reference_id,
                        "label": names.get(conflict.second.citation.reference_id, "Reference"),
                    },
                ],
            )
            _count_conflict()
            logger.info(
                "reference_conflict",
                project_id=project_id,
                parameter=conflict.parameter,
                key=conflict.key,
            )
    return out


_metrics: Any = None


def _count_conflict() -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_reference_conflict()
    except Exception:  # noqa: BLE001 - a metric never fails a write
        return
