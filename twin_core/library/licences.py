"""Which third-party content licences MetaForge accepts for imported geometry.

A part model somebody else made can be stored, shown and shipped in a
deliverable only under a licence that allows all three without a separate
agreement. The list is short on purpose: each entry is a licence whose
terms are public, permit commercial use and modification, and ask for no
more than credit and keeping the licence text with copies.

A licence outside it is not necessarily unusable; it means a person has to
read it. Universal Robots' graphical-documentation terms are the case that
prompted this (FORGE-711): they bind anyone who opens the file, which is not
something the importer can agree to on a user's behalf.

Layer: stdlib only.
"""

from __future__ import annotations

import re

#: SPDX ids accepted without a person reading the terms.
ACCEPTED_LICENCES: frozenset[str] = frozenset(
    {"CC-BY-4.0", "CC0-1.0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MIT"}
)

#: What each accepted licence asks of whoever stores or passes on a copy.
OBLIGATIONS: dict[str, str] = {
    "CC-BY-4.0": "credit the creator, link the licence, and say if the model was changed",
    "CC0-1.0": "none",
    "Apache-2.0": "keep the licence text and any NOTICE with copies, and mark changed files",
    "BSD-2-Clause": "keep the copyright notice and licence text with copies",
    "BSD-3-Clause": (
        "keep the copyright notice and licence text with copies, and do not use the "
        "owner's name to endorse anything"
    ),
    "MIT": "keep the copyright notice and licence text with copies",
}

_ALIASES: dict[str, str] = {
    "ccby40": "CC-BY-4.0",
    "ccby4": "CC-BY-4.0",
    "creativecommonsattribution40": "CC-BY-4.0",
    "creativecommonsattribution40international": "CC-BY-4.0",
    "attribution40international": "CC-BY-4.0",
    "cc010": "CC0-1.0",
    "cc0": "CC0-1.0",
    "apache20": "Apache-2.0",
    "apachelicense20": "Apache-2.0",
    "apachelicenseversion20": "Apache-2.0",
    "bsd2clause": "BSD-2-Clause",
    "bsd3clause": "BSD-3-Clause",
    "mit": "MIT",
    "mitlicense": "MIT",
}


def normalise_licence(raw: object) -> str | None:
    """An SPDX id for a licence name as datasets and files spell it, or None.

    ``"CC-BY 4.0"``, ``"cc-by-4.0"`` and ``"Creative Commons Attribution 4.0"``
    are the same licence. A name that is not recognised comes back as written
    (stripped), so a refusal can quote it, never silently as an accepted id.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    return _ALIASES.get(key, text)


def is_accepted(licence: str | None) -> bool:
    return licence in ACCEPTED_LICENCES
