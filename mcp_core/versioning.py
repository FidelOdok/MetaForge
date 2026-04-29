"""MCP tool schema versioning convention (MET-389).

Tool schemas will change (input fields, output shape). Without a
versioning convention every change breaks every harness simultaneously.
This module defines:

* ``schema_version`` annotation — every tool registers with a string
  version (``"v1"`` by default) carried in its manifest metadata.
* ``versioned_tool_id(tool_id, version)`` — produces the parallel
  ``<id>@<version>`` alias the registry exposes alongside the bare id.
* ``deprecation_message`` helper — formats the standard deprecation
  string a tool's ``description`` field carries when the version is
  marked sunset.

Wire format invariants:

* The bare tool name (``knowledge.search``) always points at the
  *latest* registered version. Harnesses that don't pin a version get
  the current schema.
* The versioned alias (``knowledge.search@v1``) is frozen — once
  published, its schema and behaviour don't change.
* During a deprecation window both names are listed; clients choosing
  the bare name still work, clients pinning ``@v1`` keep working.
* After the grace period (one cycle), removing the v1 registration
  cleanly fails harness calls with ``method_not_found``.

Layer-1 invariant: stdlib + pydantic only. Tool adapters import down;
nothing here reaches up.
"""

from __future__ import annotations

import re
from typing import Final

# Allowed schema-version values: ``v`` followed by 1+ digits.
# Forces a deliberate format choice (no ``1``, ``v1.0``, ``v1-beta`` —
# semver inside the version string is over-engineering for a tool
# manifest). Anything else raises at registration time.
_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^v[0-9]+$")

# Default version applied when a tool registers without specifying.
# Bumping this in the future is intentional — every existing tool needs
# to either stay on v1 (frozen) or migrate to the new default.
DEFAULT_VERSION: Final[str] = "v1"


def normalise_version(version: str) -> str:
    """Validate + return the canonical version string.

    Raises ``ValueError`` for anything that doesn't match ``^v[0-9]+$``.
    Centralised so every entrypoint that registers a tool fails the same
    way — a typo can't slip through one path while another catches it.
    """
    if not isinstance(version, str):
        raise ValueError(f"version must be a string, got {type(version).__name__}")
    if not _VERSION_PATTERN.match(version):
        raise ValueError(
            f"invalid schema_version {version!r}: must match {_VERSION_PATTERN.pattern}"
        )
    return version


def versioned_tool_id(tool_id: str, version: str) -> str:
    """Compose a versioned alias for a tool id.

    >>> versioned_tool_id("knowledge.search", "v1")
    'knowledge.search@v1'

    Raises ``ValueError`` if either argument is malformed (no ``@`` in
    the ``tool_id``, no ``v<digits>`` in the version).
    """
    if "@" in tool_id:
        raise ValueError(
            f"tool_id {tool_id!r} already carries an @-suffix; pass the bare id without a version"
        )
    return f"{tool_id}@{normalise_version(version)}"


def parse_versioned_tool_id(versioned: str) -> tuple[str, str | None]:
    """Split a (possibly versioned) tool id into ``(bare_id, version_or_None)``.

    >>> parse_versioned_tool_id("knowledge.search")
    ('knowledge.search', None)
    >>> parse_versioned_tool_id("knowledge.search@v1")
    ('knowledge.search', 'v1')

    Used by ``tool/call`` dispatch so a harness can pin to a specific
    version (``knowledge.search@v1``) and still hit the same registry
    entry as the bare name.
    """
    if "@" not in versioned:
        return versioned, None
    bare, _, version = versioned.partition("@")
    # Validate the version segment to catch malformed aliases at parse
    # time rather than letting them cascade into "method not found".
    normalise_version(version)
    return bare, version


def deprecation_message(version: str, replaced_by_version: str, sunset_cycle: str) -> str:
    """Standard ``deprecation_warning`` blurb for a tool's description.

    >>> deprecation_message("v1", "v2", "2026-Q3-W3")
    '⚠️ DEPRECATED: schema v1 sunsets 2026-Q3-W3. Migrate to v2 (pin via @v2 in tool name).'
    """
    normalise_version(version)
    normalise_version(replaced_by_version)
    return (
        f"⚠️ DEPRECATED: schema {version} sunsets {sunset_cycle}. "
        f"Migrate to {replaced_by_version} (pin via @{replaced_by_version} in tool name)."
    )


__all__ = [
    "DEFAULT_VERSION",
    "deprecation_message",
    "normalise_version",
    "parse_versioned_tool_id",
    "versioned_tool_id",
]
