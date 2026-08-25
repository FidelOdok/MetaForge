"""Unit tests for the catalog filter DSL -> SQL compiler (MET-436).

Pure ``build_sql()`` tests — no database involved, per the design goal
that this compiler stays unit-testable without a live Postgres.
"""

from __future__ import annotations

import pytest

from digital_twin.catalog.query import (
    CatalogQuery,
    ComponentFilter,
    CrossCategorySpecFilterError,
    UnknownCatalogFieldError,
    build_sql,
)


def test_base_column_filter():
    sql, params = build_sql(
        CatalogQuery(filters=[ComponentFilter(property="cost_usd", op="<=", value=1.0)])
    )
    assert "cost_usd <= $1" in sql
    assert params[0] == 1.0


def test_category_scoped_specs_filter():
    sql, params = build_sql(
        CatalogQuery(
            category="buck_converter",
            filters=[ComponentFilter(property="v_out", op="==", value=5.0)],
        )
    )
    assert "category = $1" in sql
    assert "(specs->>'v_out')::double precision = $2" in sql
    assert params == ["buck_converter", 5.0, 25, 0]  # default limit/offset appended


@pytest.mark.parametrize(
    "op,expected_sql_fragment",
    [
        ("==", "= $"), ("!=", "<> $"), (">=", ">= $"), ("<=", "<= $"),
        (">", "> $"), ("<", "< $"),
    ],
)
def test_comparison_ops_compile(op, expected_sql_fragment):
    sql, _ = build_sql(
        CatalogQuery(
            category="buck_converter",
            filters=[ComponentFilter(property="i_out_max", op=op, value=2.0)],
        )
    )
    assert expected_sql_fragment in sql


def test_in_op_compiles_to_any():
    sql, params = build_sql(
        CatalogQuery(
            category="buck_converter",
            filters=[ComponentFilter(property="package", op="in", value=["QFN-24", "QFN-28"])],
        )
    )
    assert "= ANY(" in sql
    assert ["QFN-24", "QFN-28"] in params


def test_not_in_op_compiles_to_all():
    sql, params = build_sql(
        CatalogQuery(
            category="buck_converter",
            filters=[ComponentFilter(property="package", op="not_in", value=["SO-8"])],
        )
    )
    assert "<> ALL(" in sql
    assert ["SO-8"] in params


def test_in_op_requires_list_value():
    with pytest.raises(ValueError):
        build_sql(
            CatalogQuery(
                category="buck_converter",
                filters=[ComponentFilter(property="package", op="in", value="QFN-24")],
            )
        )


def test_unknown_field_raises():
    with pytest.raises(UnknownCatalogFieldError):
        build_sql(
            CatalogQuery(
                category="buck_converter",
                filters=[ComponentFilter(property="v0ut", op="==", value=5.0)],
            )
        )


def test_unknown_category_raises():
    with pytest.raises(UnknownCatalogFieldError):
        build_sql(
            CatalogQuery(
                category="not_a_real_category",
                filters=[ComponentFilter(property="x", op="==", value=1)],
            )
        )


def test_specs_filter_without_category_raises_cross_category_error():
    with pytest.raises(CrossCategorySpecFilterError):
        build_sql(CatalogQuery(filters=[ComponentFilter(property="v_out", op="==", value=5.0)]))


def test_non_queryable_field_raises():
    with pytest.raises(UnknownCatalogFieldError):
        build_sql(
            CatalogQuery(
                category="flight_controller",
                filters=[ComponentFilter(property="processor", op="==", value="STM32")],
            )
        )


def test_order_by_base_column():
    sql, _ = build_sql(CatalogQuery(order_by="cost_usd", order_desc=False))
    assert "ORDER BY cost_usd ASC" in sql


def test_order_by_specs_field_requires_category():
    with pytest.raises(CrossCategorySpecFilterError):
        build_sql(CatalogQuery(order_by="v_out"))


def test_order_by_specs_field_with_category():
    sql, _ = build_sql(
        CatalogQuery(category="buck_converter", order_by="efficiency", order_desc=True)
    )
    assert "ORDER BY (specs->>'efficiency')::double precision DESC" in sql


def test_limit_and_offset():
    sql, params = build_sql(CatalogQuery(limit=10, offset=20))
    assert "LIMIT $1" in sql
    assert "OFFSET $2" in sql
    assert params == [10, 20]


def test_negative_limit_and_offset_clamp_to_zero():
    _, params = build_sql(CatalogQuery(limit=-5, offset=-1))
    assert params == [0, 0]


def test_max_cost_usd_convenience_field():
    sql, params = build_sql(CatalogQuery(max_cost_usd=2.5))
    assert "cost_usd <= $1" in sql
    assert params[0] == 2.5


def test_lifecycle_in_convenience_field():
    sql, params = build_sql(CatalogQuery(lifecycle_in=["active", "nrnd"]))
    assert "lifecycle = ANY($1)" in sql
    assert params[0] == ["active", "nrnd"]


def test_min_confidence_adds_clause_per_specs_filter():
    sql, params = build_sql(
        CatalogQuery(
            category="buck_converter",
            filters=[ComponentFilter(property="efficiency", op=">=", value=0.9)],
            min_confidence=0.6,
        )
    )
    assert "extraction_meta->'efficiency'->>'confidence')::double precision >= $" in sql
    assert 0.6 in params


def test_min_confidence_not_applied_to_base_column_filters():
    sql, params = build_sql(
        CatalogQuery(
            filters=[ComponentFilter(property="cost_usd", op="<=", value=1.0)],
            min_confidence=0.6,
        )
    )
    assert "confidence" not in sql
    assert 0.6 not in params


def test_unsupported_op_raises():
    with pytest.raises(ValueError):
        build_sql(
            CatalogQuery(
                category="buck_converter",
                filters=[ComponentFilter(property="v_out", op="~=", value=5.0)],  # type: ignore[arg-type]
            )
        )


def test_mpns_dedups_preserving_order():
    from uuid import uuid4

    from digital_twin.catalog.query import CatalogQueryResult, ComponentCatalogRow

    def row(mpn: str) -> ComponentCatalogRow:
        return ComponentCatalogRow(
            id=uuid4(), mpn=mpn, manufacturer="ACME", category="resistor",
            purchase_unit="discrete_part", cost_usd=0.01, lifecycle="active",
            datasheet_url="", specs={}, extraction_meta={}, schema_version=1,
        )

    result = CatalogQueryResult(rows=[row("A"), row("B"), row("A")], query_time_ms=1.0)
    assert result.mpns() == ["A", "B"]
