"""Typed property extraction from datasheet tables (MET-445).

Once MET-444 ships per-page structured tables on
``Datasheet.metadata["tables"]``, this module turns
``("STM32H745ZIT6", "operating_temperature_max_c")`` into a typed
value with a citation and a confidence score.

## Tier ladder

Per the MET-422 spec, ``knowledge.extract`` answers with **three tiers**
of confidence:

| Tier | Source                           | Confidence | Method            |
|------|----------------------------------|------------|-------------------|
| 1    | Literal table-cell match          | 1.0        | ``verbatim``     |
| 2    | LLM-inferred from text chunks     | 0.6 – 0.8  | ``llm_inferred`` |
| 3    | Derived from related fields       | 0.4 – 0.6  | ``derived``      |

This module ships **Tier 1 only**. Tiers 2 and 3 land in a follow-up
that wires the LLM provider into the path; the public API
(``extract_property_from_tables`` + ``ExtractedProperty``) is designed
so adding Tier 2/3 is purely additive.

## Why a standalone module

The extractor is fed pre-extracted tables. It does not hold a
``TwinAPI`` reference, an MCP context, or an LLM client. Callers (the
forthcoming ``knowledge.extract`` MCP tool, the constraint engine,
ad-hoc CLI) walk the supersedes chain themselves to find the **current**
datasheet, pull its ``metadata["tables"]``, and pass them in. Keeping
this surface pure makes it testable without fixtures and reusable
outside MCP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from digital_twin.knowledge.service import ExtractedProperties


class ExtractionMethod(StrEnum):
    """How the value was obtained — used for downstream trust gating."""

    VERBATIM = "verbatim"
    LLM_INFERRED = "llm_inferred"
    DERIVED = "derived"
    NOT_FOUND = "not_found"


@dataclass
class ExtractedProperty:
    """One typed-property answer with a citation and confidence score.

    ``value`` is ``None`` when the property couldn't be located —
    confidence is also 0.0 in that case so the caller can filter on a
    single threshold instead of two.
    """

    property_name: str
    value: str | None
    unit: str | None = None
    confidence: float = 0.0
    extraction_method: ExtractionMethod = ExtractionMethod.NOT_FOUND
    # Citation fields — populated when value is non-None.
    page: int | None = None
    heading: str | None = None
    table_row: int | None = None
    # Free-form: row context, conditions ("at 25 °C"), etc.
    conditions: dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.value is not None


def extract_property_from_tables(
    tables: list[dict[str, Any]],
    property_name: str,
    *,
    aliases: list[str] | None = None,
) -> ExtractedProperty:
    """Tier 1 lookup: find ``property_name`` as a literal table-cell match.

    Scans every table's rows for a cell whose normalised text equals
    the property name (or one of ``aliases``). The adjacent cell on the
    same row is the value. Returns the first match; ties (same property
    in multiple tables) prefer the **earliest page**, then the earliest
    table index, then the earliest row index — deterministic so the
    same input always produces the same citation.

    ``property_name`` matches are case-insensitive and
    whitespace/punctuation-normalised: ``Operating Temperature``,
    ``operating temperature``, ``operating_temperature`` all hit the
    same row. Aliases (e.g. ``["VCC", "Supply Voltage"]``) widen the
    match set without changing the scoring.
    """
    targets = {_normalise(property_name), *(_normalise(a) for a in aliases or [])}

    for table in tables:
        page = int(table.get("page", 0)) or None
        heading = table.get("heading")
        rows = table.get("rows") or []

        for row_index, row in enumerate(rows):
            if not row:
                continue
            # Find the first cell on this row whose normalised text is
            # one of our targets. The adjacent cell (the next non-empty
            # one) is the value.
            for cell_index, cell in enumerate(row):
                if _normalise(cell) not in targets:
                    continue
                value, unit = _value_and_unit(row, cell_index)
                if value is None:
                    # Property name was found but the row had no value
                    # cell to the right — likely a column header or a
                    # malformed row. Skip rather than emit a low-quality hit.
                    continue
                return ExtractedProperty(
                    property_name=property_name,
                    value=value,
                    unit=unit,
                    confidence=1.0,
                    extraction_method=ExtractionMethod.VERBATIM,
                    page=page,
                    heading=heading,
                    table_row=row_index,
                )

    # No match — explicit "not found" rather than a guess.
    return ExtractedProperty(
        property_name=property_name,
        value=None,
        confidence=0.0,
        extraction_method=ExtractionMethod.NOT_FOUND,
    )


async def extract_properties_for_mpn(
    twin: Any,
    mpn: str,
    properties: list[str],
    *,
    aliases: dict[str, list[str]] | None = None,
) -> ExtractedProperties:
    """Resolve ``mpn`` to its current datasheet and extract typed values.

    Wires the MET-446 head-of-supersedes-chain lookup
    (``twin.get_current_datasheet``) into MET-445's
    ``extract_property_from_tables``. Used by ``knowledge.extract``
    (MET-433) and the constraint engine's elec-power-budget rule.

    ``aliases`` maps a requested property name to alternative labels
    the same Tier-1 matcher should accept.

    Returns an ``ExtractedProperties`` with one ``ExtractedProperty``
    per input property name (input order preserved). When no current
    datasheet exists for ``mpn``, ``mpn_found=False`` and ``items``
    is empty.
    """
    from digital_twin.knowledge.service import ExtractedProperties

    datasheet = await twin.get_current_datasheet(mpn)
    if datasheet is None:
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=False,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=[],
        )

    tables = (datasheet.metadata or {}).get("tables") or []
    alias_map = aliases or {}
    items = [
        extract_property_from_tables(tables, name, aliases=alias_map.get(name))
        for name in properties
    ]

    return ExtractedProperties(
        mpn=mpn,
        mpn_found=True,
        datasheet_revision=datasheet.revision,
        datasheet_published_at=datasheet.published_at,
        datasheet_source_path=datasheet.source_url or datasheet.source_path or None,
        items=items,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalise(text: Any) -> str:
    """Lowercase + collapse non-alphanumerics → single underscore."""
    if text is None:
        return ""
    return _WORD_RE.sub("_", str(text).strip().lower()).strip("_")


_UNIT_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s].*)?$")


def _value_and_unit(row: list[Any], label_cell_index: int) -> tuple[str | None, str | None]:
    """Pick the value cell on a row after the label cell, and split off the unit.

    Looks at every cell strictly to the right of ``label_cell_index``,
    skips empties, and returns the first non-empty one as the value.
    When the cell looks like ``"3.6 V"`` we split it into ``("3.6", "V")``.
    Pure-text cells (``"active"``) come back as ``("active", None)``.
    """
    for cell in row[label_cell_index + 1 :]:
        if cell is None:
            continue
        text = str(cell).strip()
        if not text:
            continue
        m = _UNIT_RE.match(text)
        if m:
            value, unit = m.group(1), (m.group(2) or "").strip() or None
            return value, unit
        return text, None
    return None, None
