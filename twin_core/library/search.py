"""One search over the whole library (FORGE-895).

The library could list a kind by family and fetch one entry by exact id or name; nothing
answered "what do we have for 'xiao'" or "UK fabricators for SLS". :func:`search_library`
matches every word of a query against each entry's text fields, across every kind, and
ranks what matches all of them. Nothing is fuzzy or learned: a hit names the fields that
matched, so a person can see why it is there.

A component also answers to its maker's organisation (title and every alias) and to its
suppliers', a machine instance to its operator's. Organisations carry their status, and a
component whose maker is blocked, or whose every seller is blocked, says so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.library.organisations import OrganisationEntry

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.library.search")

#: What can be searched, in the order a tie is shown.
SEARCH_KINDS: tuple[str, ...] = (
    "component",
    "organisation",
    "material",
    "process",
    "machine",
    "appearance",
    "ergonomic_limit",
)
MAX_LIMIT = 200

#: How much a match in each kind of field counts. An identifier beats a description.
_W_ID, _W_TITLE, _W_LINKED, _W_TEXT = 8.0, 5.0, 3.0, 1.0


def words(text: Any) -> list[str]:
    return re.sub(r"[^a-z0-9.+#/-]+", " ", str(text or "").lower()).split()


@dataclass
class Hit:
    kind: str
    id: str
    name: str
    title: str
    score: float
    matched: list[str]
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "score": round(self.score, 2),
            "matched": self.matched,
            **self.detail,
        }


def _score(query: list[str], fields: Iterable[tuple[str, float, Any]]) -> tuple[float, list[str]]:
    """Each query word must match some field; a word scores its best field.

    A whole-word match counts fully, a word that starts a field word 0.7, a substring
    0.4. Returns (0, []) when any word matches nothing.
    """
    prepared = [(label, weight, words(value)) for label, weight, value in fields if value]
    total, matched = 0.0, set()
    for q in query:
        best, where = 0.0, ""
        for label, weight, field_words in prepared:
            for w in field_words:
                s = 1.0 if w == q else 0.7 if w.startswith(q) else 0.4 if q in w else 0.0
                if s * weight > best:
                    best, where = s * weight, label
        if best == 0.0:
            return 0.0, []
        total += best
        matched.add(where)
    return total, sorted(matched)


def _org_fields(org: OrganisationEntry | None, label: str) -> list[tuple[str, float, Any]]:
    if org is None:
        return []
    return [(label, _W_LINKED, " ".join([org.title, *org.aliases]))]


def search_library(
    catalog: Any,
    machines: Any,
    query: str,
    *,
    kinds: Iterable[str] | None = None,
    role: str | None = None,
    region: str | None = None,
    status: str | None = None,
    limit: int = 50,
    viewer: Any = None,
) -> dict[str, Any]:
    """Entries matching every word of ``query``, best first, across ``kinds``.

    ``role``, ``region`` and ``status`` filter organisations, and the components and
    machines linked to one: a component passes ``region`` when its maker or a seller
    serves the region, a machine when its operator does (or it is your own).
    """
    q = words(query)
    wanted = [k for k in (kinds or SEARCH_KINDS) if k in SEARCH_KINDS]
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    with tracer.start_as_current_span("library.search") as span:
        span.set_attribute("search.words", len(q))
        orgs = {o.id: o for o in catalog.list("organisation", viewer=viewer)}

        def org_ok(org: OrganisationEntry | None) -> bool:
            if org is None:
                return role is None and region is None and status is None
            if role and role not in org.roles:
                return False
            if region and region not in org.regions and "Global" not in org.regions:
                return False
            return not (status and org.status != status)

        hits: list[Hit] = []
        for kind in wanted:
            if kind == "machine":
                entries: list[Any] = list(machines.list(viewer=viewer))
            else:
                entries = list(catalog.list(kind, viewer=viewer))
            for entry in entries:
                hit = _match(kind, entry, q, orgs, org_ok, (role, region, status))
                if hit is not None:
                    hits.append(hit)
        order = {k: i for i, k in enumerate(SEARCH_KINDS)}
        hits.sort(key=lambda h: (-h.score, order[h.kind], h.title.lower()))
        span.set_attribute("search.hits", len(hits))
    logger.info("library_searched", words=len(q), kinds=len(wanted), hits=len(hits))
    return {
        "query": query,
        "total": len(hits),
        "results": [_with_owner(h.as_dict(), catalog, machines) for h in hits[:limit]],
        "filters": {"kinds": wanted, "role": role, "region": region, "status": status},
    }


def _with_owner(hit: dict[str, Any], catalog: Any, machines: Any) -> dict[str, Any]:
    """The hit, saying whether it is from the shared library (FORGE-876) or the
    caller's own, as every library listing does."""
    store = machines if hit["kind"] == "machine" else catalog
    owner_of = getattr(store, "owner_of", None)
    if owner_of is None:
        return hit
    try:
        return {**hit, "shared": owner_of(hit["id"]) is None}
    except Exception:  # noqa: BLE001 -- an entry removed mid-search is just unmarked
        return hit


def _match(
    kind: str,
    entry: Any,
    q: list[str],
    orgs: dict[str, OrganisationEntry],
    org_ok: Any,
    filters: tuple[str | None, str | None, str | None],
) -> Hit | None:
    filtering = any(filters)
    detail: dict[str, Any] = {}
    if kind == "organisation":
        if not org_ok(entry):
            return None
        fields = [
            ("title", _W_TITLE, entry.title),
            ("alias", _W_ID, " ".join(entry.aliases)),
            ("name", _W_ID, entry.name),
            ("role", _W_TEXT, " ".join(r.replace("_", " ") for r in entry.roles)),
            ("place", _W_TEXT, " ".join([entry.country, *entry.regions])),
            ("certifications", _W_TEXT, " ".join(entry.certifications)),
            ("notes", _W_TEXT, entry.notes),
        ]
        detail = {
            "roles": entry.roles,
            "status": entry.status,
            "country": entry.country,
            "regions": entry.regions,
        }
    elif kind == "component":
        maker = orgs.get(entry.manufacturer_org)
        sellers = [orgs.get(r.get("org", "")) for r in entry.suppliers]
        if filtering and not (org_ok(maker) or any(org_ok(s) for s in sellers if s)):
            return None
        fields = [
            ("name", _W_ID, entry.name),
            ("mpn", _W_ID, entry.mpn),
            ("title", _W_TITLE, entry.title),
            ("manufacturer", _W_LINKED, entry.manufacturer),
            *_org_fields(maker, "manufacturer"),
            ("supplier", _W_TEXT, " ".join(r.get("supplier", "") for r in entry.suppliers)),
            ("sku", _W_ID, " ".join(r.get("sku", "") for r in entry.suppliers)),
            ("family", _W_TEXT, entry.family),
            ("notes", _W_TEXT, entry.notes),
        ]
        known = [s for s in sellers if s]
        blocked = []
        if maker is not None and maker.status == "blocked":
            blocked.append(f"maker {maker.title}: {maker.status_reason}")
        if (
            known
            and len(known) == len(entry.suppliers)
            and all(s.status == "blocked" for s in known)
        ):
            blocked.append("every seller is blocked")
        detail = {
            "family": entry.family,
            "mpn": entry.mpn,
            "manufacturer": entry.manufacturer,
            "manufacturer_org": entry.manufacturer_org or None,
            "suppliers": sorted({r.get("supplier", "") for r in entry.suppliers}),
        }
        if blocked:
            detail["blocked"] = blocked
    elif kind == "machine":
        row = entry.as_dict()
        operator = orgs.get(row.get("operator_org") or "")
        if filtering and not (
            org_ok(operator)
            or (operator is None and row.get("route") == "in_house" and not filters[2])
        ):
            return None
        fields = [
            ("name", _W_TITLE, row.get("name") or f"{row.get('make', '')} {row.get('model', '')}"),
            ("make", _W_ID, f"{row.get('make', '')} {row.get('model', '')}"),
            *_org_fields(operator, "operator"),
            ("kind", _W_TEXT, str(row.get("kind", "")).replace("_", " ")),
            ("processes", _W_TEXT, " ".join(row.get("processes") or [])),
            ("place", _W_TEXT, row.get("location", "")),
        ]
        detail = {
            "layer": row.get("layer"),
            "machine_kind": row.get("kind"),
            "operator_org": row.get("operator_org") or None,
        }
        title = row.get("name") or f"{row.get('make', '')} {row.get('model', '')}".strip()
        score, matched = _score(q, fields) if q else (1.0, [])
        if not score:
            return None
        return Hit(kind, row["id"], row.get("name") or title, title, score, matched, detail)
    else:
        if filtering:
            return None
        extra = {
            "material": ("designation", getattr(entry, "designation", "")),
            "appearance": ("finish", getattr(entry, "finish", "")),
            "ergonomic_limit": ("applies to", getattr(entry, "applies_to", "")),
            "process": ("family", getattr(entry, "family", "")),
        }[kind]
        fields = [
            ("name", _W_ID, entry.name),
            ("title", _W_TITLE, entry.title),
            (extra[0], _W_ID if kind == "material" else _W_TEXT, extra[1]),
            ("family", _W_TEXT, getattr(entry, "family", "")),
            ("notes", _W_TEXT, getattr(entry, "notes", "")),
        ]
    score, matched = _score(q, fields) if q else (1.0, [])
    if not score:
        return None
    return Hit(kind, entry.id, entry.name, entry.title or entry.name, score, matched, detail)
