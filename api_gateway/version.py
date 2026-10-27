"""One source for the version the gateway reports.

The number used to be the literal ``"0.1.0"``, written out in four places:
``pyproject.toml``, the ``HealthResponse.version`` default, the ``FastAPI``
application factory and the startup log line. They drifted apart as soon as
the release workflow began tagging every push to main — the tags were on
0.3.x while all four literals still read 0.1.0, so ``/health`` disagreed
with the wheel it was running from.

Reading the installed distribution's metadata means there is nothing left to
keep in step by hand. A checkout that was never installed has no metadata to
read; it says so rather than inventing a number.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

#: Distribution name from ``pyproject.toml`` — not the import package, which
#: is ``metaforge``. See the comment on ``[project].name`` for why they differ.
DISTRIBUTION = "metaforge-hw"

#: What to report when the package is importable but was never installed, as
#: in a bare checkout run straight from the source tree.
UNKNOWN = "0.0.0+unknown"


def gateway_version() -> str:
    """Return the installed distribution's version, or :data:`UNKNOWN`."""
    try:
        return _package_version(DISTRIBUTION)
    except PackageNotFoundError:
        return UNKNOWN
