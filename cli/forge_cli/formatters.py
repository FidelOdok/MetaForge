"""Output formatting utilities for the MetaForge CLI.

Supports three output modes:

* **table** (default) — human-friendly aligned columns
* **json** — raw JSON for scripting / piping
* **compact** — one-line-per-item summary
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Core formatting functions
# ---------------------------------------------------------------------------


def format_json(data: Any) -> str:
    """Pretty-print *data* as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def format_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Render *rows* as an aligned text table.

    Parameters
    ----------
    rows:
        List of dictionaries.  Each dict is one row.
    columns:
        Explicit column order.  If ``None``, columns are derived from
        the keys of the first row.

    Returns
    -------
    str
        Multi-line table string with header and separator.
    """
    if not rows:
        return "(no results)"

    if columns is None:
        columns = list(rows[0].keys())

    # Compute column widths
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            cell = str(row.get(col, ""))
            widths[col] = max(widths[col], len(cell))

    # Build header
    header = "  ".join(col.upper().ljust(widths[col]) for col in columns)
    separator = "  ".join("-" * widths[col] for col in columns)

    # Build body
    lines = [header, separator]
    for row in rows:
        line = "  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        lines.append(line)

    return "\n".join(lines)


def format_compact(rows: list[dict[str, Any]], key_field: str = "id") -> str:
    """One-line-per-item summary.

    Each line shows ``<key_field>: <remaining fields as key=value>``.
    """
    if not rows:
        return "(no results)"

    lines: list[str] = []
    for row in rows:
        key_val = row.get(key_field, "?")
        rest = " ".join(
            f"{k}={v}" for k, v in row.items() if k != key_field
        )
        lines.append(f"{key_val}: {rest}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def format_output(
    data: Any,
    fmt: str = "table",
    columns: list[str] | None = None,
    key_field: str = "id",
) -> str:
    """Route *data* through the appropriate formatter.

    Parameters
    ----------
    data:
        Either a list of dicts (for table/compact) or any JSON-serialisable
        value (for json mode).
    fmt:
        One of ``"table"``, ``"json"``, ``"compact"``.
    columns:
        Passed to ``format_table``.
    key_field:
        Passed to ``format_compact``.
    """
    if fmt == "json":
        return format_json(data)
    if fmt == "compact":
        if isinstance(data, list):
            return format_compact(data, key_field=key_field)
        return format_json(data)
    # Default: table
    if isinstance(data, list):
        return format_table(data, columns=columns)
    return format_json(data)
