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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from digital_twin.knowledge.llm_property_extractor import PropertyLLM
    from digital_twin.knowledge.service import ExtractedProperties, SearchHit

# Type alias: a narrow search callable that takes the MPN query and a
# top-K cap and returns ``SearchHit`` rows. Kept local so this module
# doesn't import the full ``KnowledgeService`` protocol just for typing.
SearchCallable = Callable[[str, int], Awaitable[list["SearchHit"]]]

# G4 (MET-477): max chunks pulled from search for the LLM-over-chunks
# fallback when no Twin Datasheet exists. 5 keeps the prompt budget
# bounded; chunks are typically ~1k chars each.
_DEFAULT_FALLBACK_TOP_K = 5


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
    llm: PropertyLLM | None = None,
    search: SearchCallable | None = None,
    fallback_top_k: int = _DEFAULT_FALLBACK_TOP_K,
) -> ExtractedProperties:
    """Resolve ``mpn`` to its current datasheet and extract typed values.

    Wires the MET-446 head-of-supersedes-chain lookup
    (``twin.get_current_datasheet``) into MET-445's
    ``extract_property_from_tables``. Used by ``knowledge.extract``
    (MET-433) and the constraint engine's elec-power-budget rule.

    ``aliases`` maps a requested property name to alternative labels
    the same Tier-1 matcher should accept.

    ``llm`` (MET-462) enables the Tier-2/3 fallback: when Tier-1 verbatim
    matching can't locate a property and the datasheet carries prose text,
    the property is re-asked of the LLM (``llm_inferred`` / ``derived``).
    When ``llm`` is ``None`` the behaviour is Tier-1-only, unchanged.

    ``search`` (MET-477 G4) plus ``llm`` together enable the
    LLM-over-chunks fallback: when no Twin ``Datasheet`` node exists for
    ``mpn`` (text-only ingest path), search the KB for the MPN, take the
    top ``fallback_top_k`` chunks, concatenate their content, and run
    each property through the Tier-2/3 LLM extractor against that
    synthesised prose. This is what closes the populated-KB-but-empty-
    Twin gap the MET-477 smoke surfaced. When the fallback fires,
    ``mpn_found`` is True, ``datasheet_revision`` is ``None`` (sentinel
    for "synthesised from chunks"), and ``datasheet_source_path`` is the
    source path of the top-ranked hit.

    Returns an ``ExtractedProperties`` with one ``ExtractedProperty``
    per input property name (input order preserved). When no current
    datasheet exists for ``mpn`` AND no fallback path is available,
    ``mpn_found=False`` and ``items`` is empty.
    """
    from digital_twin.knowledge.service import ExtractedProperties

    datasheet = await twin.get_current_datasheet(mpn)
    if datasheet is None:
        return await _extract_via_search_fallback(
            mpn=mpn,
            properties=properties,
            llm=llm,
            search=search,
            top_k=fallback_top_k,
        )

    tables = (datasheet.metadata or {}).get("tables") or []
    alias_map = aliases or {}
    datasheet_text = _datasheet_text(datasheet) if llm is not None else ""

    items: list[ExtractedProperty] = []
    for name in properties:
        tier1 = extract_property_from_tables(tables, name, aliases=alias_map.get(name))
        if tier1.found or llm is None or not datasheet_text:
            items.append(tier1)
            continue
        # Tier-1 miss with an LLM wired and prose available → Tier-2/3.
        from digital_twin.knowledge.llm_property_extractor import infer_property

        items.append(
            await infer_property(
                llm,
                mpn=mpn,
                property_name=name,
                datasheet_text=datasheet_text,
            )
        )

    return ExtractedProperties(
        mpn=mpn,
        mpn_found=True,
        datasheet_revision=datasheet.revision,
        datasheet_published_at=datasheet.published_at,
        datasheet_source_path=datasheet.source_url or datasheet.source_path or None,
        items=items,
    )


async def _extract_via_search_fallback(
    *,
    mpn: str,
    properties: list[str],
    llm: PropertyLLM | None,
    search: SearchCallable | None,
    top_k: int,
) -> ExtractedProperties:
    """G4 fallback path used when no Twin ``Datasheet`` node exists.

    Returns a fully NOT_FOUND result (``mpn_found=False``, empty
    ``items``) when either dependency is missing — that's the
    pre-MET-477 contract. When both ``search`` and ``llm`` are wired,
    pull top-K chunks and run the same per-property LLM extractor used
    for Tier-2/3 prose lookups; ``mpn_found`` flips to True even though
    no structured Datasheet node was found.
    """
    from digital_twin.knowledge.service import ExtractedProperties

    if llm is None or search is None:
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=False,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=[],
        )

    try:
        hits = await search(mpn, top_k)
    except Exception:
        # Search backend failure must not crash extract — fall through
        # to "no datasheet" semantics, same as a missing dependency.
        hits = []

    if not hits:
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=False,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=[],
        )

    chunks_text = "\n\n".join((hit.content or "") for hit in hits if hit.content)
    if not chunks_text:
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=False,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=None,
            items=[],
        )

    from digital_twin.knowledge.llm_property_extractor import infer_property

    items: list[ExtractedProperty] = []
    for name in properties:
        items.append(
            await infer_property(
                llm,
                mpn=mpn,
                property_name=name,
                datasheet_text=chunks_text,
            )
        )

    top_source = next((hit.source_path for hit in hits if hit.source_path), None)
    return ExtractedProperties(
        mpn=mpn,
        mpn_found=True,
        datasheet_revision=None,
        datasheet_published_at=None,
        datasheet_source_path=top_source,
        items=items,
    )


def _datasheet_text(datasheet: Any) -> str:
    """Best-effort prose text for the Tier-2/3 LLM pass.

    Prefers an explicit full-text field on ``metadata`` (``text`` /
    ``full_text`` / ``raw_text``); falls back to flattening any
    structured tables so a table-only datasheet still gives the model
    something to reason over. Returns ``""`` when nothing is available —
    the caller then skips the LLM pass.
    """
    metadata = datasheet.metadata or {}
    for key in ("text", "full_text", "raw_text"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    tables = metadata.get("tables") or []
    parts: list[str] = []
    for table in tables:
        heading = table.get("heading")
        if heading:
            parts.append(str(heading))
        for row in table.get("rows") or []:
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


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
