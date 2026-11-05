"""Versioned, process-specific DFM rule profiles (DFM-009).

One profile per process, each a :class:`DfmProfile` at a semantic version,
every rule with its kind (machine limit, heuristic or general guidance) and
the source of its number. No supplier capability is seeded (DFM-014):
:class:`DfmRule` refuses one. Machine limits name the machine library
capability they read; their numbers are never in a profile.

:func:`profile_for_process` maps a manufacturing context's process words
("FDM", "3D printing", "CNC milling", "injection moulding") to a profile.
"""

from __future__ import annotations

import re

from twin_core.dfm.profiles import cnc, fdm, injection_moulding, resin, sheet_metal
from twin_core.dfm.profiles.model import LIMIT_KINDS, DfmProfile, DfmRule, RuleKind

#: Every profile, by id. Order is the order alternatives are offered in.
PROFILES: dict[str, DfmProfile] = {
    p.id: p
    for p in (
        fdm.PROFILE,
        resin.PROFILE,
        cnc.PROFILE,
        injection_moulding.PROFILE,
        sheet_metal.PROFILE,
    )
}


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip() + " "


def profile_for_process(process: str | None) -> DfmProfile | None:
    """The profile for a process named in words, or ``None`` when none fits.

    The longest alias found as whole words wins, so "SLA resin print" is resin
    and "3D printing" is FDM.
    """
    if not process or not str(process).strip():
        return None
    text = _words(str(process))
    best: tuple[int, DfmProfile] | None = None
    for profile in PROFILES.values():
        for alias in (profile.id.replace("_", " "), *profile.aliases):
            needle = _words(alias)
            if needle.strip() and needle in text and (best is None or len(needle) > best[0]):
                best = (len(needle), profile)
    return best[1] if best else None


def get_profile(profile_id: str) -> DfmProfile:
    """A profile by id; ``KeyError`` when there is none."""
    return PROFILES[profile_id]


__all__ = [
    "LIMIT_KINDS",
    "PROFILES",
    "DfmProfile",
    "DfmRule",
    "RuleKind",
    "get_profile",
    "profile_for_process",
]
