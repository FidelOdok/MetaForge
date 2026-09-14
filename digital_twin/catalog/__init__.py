"""Parametric component catalog (MET-436).

A typed, range-queryable index of electronic component specs — the
structural gap the rest of MetaForge's knowledge layer can't fill:
``extract_properties_for_mpn`` answers "what is MPN X's efficiency" given
the MPN, and ``KnowledgeType.COMPONENT`` semantic search is equality-only.
Neither can answer "which parts have efficiency > 0.9."

- ``taxonomy`` — the declarative ``CATEGORY_REGISTRY`` of component
  categories, each with its own typed ``SpecField`` list.
- ``units`` — datasheet-unit -> canonical-unit normalization.
- ``store`` — ``ComponentCatalogStore``, the Postgres-backed index.
- ``indexer`` — ``index_component()``, extract -> validate -> upsert.
- ``query`` — the ``{property, op, value}`` filter DSL and its SQL compiler.

This package never imports ``digital_twin.knowledge`` — composition with
the intent-search/fuzzy-fallback layer happens only at the MCP adapter
level (``tool_registry/tools/components/adapter.py``), per this repo's
per-directory import-layer rules.
"""

from digital_twin.catalog.indexer import IndexOutcome, IndexStatus, index_component
from digital_twin.catalog.query import (
    CatalogFilterOp,
    CatalogQuery,
    CatalogQueryResult,
    ComponentCatalogRow,
    ComponentFilter,
    CrossCategorySpecFilterError,
    UnknownCatalogFieldError,
    build_sql,
)
from digital_twin.catalog.store import ComponentCatalogStore, schema_statements
from digital_twin.catalog.taxonomy import (
    CATEGORY_REGISTRY,
    CategorySpec,
    PurchaseUnit,
    SpecField,
    categories_by_purchase_unit,
    spec_model,
)
from digital_twin.catalog.units import UnitConversionError, normalize_value

__all__ = [
    "CATEGORY_REGISTRY",
    "CatalogFilterOp",
    "CatalogQuery",
    "CatalogQueryResult",
    "CategorySpec",
    "ComponentCatalogRow",
    "ComponentCatalogStore",
    "ComponentFilter",
    "CrossCategorySpecFilterError",
    "IndexOutcome",
    "IndexStatus",
    "PurchaseUnit",
    "SpecField",
    "UnitConversionError",
    "UnknownCatalogFieldError",
    "build_sql",
    "categories_by_purchase_unit",
    "index_component",
    "normalize_value",
    "schema_statements",
    "spec_model",
]
