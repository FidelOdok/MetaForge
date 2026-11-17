"""Organisations: manufacturers, distributors, fabricators and test labs (FORGE-450).

Before this, a manufacturer was a text field on a component, a supplier a text field in an
offer, and a manufacturing service a machine instance whose name began "Protolabs UK:". Two
spellings of one company ("TI", "Texas Instruments") were two companies, and nothing could
say a vendor was not to be used.

An organisation entry is the company once: its canonical name, every alias it is written as,
its roles, where it is and whom it serves, and whether it is approved, unreviewed or blocked.
Components, offers and machine instances point at it by id; text that names one is resolved
through :func:`org_key`, so any recorded spelling finds the same record.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: What an organisation does for a product. A company may do several.
ORG_ROLES: tuple[str, ...] = ("manufacturer", "distributor", "fabricator", "test_lab")

#: Whether a project may use it. ``blocked`` is flagged wherever it is chosen.
ORG_STATUSES: tuple[str, ...] = ("approved", "unreviewed", "blocked")

#: Regions an organisation serves, coarse enough to filter by.
ORG_REGIONS: tuple[str, ...] = (
    "UK",
    "EU",
    "Europe",
    "Asia",
    "Americas",
    "Oceania",
    "Africa",
    "Global",
)

MAX_ALIASES = 24
MAX_CERTIFICATIONS = 24
MAX_CONTACTS = 8

#: Legal-form words that do not tell two companies apart.
_LEGAL = {
    "inc", "incorporated", "ltd", "limited", "llc", "plc", "corp", "corporation", "co",
    "company", "gmbh", "ag", "sa", "sas", "srl", "spa", "bv", "nv", "ab", "oy", "as",
    "kk", "pte", "pty", "group", "holdings",
}  # fmt: skip
_COUNTRY = re.compile(r"^[A-Z]{2}$")


class OrganisationError(ValueError):
    """An organisation that cannot be recorded as given."""


def org_key(text: Any) -> str:
    """The form a company name is matched in: lower case, punctuation and legal form gone.

    "Texas Instruments Inc." and "texas instruments" give the same key; "TI" does not (that
    is an alias, recorded on the entry).
    """
    words = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()
    while len(words) > 1 and words[-1] in _LEGAL:
        words.pop()
    return " ".join(words)


@dataclass
class OrganisationEntry:
    id: str
    name: str
    title: str
    roles: list[str]
    aliases: list[str] = field(default_factory=list)
    status: str = "unreviewed"
    status_reason: str = ""
    country: str = ""
    regions: list[str] = field(default_factory=list)
    website: str = ""
    quote_url: str = ""
    certifications: list[str] = field(default_factory=list)
    #: Ways to reach it, by role (sales, support, quotes): a page or a shared address,
    #: never a person's own details.
    contacts: list[dict[str, str]] = field(default_factory=list)
    lifecycle_policy: str = ""
    #: The vendor connector that quotes and orders from it, when one exists.
    connector: str = ""
    notes: str = ""
    version: int = 1
    updated_at: float = 0.0
    kind: str = field(default="organisation")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def keys(self) -> set[str]:
        """Every key the entry answers to: its title, its name and each alias."""
        return {k for k in (org_key(x) for x in (self.title, self.name, *self.aliases)) if k}


def _strings(where: str, raw: Any, limit: int) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise OrganisationError(f"{where} must be a list of text")
    out: list[str] = []
    for item in raw:
        item = item.strip()
        if item and item not in out:
            out.append(item[:120])
    if len(out) > limit:
        raise OrganisationError(f"{where} holds at most {limit}")
    return out


def _link(where: str, raw: Any) -> str:
    text = str(raw or "").strip()
    if text and not re.match(r"^https?://\S+$", text):
        raise OrganisationError(f"{where} must be an http(s) address")
    return text


def parse_organisation(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """The organisation fields of ``data``, checked; ``partial`` for an update's changes."""
    out: dict[str, Any] = {}
    if not partial or "roles" in data:
        roles = _strings("roles", data.get("roles"), len(ORG_ROLES))
        bad = [r for r in roles if r not in ORG_ROLES]
        if bad or not roles:
            raise OrganisationError(f"roles must be one or more of {', '.join(ORG_ROLES)}")
        out["roles"] = roles
    if not partial or "status" in data:
        status = str(data.get("status") or "unreviewed")
        if status not in ORG_STATUSES:
            raise OrganisationError(f"status must be one of {', '.join(ORG_STATUSES)}")
        out["status"] = status
        reason = str(data.get("status_reason") or "").strip()
        if status == "blocked" and not reason:
            raise OrganisationError("a blocked organisation says why (status_reason)")
        out["status_reason"] = reason[:300]
    elif "status_reason" in data:
        out["status_reason"] = str(data.get("status_reason") or "").strip()[:300]
    if "aliases" in data or not partial:
        out["aliases"] = _strings("aliases", data.get("aliases"), MAX_ALIASES)
    if "country" in data or not partial:
        country = str(data.get("country") or "").strip().upper()
        if country and not _COUNTRY.match(country):
            raise OrganisationError("country must be an ISO 3166 two-letter code, e.g. GB")
        out["country"] = country
    if "regions" in data or not partial:
        regions = _strings("regions", data.get("regions"), len(ORG_REGIONS))
        bad = [r for r in regions if r not in ORG_REGIONS]
        if bad:
            raise OrganisationError(f"regions must be among {', '.join(ORG_REGIONS)}")
        out["regions"] = regions
    for key in ("website", "quote_url"):
        if key in data or not partial:
            out[key] = _link(key, data.get(key))
    if "certifications" in data or not partial:
        out["certifications"] = _strings(
            "certifications", data.get("certifications"), MAX_CERTIFICATIONS
        )
    if "contacts" in data or not partial:
        raw = data.get("contacts") or []
        if not isinstance(raw, list) or len(raw) > MAX_CONTACTS:
            raise OrganisationError(f"contacts must be a list of up to {MAX_CONTACTS}")
        contacts = []
        for i, c in enumerate(raw):
            if not isinstance(c, dict) or not c.get("role") or not (c.get("url") or c.get("email")):
                raise OrganisationError(f"contacts[{i}] needs a role and a url or shared email")
            row = {"role": str(c["role"]).strip()[:40]}
            if c.get("url"):
                row["url"] = _link(f"contacts[{i}].url", c["url"])
            if c.get("email"):
                row["email"] = str(c["email"]).strip()[:120]
            contacts.append(row)
        out["contacts"] = contacts
    for key, limit in (("lifecycle_policy", 500), ("connector", 64)):
        if key in data or not partial:
            out[key] = str(data.get(key) or "").strip()[:limit]
    return out


def summarize_organisation(entry: OrganisationEntry) -> str:
    bits = [", ".join(r.replace("_", " ") for r in entry.roles)]
    if entry.country:
        bits.append(entry.country)
    if entry.regions:
        bits.append("serves " + ", ".join(entry.regions))
    if entry.status != "approved":
        bits.append(entry.status)
    return f"{entry.title or entry.name}: " + "; ".join(bits)
