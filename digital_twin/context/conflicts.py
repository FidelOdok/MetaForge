"""Conflict detection across assembled context fragments (MET-322).

Surfaces real disagreement between sources rather than silently picking
one — when the schematic says ``voltage: 5V`` and the BOM says ``3.3V``
for the same MPN, the agent needs to know.

Three pieces wire together:

* ``Conflict`` — Pydantic model with ``field``, ``source_a/_b``,
  ``value_a/_b``, ``severity``, ``mpn`` (or other grouping key), plus
  a free-text ``description``.
* ``ConflictSeverity`` — ``info`` / ``warning`` / ``blocking``.
  ``blocking`` flips a request flag so agents can refuse to act.
* ``ConflictDetector`` — extracts value-bearing fields per fragment,
  groups by their identity key (default ``mpn``), and emits a
  ``Conflict`` for each field that disagrees within a group.

Field extraction order:

1. ``metadata[<field>]`` — caller / consumer set this explicitly.
2. ``content`` regex of the form ``<field>: <value>`` — works for
   markdown tables and prose alike.

Field-to-severity table is data-only so MET-326 / MET-333 can extend
it without touching the detector.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from digital_twin.context.models import ContextFragment

__all__ = [
    "Conflict",
    "ConflictDetector",
    "ConflictSeverity",
    "DEFAULT_FIELD_SEVERITY",
    "DEFAULT_TRACKED_FIELDS",
]


class ConflictSeverity(StrEnum):
    """Severity tiers for surfaced conflicts.

    * ``INFO`` — likely cosmetic (package alias, e.g. ``SOIC-8`` vs
      ``SO-8``); included in context for the agent's awareness.
    * ``WARNING`` — disagreement on a load-bearing parameter
      (voltage, current, footprint); agent should mention it but may
      still propose.
    * ``BLOCKING`` — identity mismatch (different MPN for the same
      ref-des); agent must refuse the action and escalate.
    """

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


# Per-field severity. Designed so that MET-333 dashboards can render a
# colour-coded badge per row without bespoke logic.
DEFAULT_FIELD_SEVERITY: dict[str, ConflictSeverity] = {
    "mpn": ConflictSeverity.BLOCKING,
    "voltage": ConflictSeverity.WARNING,
    "current": ConflictSeverity.WARNING,
    "tolerance": ConflictSeverity.WARNING,
    "material": ConflictSeverity.WARNING,
    "package": ConflictSeverity.INFO,
    "footprint": ConflictSeverity.INFO,
}

DEFAULT_TRACKED_FIELDS: list[str] = list(DEFAULT_FIELD_SEVERITY.keys())


# Match ``<field>: <value>`` lines (markdown tables, prose, key-value
# blocks). Stops at the first newline; values are trimmed by the caller.
_KV_LINE_RE = re.compile(
    r"(?im)^[\s|*\-]*(?P<field>[A-Za-z][A-Za-z0-9_ ]{1,30})\s*[:=]\s*(?P<value>[^\n|]+?)\s*(?:\||$)"
)


class Conflict(BaseModel):
    """A single observed disagreement between two context fragments."""

    field: str = Field(..., description="Field name in dispute (e.g. ``mpn``)")
    value_a: str = Field(..., description="Value reported by the first source")
    value_b: str = Field(..., description="Value reported by the second source")
    source_a: str = Field(..., description="``source_id`` of the first fragment")
    source_b: str = Field(..., description="``source_id`` of the second fragment")
    severity: ConflictSeverity = Field(...)
    grouping_key: str | None = Field(
        default=None,
        description=(
            "Identity key the two sources shared — typically the MPN, "
            "ref-des, or work-product UUID. ``None`` means the conflict "
            "was detected globally without a grouping pivot."
        ),
    )
    description: str = Field(default="", description="Human-readable summary")


class ConflictDetector:
    """Extract field-value pairs from fragments and emit ``Conflict`` rows.

    Construction is data-only — pass a custom ``tracked_fields`` /
    ``severity_map`` / ``grouping_field`` to widen detection without
    forking. ``detect`` runs in O(n) over fragments per group key.
    """

    def __init__(
        self,
        tracked_fields: list[str] | None = None,
        severity_map: dict[str, ConflictSeverity] | None = None,
        grouping_field: str = "mpn",
    ) -> None:
        self._tracked_fields = tracked_fields or DEFAULT_TRACKED_FIELDS
        self._severity_map = severity_map or DEFAULT_FIELD_SEVERITY
        self._grouping_field = grouping_field

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, fragments: list[ContextFragment]) -> list[Conflict]:
        """Scan *fragments* and return every disagreement found.

        Two fragments conflict on ``field`` when:

        1. They share a ``grouping_field`` value (default ``mpn``), or
           one is the pivot work-product fragment, AND
        2. Both fragments have a value for ``field``, AND
        3. The two values normalise to different strings.

        Conflicts are deduplicated — a given (group, field, source_a,
        source_b) tuple appears once.
        """
        if not fragments:
            return []

        # group_key (e.g. MPN value, or "__global__") → list of
        # (fragment, extracted_fields) pairs.
        groups: dict[str, list[tuple[ContextFragment, dict[str, str]]]] = {}
        for fragment in fragments:
            extracted = self._extract_fields(fragment)
            if not extracted:
                continue
            group_key = extracted.get(self._grouping_field) or "__global__"
            groups.setdefault(group_key, []).append((fragment, extracted))

        conflicts: list[Conflict] = []
        seen: set[tuple[str, str, str, str]] = set()
        for group_key, members in groups.items():
            if len(members) < 2:
                continue
            for i, (frag_a, fields_a) in enumerate(members):
                for frag_b, fields_b in members[i + 1 :]:
                    for field in self._tracked_fields:
                        a = fields_a.get(field)
                        b = fields_b.get(field)
                        if not a or not b:
                            continue
                        if self._normalise(a) == self._normalise(b):
                            continue
                        # Order source ids so dedup works regardless
                        # of which fragment was "a".
                        s1, s2 = sorted([frag_a.source_id, frag_b.source_id])
                        key = (group_key, field, s1, s2)
                        if key in seen:
                            continue
                        seen.add(key)
                        conflicts.append(
                            Conflict(
                                field=field,
                                value_a=a,
                                value_b=b,
                                source_a=frag_a.source_id,
                                source_b=frag_b.source_id,
                                severity=self._severity_map.get(field, ConflictSeverity.INFO),
                                grouping_key=None if group_key == "__global__" else group_key,
                                description=(
                                    f"{field} disagrees between "
                                    f"{frag_a.source_id} ({a}) and "
                                    f"{frag_b.source_id} ({b})"
                                ),
                            )
                        )
        return conflicts

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _extract_fields(self, fragment: ContextFragment) -> dict[str, str]:
        """Pull tracked fields from metadata first, then content regex."""
        out: dict[str, str] = {}
        # Metadata wins — agents and ingestion may pre-populate.
        for field in [self._grouping_field, *self._tracked_fields]:
            value = fragment.metadata.get(field)
            if value is None or not isinstance(value, str | int | float):
                continue
            out[field] = str(value).strip()

        # Fall back to content scan for fields not yet found.
        if fragment.content:
            for match in _KV_LINE_RE.finditer(fragment.content):
                key = match.group("field").strip().lower().replace(" ", "_")
                if key not in self._tracked_fields and key != self._grouping_field:
                    continue
                if key in out:
                    continue  # Metadata already won.
                out[key] = match.group("value").strip()
        return out

    @staticmethod
    def _normalise(value: str) -> str:
        """Case-insensitive whitespace-collapsed compare key."""
        return " ".join(value.lower().split())
