"""Filter DSL and SQL compiler for the parametric component catalog (MET-436).

Extends ``bom_populator.py``'s ``{property, op, value}`` shape but compiles
to parameterized SQL instead of scoring pre-fetched rows — client-side
scoring over a small, pre-filtered candidate set (what ``populate_bom``
does) doesn't scale to an indexed, catalog-scale range query; this module
pushes the filtering down to Postgres instead.

**Deliberate divergence from the existing ``knowledge_search`` filter
contract**: that contract returns zero results on an unknown filter key,
by design, for fuzzy semantic search (MET-417/KB-SRC-014). This engine
**fails loudly** instead (``UnknownCatalogFieldError``) — a typo'd field
name in a structured spec query is a much worse silent failure here than
in fuzzy search, since "no buck converter meets your specs" reads as a
real negative result, not a typo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY

CatalogFilterOp = Literal["==", "!=", ">=", "<=", ">", "<", "in", "not_in"]
_ALLOWED_OPS: tuple[CatalogFilterOp, ...] = ("==", "!=", ">=", "<=", ">", "<", "in", "not_in")

_OP_SQL: dict[str, str] = {"==": "=", "!=": "<>", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}

# Base columns every category shares, mapped to their native SQL column
# (no JSONB cast needed) and declared type (for filter-value validation).
_BASE_COLUMNS: dict[str, str] = {
    "mpn": "mpn",
    "manufacturer": "manufacturer",
    "category": "category",
    "purchase_unit": "purchase_unit",
    "cost_usd": "cost_usd",
    "lifecycle": "lifecycle",
    "datasheet_url": "datasheet_url",
}

_CAST_SQL: dict[str, str] = {
    "float": "::double precision",
    "int": "::bigint",
    "bool": "::boolean",
    "str": "",
    "enum": "",
}


class UnknownCatalogFieldError(ValueError):
    """Raised when a filter/order_by references a field not in the taxonomy."""


class CrossCategorySpecFilterError(ValueError):
    """Raised when a specs-field filter/order_by is used without ``category`` set.

    Spec attribute sets are disjoint per category by design (a
    ``flight_controller``'s ``weight_g`` and a ``buck_converter``'s
    ``v_out`` don't coexist in one flat schema) — a filter on a
    category-specific field is meaningless without knowing which
    category's ``SpecField`` list to resolve it against.
    """


@dataclass(frozen=True)
class ComponentFilter:
    """One ``{property, op, value}`` filter clause."""

    property: str
    op: CatalogFilterOp
    value: Any


@dataclass(frozen=True)
class CatalogQuery:
    category: str | None = None
    filters: list[ComponentFilter] = field(default_factory=list)
    lifecycle_in: list[str] | None = None
    max_cost_usd: float | None = None
    min_confidence: float | None = None
    """When set, any specs-field filter also requires that field's
    extraction confidence (``extraction_meta[<field>].confidence``) to be
    at or above this floor."""
    order_by: str | None = None
    order_desc: bool = False
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True)
class ComponentCatalogRow:
    id: UUID
    mpn: str
    manufacturer: str
    category: str
    purchase_unit: Literal["discrete_part", "cots_assembly"]
    cost_usd: float | None
    lifecycle: str
    datasheet_url: str
    specs: dict[str, Any]
    extraction_meta: dict[str, Any]
    schema_version: int
    indexed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """Server-assigned on first insert; ``store.upsert()`` sets this via
    ``now()`` on INSERT and leaves it untouched on UPDATE (``updated_at``
    tracks re-indexing instead). The default here exists purely so
    callers building a row to pass into ``upsert()`` don't need to think
    about it — the value they supply is discarded by the INSERT SQL."""


@dataclass(frozen=True)
class CatalogQueryResult:
    rows: list[ComponentCatalogRow]
    query_time_ms: float

    def mpns(self) -> list[str]:
        """Unique MPNs, order preserved — direct feed for offer-resolution."""
        seen: set[str] = set()
        out: list[str] = []
        for row in self.rows:
            if row.mpn in seen:
                continue
            seen.add(row.mpn)
            out.append(row.mpn)
        return out


def _resolve_field(category: str | None, prop: str) -> tuple[str, str, bool]:
    """Resolve a filter/order_by property name to ``(sql_expr, value_type, is_base_column)``.

    ``sql_expr`` is the *uncast* SQL fragment for the field's raw value —
    the caller appends the type cast (base columns are already native
    types; JSONB specs fields need ``::double precision`` etc, see
    ``_CAST_SQL``).
    """
    if prop in _BASE_COLUMNS:
        value_type = "float" if prop == "cost_usd" else "str"
        return _BASE_COLUMNS[prop], value_type, True

    if category is None:
        raise CrossCategorySpecFilterError(
            f"filter/order_by on {prop!r} requires CatalogQuery.category to be set — "
            "specs attribute sets are disjoint per category"
        )
    spec = CATEGORY_REGISTRY.get(category)
    if spec is None:
        raise UnknownCatalogFieldError(f"unknown category {category!r}")
    field_def = spec.field(prop)
    if field_def is None:
        known = sorted(f.name for f in spec.fields)
        raise UnknownCatalogFieldError(
            f"category {category!r} has no field {prop!r}; known fields: {known}"
        )
    if not field_def.queryable:
        raise UnknownCatalogFieldError(
            f"field {prop!r} on category {category!r} is not queryable "
            "(queryable=False — present in specs for display only)"
        )
    return f"specs->>'{prop}'", field_def.type, False


def build_sql(query: CatalogQuery) -> tuple[str, list[Any]]:
    """Compile a ``CatalogQuery`` into a parameterized SQL SELECT.

    Pure and DB-free — the whole point is that this is unit-testable
    without a live Postgres (see ``tests/unit/test_catalog_query.py``).
    """
    params: list[Any] = []
    clauses: list[str] = []

    if query.category is not None:
        params.append(query.category)
        clauses.append(f"category = ${len(params)}")

    if query.lifecycle_in:
        params.append(list(query.lifecycle_in))
        clauses.append(f"lifecycle = ANY(${len(params)})")

    if query.max_cost_usd is not None:
        params.append(query.max_cost_usd)
        clauses.append(f"cost_usd <= ${len(params)}")

    for filt in query.filters:
        if filt.op not in _ALLOWED_OPS:
            raise ValueError(f"unsupported op {filt.op!r}; must be one of {_ALLOWED_OPS}")
        expr, value_type, is_base = _resolve_field(query.category, filt.property)
        typed_expr = expr if is_base else f"({expr}){_CAST_SQL.get(value_type, '')}"

        if filt.op in ("in", "not_in"):
            if not isinstance(filt.value, (list, tuple)):
                raise ValueError(
                    f"filter on {filt.property!r} with op={filt.op!r} needs a list/tuple value"
                )
            params.append(list(filt.value))
            operator = "= ANY" if filt.op == "in" else "<> ALL"
            clauses.append(f"{typed_expr} {operator}(${len(params)})")
        else:
            params.append(filt.value)
            clauses.append(f"{typed_expr} {_OP_SQL[filt.op]} ${len(params)}")

        if query.min_confidence is not None and not is_base:
            params.append(query.min_confidence)
            clauses.append(
                f"(extraction_meta->'{filt.property}'->>'confidence')::double precision "
                f">= ${len(params)}"
            )

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    order_sql = ""
    if query.order_by:
        order_expr, order_type, order_is_base = _resolve_field(query.category, query.order_by)
        order_typed = (
            order_expr if order_is_base else f"({order_expr}){_CAST_SQL.get(order_type, '')}"
        )
        direction = "DESC" if query.order_desc else "ASC"
        order_sql = f" ORDER BY {order_typed} {direction}"

    params.append(max(0, query.limit))
    limit_sql = f" LIMIT ${len(params)}"
    params.append(max(0, query.offset))
    offset_sql = f" OFFSET ${len(params)}"

    sql = (
        "SELECT id, mpn, manufacturer, category, purchase_unit, cost_usd, "
        "lifecycle, datasheet_url, specs, extraction_meta, schema_version, indexed_at "
        f"FROM component_catalog{where_sql}{order_sql}{limit_sql}{offset_sql}"
    )
    return sql, params


__all__ = [
    "CatalogFilterOp",
    "CatalogQuery",
    "CatalogQueryResult",
    "ComponentCatalogRow",
    "ComponentFilter",
    "CrossCategorySpecFilterError",
    "UnknownCatalogFieldError",
    "build_sql",
]
