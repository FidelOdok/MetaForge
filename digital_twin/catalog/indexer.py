"""Extract -> validate -> upsert orchestration for the component catalog (MET-436).

Pure orchestration — reuses the existing, unmodified
``extract_properties_for_mpn`` (3-tier: verbatim/LLM-inferred/derived,
with citations and confidence). Two outcomes once an MPN resolves at all:
``"indexed"`` (every required field present and trusted) or ``"partial"``
(still upserted — a missing/low-confidence field just means a range
filter on it correctly excludes the row via SQL NULL semantics, no
special-casing needed downstream). Only a wholly unresolved MPN
(``mpn_found=False``) produces ``"failed"``, with nothing written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from digital_twin.catalog.query import ComponentCatalogRow
from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY
from digital_twin.catalog.units import UnitConversionError, normalize_value
from digital_twin.knowledge.property_extractor import extract_properties_for_mpn

if TYPE_CHECKING:
    from digital_twin.catalog.store import ComponentCatalogStore
    from digital_twin.knowledge.llm_property_extractor import PropertyLLM
    from digital_twin.knowledge.property_extractor import SearchCallable

IndexStatus = Literal["indexed", "partial", "failed"]


@dataclass
class IndexOutcome:
    """Result of one ``index_component()`` call."""

    mpn: str
    manufacturer: str
    category: str
    status: IndexStatus
    specs: dict[str, Any] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def index_component(
    twin: Any,
    *,
    mpn: str,
    manufacturer: str,
    category: str,
    store: ComponentCatalogStore,
    cost_usd: float | None = None,
    lifecycle: str = "active",
    datasheet_url: str = "",
    llm: PropertyLLM | None = None,
    search: SearchCallable | None = None,
    min_trusted_confidence: float = 0.4,
) -> IndexOutcome:
    """Extract ``category``'s spec fields for ``mpn`` and upsert into the catalog.

    ``llm``/``search`` are forwarded verbatim to
    ``extract_properties_for_mpn`` — passing neither restricts extraction
    to Tier-1 verbatim table matches, same as that function's own default.
    """
    spec = CATEGORY_REGISTRY.get(category)
    if spec is None:
        raise KeyError(f"unknown catalog category {category!r}")

    properties = [f.name for f in spec.fields]
    aliases = {f.name: list(f.aliases) for f in spec.fields if f.aliases}

    extracted = await extract_properties_for_mpn(
        twin,
        mpn,
        properties,
        aliases=aliases,
        llm=llm,
        search=search,
    )

    if not extracted.mpn_found:
        return IndexOutcome(
            mpn=mpn,
            manufacturer=manufacturer,
            category=category,
            status="failed",
            missing_required=[f.name for f in spec.required_fields()],
            errors=["mpn_not_found"],
        )

    items_by_name = {item.property_name: item for item in extracted.items}

    specs: dict[str, Any] = {}
    extraction_meta: dict[str, Any] = {}
    missing_required: list[str] = []
    low_confidence_fields: list[str] = []
    errors: list[str] = []

    for f in spec.fields:
        item = items_by_name.get(f.name)
        if item is None or item.value is None:
            if f.required:
                missing_required.append(f.name)
            continue
        try:
            value = normalize_value(
                item.value,
                from_unit=item.unit,
                to_unit=f.unit,
                value_type=f.type,
                enum_values=f.enum_values,
            )
        except UnitConversionError as exc:
            # Treated the same as NOT_FOUND for indexing purposes — a
            # guessed conversion would be worse than an absent field.
            errors.append(f"{f.name}: {exc}")
            if f.required:
                missing_required.append(f.name)
            continue

        specs[f.name] = value
        extraction_meta[f.name] = {
            "method": str(item.extraction_method),
            "confidence": item.confidence,
            "page": item.page,
            "heading": item.heading,
            "table_row": item.table_row,
        }
        if item.confidence < min_trusted_confidence:
            low_confidence_fields.append(f.name)

    status: IndexStatus = (
        "partial" if (missing_required or low_confidence_fields or errors) else "indexed"
    )

    await store.upsert(
        ComponentCatalogRow(
            id=uuid4(),
            mpn=mpn,
            manufacturer=manufacturer,
            category=category,
            purchase_unit=spec.purchase_unit,
            cost_usd=cost_usd,
            lifecycle=lifecycle,
            datasheet_url=datasheet_url or (extracted.datasheet_source_path or ""),
            specs=specs,
            extraction_meta=extraction_meta,
            schema_version=1,
            indexed_at=datetime.now(UTC),
        )
    )

    return IndexOutcome(
        mpn=mpn,
        manufacturer=manufacturer,
        category=category,
        status=status,
        specs=specs,
        missing_required=missing_required,
        low_confidence_fields=low_confidence_fields,
        errors=errors,
    )


__all__ = ["IndexOutcome", "IndexStatus", "index_component"]
