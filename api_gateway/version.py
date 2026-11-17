"""The version the gateway reports.

It is the installed distribution's version, which the release workflow stamps
with the resolved release before building the image. The lookup lives in
:mod:`metaforge.version` so the gateway and the MCP server report the same
number; this module keeps the gateway's names.
"""

from __future__ import annotations

from metaforge.version import DISTRIBUTION, UNKNOWN, package_version

__all__ = ["DISTRIBUTION", "UNKNOWN", "gateway_version"]


def gateway_version() -> str:
    """Return the installed distribution's version, or ``UNKNOWN``."""
    return package_version()
