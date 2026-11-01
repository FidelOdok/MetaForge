"""Deciding whether a SimReady package may be stored (FORGE-711).

A package states its licence in up to four places: the Hugging Face dataset
card, the package's ``com.nvidia.simready.packaging.json``, the
``.metadata/LICENSE.txt`` beside it, and ``asset_license`` in the root USD
layer. SimReady Central requires the first two to match; this module also
requires the others to agree when they are present, because a disagreement
means somebody repackaged the asset and the licence can no longer be taken
on trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from twin_core.library.licences import OBLIGATIONS, is_accepted, normalise_licence


@dataclass(frozen=True)
class LicenceDecision:
    licence: str | None
    accepted: bool
    reason: str
    obligations: str = ""
    declared: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "licence": self.licence,
            "accepted": self.accepted,
            "reason": self.reason,
            "obligations": self.obligations,
            "declared": dict(self.declared),
        }


def detect_licence_text(text: str | None) -> str | None:
    """The SPDX id of a full licence text, from its tell-tale lines."""
    if not text:
        return None
    t = " ".join(text.split()).lower()
    if "attribution 4.0 international" in t and "creative commons" in t:
        return "CC-BY-4.0"
    if "cc0 1.0 universal" in t:
        return "CC0-1.0"
    if "apache license" in t and "version 2.0" in t:
        return "Apache-2.0"
    if "redistribution and use in source and binary forms" in t:
        return "BSD-3-Clause" if "neither the name" in t else "BSD-2-Clause"
    if "permission is hereby granted, free of charge" in t:
        return "MIT"
    return None


def decide(declared: dict[str, object]) -> LicenceDecision:
    """Accept only when every place that names a licence names the same accepted one.

    ``declared`` maps where the licence was read (``dataset``, ``package``,
    ``licence_file``, ``usd_layer``) to what it said; ``None`` means that
    place said nothing.
    """
    named = {k: v for k, v in ((k, normalise_licence(v)) for k, v in declared.items()) if v}
    if not named:
        return LicenceDecision(None, False, "the package declares no licence", declared=named)
    distinct = sorted(set(named.values()))
    if len(distinct) > 1:
        where = ", ".join(f"{k}: {v}" for k, v in sorted(named.items()))
        return LicenceDecision(
            None, False, f"the licence is stated inconsistently ({where})", declared=named
        )
    licence = distinct[0]
    if not is_accepted(licence):
        return LicenceDecision(
            licence,
            False,
            f"{licence} is not a licence MetaForge stores without a person reading it",
            declared=named,
        )
    return LicenceDecision(licence, True, "accepted", OBLIGATIONS.get(licence, ""), named)
