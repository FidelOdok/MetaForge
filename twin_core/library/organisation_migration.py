"""Build organisation entries from the names the library already holds (FORGE-450).

Before organisations, a company was text in three places: a component's ``manufacturer``,
the ``supplier`` of each offer, and the prefix of a service bureau's machine instance
("Protolabs UK: CNC milling"). :func:`plan_organisations` reads all three and proposes one
organisation per company, merging spellings that :func:`org_key` makes equal and the few
well-known short forms in :data:`KNOWN_ALIASES`. It guesses nothing else: no country from
prose, no status but ``unreviewed``. Names that look related but are not merged (one
contains the other) are listed for a person to decide.

:func:`apply_plan` registers the proposals, links every component and offer through the
catalog's own resolver, and points each bureau instance at its organisation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

from twin_core.library.catalog import CatalogError, SqliteCatalog
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.library.organisations import org_key

logger = structlog.get_logger(__name__)

#: Short forms that do not reduce to their company's key. Each value is the title used.
KNOWN_ALIASES: dict[str, str] = {
    "ti": "Texas Instruments",
    "st": "STMicroelectronics",
    "stmicro": "STMicroelectronics",
    "nxp": "NXP Semiconductors",
    "microchip": "Microchip Technology",
    "adi": "Analog Devices",
    "analog devices maxim": "Analog Devices",
    "maxim": "Analog Devices",
    "maxim integrated": "Analog Devices",
    "on semi": "onsemi",
    "on semiconductor": "onsemi",
    "diodes": "Diodes Incorporated",
    "espressif": "Espressif Systems",
    "nordic": "Nordic Semiconductor",
}


@dataclass
class Proposal:
    title: str
    roles: set[str] = field(default_factory=set)
    spellings: Counter[str] = field(default_factory=Counter)
    locations: set[str] = field(default_factory=set)
    components: int = 0
    offers: int = 0
    machines: int = 0

    def as_dict(self) -> dict[str, Any]:
        aliases = sorted(s for s in self.spellings if s != self.title)
        return {
            "title": self.title,
            "roles": sorted(self.roles),
            "aliases": aliases,
            "components": self.components,
            "offers": self.offers,
            "machines": self.machines,
            "locations": sorted(self.locations),
        }


def _canonical(text: str) -> str:
    key = org_key(text)
    known = KNOWN_ALIASES.get(key)
    return org_key(known) if known else key


def bureau_of(name: str) -> str:
    """The company in a bureau instance's name: the text before ':', or '' if none."""
    head, sep, _ = str(name or "").partition(":")
    return head.strip() if sep else ""


def plan_organisations(catalog: SqliteCatalog, machines: SqliteMachineLibrary) -> dict[str, Any]:
    """What the migration would create and link, without writing anything."""
    found: dict[str, Proposal] = {}

    def note(text: str, role: str) -> Proposal | None:
        text = str(text or "").strip()
        key = _canonical(text)
        if not key:
            return None
        title = KNOWN_ALIASES.get(org_key(text)) or text
        proposal = found.setdefault(key, Proposal(title=title))
        proposal.roles.add(role)
        proposal.spellings[text] += 1
        return proposal

    for comp in catalog.list("component", viewer=None):
        p = note(comp.manufacturer, "manufacturer")
        if p:
            p.components += 1
        for row in comp.suppliers:
            p = note(row.get("supplier"), "distributor")
            if p:
                p.offers += 1
    for machine in machines.list(viewer=None):
        row = machine.as_dict()
        if row.get("layer") != "instance" or row.get("route") != "vendor":
            continue
        p = note(bureau_of(row.get("name", "")), "fabricator")
        if p:
            p.machines += 1
            if row.get("location"):
                p.locations.add(str(row["location"])[:300])

    for key, proposal in found.items():
        # The title is the known canonical name, else the most used spelling.
        known = next(
            (KNOWN_ALIASES[k] for k in KNOWN_ALIASES if org_key(KNOWN_ALIASES[k]) == key), None
        )
        proposal.title = known or proposal.spellings.most_common(1)[0][0]
    existing = {o.id: o for o in catalog.list("organisation", viewer=None)}
    covered = {k for o in existing.values() for k in o.keys()}
    new = {k: p for k, p in found.items() if k not in covered}

    keys = sorted(found)
    related = defaultdict(list)
    for a in keys:
        for b in keys:
            if a != b and len(a) >= 3 and (f"{a} " in f"{b} " or f" {a}" in f" {b}") and a in b:
                related[found[a].title].append(found[b].title)
    return {
        "proposed": [p.as_dict() for p in sorted(new.values(), key=lambda p: p.title.lower())],
        "already_recorded": len(found) - len(new),
        "review": [
            {"name": a, "also_seen_as_part_of": sorted(set(bs))}
            for a, bs in sorted(related.items())
        ],
    }


def apply_plan(
    plan: dict[str, Any], catalog: SqliteCatalog, machines: SqliteMachineLibrary, *, by: str
) -> dict[str, Any]:
    """Register the proposed organisations, then link components, offers and bureaus."""
    created, failed = 0, []
    for p in plan["proposed"]:
        notes = ""
        if p["locations"]:
            notes = "Recorded locations (from the machine library): " + " | ".join(p["locations"])
        try:
            catalog.register(
                "organisation",
                {
                    "title": p["title"],
                    "roles": p["roles"],
                    "aliases": p["aliases"],
                    "status": "unreviewed",
                    "notes": notes[:2000],
                },
                by=by,
                owner=None,
                relink=False,
            )
            created += 1
        except CatalogError as exc:
            failed.append({"title": p["title"], "reason": str(exc)})
    catalog.relink_organisations(by=by)
    linked = sum(
        1
        for comp in catalog.list("component", viewer=None)
        if comp.manufacturer_org or any(row.get("org") for row in comp.suppliers)
    )
    bureaus = 0
    for machine in machines.list(viewer=None):
        row = machine.as_dict()
        if row.get("layer") != "instance" or row.get("operator_org"):
            continue
        org = catalog.resolve_organisation(bureau_of(row.get("name", "")), viewer=None)
        if org is not None:
            machines.update(machine.id, {"operator_org": org.id}, by=by, editor=None)
            bureaus += 1
    logger.info(
        "organisations_migrated",
        created=created,
        components=linked,
        bureaus=bureaus,
        failed=len(failed),
    )
    return {
        "created": created,
        "components_linked": linked,
        "bureaus_linked": bureaus,
        "failed": failed,
    }
