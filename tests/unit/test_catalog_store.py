"""Pure, DB-free tests for ``digital_twin.catalog.store.schema_statements`` (MET-436).

``schema_statements()`` never had a unit test before this file — it was
only exercised by ``tests/integration/test_catalog_store.py``, which
requires ``asyncpg`` (not installed in every dev/CI environment) and a
live Postgres, and silently no-ops via ``pytest.importorskip`` when
either is missing. That gap is exactly how a real bug shipped
undetected: Postgres rejects a cast expression wrapped in only one
paren pair inside ``CREATE INDEX``'s expression list (e.g.
``((specs->>'x')::double precision)`` — "syntax error at or near
'::'", confirmed against a live server), but the *same* expression
double-wrapped (``(((specs->>'x')::double precision))``) parses fine.
Most visible with multi-word type names like "double precision". These
tests assert the double-wrapped shape directly, in plain Python string
checks, so this class of bug fails fast without needing Postgres at all.
"""

from __future__ import annotations

import re

from digital_twin.catalog.store import schema_statements
from digital_twin.catalog.taxonomy import CATEGORY_REGISTRY

_INDEX_RE = re.compile(
    r"CREATE INDEX IF NOT EXISTS (\w+) ON component_catalog \((.*)\) WHERE category = '(\w+)'$"
)


class TestSchemaStatements:
    def test_first_statement_creates_the_base_table(self) -> None:
        stmts = schema_statements()
        assert "CREATE TABLE IF NOT EXISTS component_catalog" in stmts[0]

    def test_every_statement_has_balanced_parentheses(self) -> None:
        for stmt in schema_statements():
            assert stmt.count("(") == stmt.count(")"), f"unbalanced parens: {stmt!r}"

    def test_every_expression_index_double_wraps_its_expression(self) -> None:
        """Regression guard for the exact bug this file's docstring describes.

        Every per-category expression index's index-key expression must
        be wrapped in its own paren pair *in addition to* the outer
        paren pair ``CREATE INDEX ... ON table (<here>)`` requires —
        i.e. the captured expression (group 2 of ``_INDEX_RE``, already
        stripped of the outer ``ON component_catalog (...)`` wrapper)
        must itself start and end with one more matching paren pair.
        """
        index_stmts = [
            s for s in schema_statements() if s.startswith("CREATE INDEX") and "idx_cc_" in s
        ]
        assert index_stmts, "expected at least one per-category expression index"

        checked_a_cast = False
        for stmt in index_stmts:
            m = _INDEX_RE.match(stmt)
            assert m, f"statement didn't match the expected shape: {stmt!r}"
            expr = m.group(2)
            assert expr.startswith("(") and expr.endswith(")"), (
                f"index expression must be double-wrapped (one extra paren pair "
                f"beyond CREATE INDEX's own), got {expr!r} in {stmt!r}"
            )
            if "::" in expr:
                checked_a_cast = True

        assert checked_a_cast, "expected at least one float/int/bool (cast) field indexed"

    def test_generates_one_index_per_queryable_indexed_field_per_category(self) -> None:
        stmts = schema_statements()
        index_stmts = [s for s in stmts if s.startswith("CREATE INDEX") and "idx_cc_" in s]
        expected = sum(
            1
            for spec in CATEGORY_REGISTRY.values()
            for f in spec.queryable_fields()
            if f.is_indexed
        )
        assert len(index_stmts) == expected

    def test_custom_taxonomy_subset_only_generates_its_own_indexes(self) -> None:
        subset = {"buck_converter": CATEGORY_REGISTRY["buck_converter"]}
        stmts = schema_statements(subset)
        index_stmts = [s for s in stmts if "idx_cc_" in s]
        assert all("buck_converter" in s for s in index_stmts)
        assert not any("flight_controller" in s for s in index_stmts)
