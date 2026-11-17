"""The version every MetaForge server reports.

The release workflow stamps the resolved release into ``[project].version``
before it builds the image (``scripts/stamp_version.py``, FORGE-648), so the
installed distribution's metadata is the real number: ``0.3.797`` for a
release, ``0.3.797+g1a2b3c4`` for a build after one. The gateway's
``/health`` already read it. The MCP server did not: it reported a literal
``0.1.0`` in ``initialize``, ``/health`` and its OpenAPI title, whatever it
was running.

Both now read it here. A checkout that was never installed has no metadata;
it says so rather than inventing a number.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

#: Distribution name from ``pyproject.toml``. Not the import package, which is
#: ``metaforge``; see the comment on ``[project].name`` for why they differ.
DISTRIBUTION = "metaforge-hw"

#: Reported when the code is importable but was never installed, as in a bare
#: checkout run straight from the source tree.
UNKNOWN = "0.0.0+unknown"


def package_version() -> str:
    """Return the installed distribution's version, or :data:`UNKNOWN`."""
    try:
        return _distribution_version(DISTRIBUTION)
    except PackageNotFoundError:
        return UNKNOWN
