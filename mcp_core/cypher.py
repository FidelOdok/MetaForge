"""Telling a read-only Cypher query from a mutating one (FORGE-407).

Moved down from ``tool_registry.tools.twin.queries``, not copied. The
approval gate lives in ``mcp_core`` and needs this to decide per *call*
rather than per tool; a second regex in a second layer is a second answer,
and the one that disagrees is the one that waves a write through.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

import re

__all__ = ["MUTATION_KEYWORDS", "detect_mutations", "is_read_only_cypher"]

#: Cypher mutation keywords. Matched case-insensitively as whole tokens, so a
#: legitimate property name containing the substring does not trip it
#: (``RETURN n.created_at`` must not match ``CREATE``).
MUTATION_KEYWORDS: tuple[str, ...] = (
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "MERGE",
    "SET",
    "REMOVE",
    "FOREACH",  # only used inside mutating loops in practice
    "LOAD",  # LOAD CSV
)

# ``\b`` is fine for Cypher because it's ASCII-only.
_MUTATION_PATTERN: re.Pattern[str] = re.compile(
    r"\b(" + "|".join(MUTATION_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def detect_mutations(cypher: str) -> list[str]:
    """Return the mutation keywords found in ``cypher``.

    Empty list = read-only. The caller decides what to do with the list
    (raise to reject, or log to audit).
    """
    if not cypher:
        return []
    matches = _MUTATION_PATTERN.findall(cypher)
    # Dedupe while preserving discovery order so audit logs read naturally.
    seen: dict[str, None] = {}
    for m in matches:
        seen.setdefault(m.upper(), None)
    return list(seen.keys())


def is_read_only_cypher(cypher: object) -> bool:
    """True only when this is certainly a read.

    Deliberately asymmetric. Anything that is not a plain non-empty string —
    absent, the wrong type, empty — is treated as *not* provably read-only,
    because the consequence of guessing wrong in that direction is an
    unapproved write, and the consequence of guessing wrong in the other is
    an approval prompt somebody has to click.
    """
    if not isinstance(cypher, str) or not cypher.strip():
        return False
    return not detect_mutations(cypher)
