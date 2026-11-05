"""Stable component ids across concept, structured mesh and CAD (SYNC-001, SYNC-002).

Until now a concept part was matched to its CAD by name substring, a GLB node
by its STEP product name and a face by its signature, so renaming anything
broke the thread. A component now has an id, ``CMP-<slug>``, issued the first
time a concept assembly names the part (``twin.record_decision`` with an
``assembly``) and kept from then on:

* on the concept assembly decision itself (``assembly.parts[i].component_id``
  and ``component_identity``, ``{name: id}``);
* on the L1 block mesh, as each node's name and ``extras.component_id``;
* on each CAD revision built for the part (``component_id``, or
  ``part_component_ids`` for an assembly's members). The first match is by
  name, with the same rule the design gate uses; once stored, the id is
  carried to the item's next revisions, so later matching is by id;
* on face semantic tags (``CMP-x::<tag>``), so a face says which component
  made it;
* in STEP product names, ``"<name> [CMP-x]"`` (:func:`stamp_step_component_ids`),
  on a STEP export and on the STEP the OCCT converter tessellates for the
  viewer, which writes the id into each GLB node's ``extras``.

Nothing is renamed and there is no new store: the mapping lives in the twin,
on the records above. A decision recorded before ids existed is read as if
its ids had been issued then (same name, same id), so nothing is backfilled.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.representation import COMPONENT_ID_KEY, PART_COMPONENT_IDS_KEY

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.component_identity")

PREFIX = "CMP-"
#: Metadata key on a concept assembly decision: ``{part name: component id}``.
IDENTITY_KEY = "component_identity"
#: The separator between a component id and a face's own semantic tag.
TAG_SEPARATOR = "::"
_MAX_SLUG = 48


def slug(name: str) -> str:
    """``"Chair Leg (front)"`` to ``"chair-leg-front"``; never empty."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return (s[:_MAX_SLUG].rstrip("-")) or "part"


def name_key(name: str) -> str:
    """The comparison key for part names (the design gate's own rule)."""
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def is_component_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX) and len(value) > len(PREFIX)


def issue_ids(names: Iterable[str], prior: dict[str, str] | None = None) -> dict[str, str]:
    """``{name: component id}`` for ``names``, keeping every id issued before.

    A name seen before (exactly, or with the same comparison key) keeps its
    id. A new name gets ``CMP-<slug>``, with ``-2``, ``-3`` ... when that id
    is already taken by another name.
    """
    known = dict(prior or {})
    by_key = {name_key(n): cid for n, cid in known.items()}
    taken = set(known.values())
    out: dict[str, str] = {}
    for name in names:
        if name in out:
            continue
        cid = known.get(name) or by_key.get(name_key(name))
        if not cid:
            base = PREFIX + slug(name)
            cid, n = base, 2
            while cid in taken:
                cid, n = f"{base}-{n}", n + 1
            taken.add(cid)
            by_key[name_key(name)] = cid
        out[name] = cid
    return out


def match_name(name: str, name_to_id: dict[str, str]) -> str | None:
    """The component a CAD name belongs to, by the design gate's name rule.

    An exact key match wins; otherwise one part whose name is contained in
    the CAD name or contains it ("Leg" covers "Chair Leg 1"). More than one
    candidate names no component in particular, so none is returned.
    """
    key = name_key(name)
    if not key:
        return None
    for part, cid in name_to_id.items():
        if name_key(part) == key:
            return cid
    hits = {
        cid
        for part, cid in name_to_id.items()
        if name_key(part) and (name_key(part) in key or key in name_key(part))
    }
    return hits.pop() if len(hits) == 1 else None


def tag_with_component(component_id: str | None, tag: str | None) -> str | None:
    """A face semantic tag prefixed with its component (``CMP-x::<tag>``)."""
    if not tag or not component_id or tag.startswith(component_id + TAG_SEPARATOR):
        return tag
    return f"{component_id}{TAG_SEPARATOR}{tag}"


def tag_core(tag: str | None) -> str | None:
    """A semantic tag without its component prefix, for comparing tags."""
    if tag and tag.startswith(PREFIX) and TAG_SEPARATOR in tag:
        return tag.split(TAG_SEPARATOR, 1)[1]
    return tag


# ---------------------------------------------------------------------------
# Component ids in STEP product names (SYNC-001, SYNC-002)
# ---------------------------------------------------------------------------

#: A product name that carries its component: ``"Base plate [CMP-base-plate]"``.
#: Any STEP reader keeps a product name, so the id survives a round trip
#: through other CAD tools, and the OCCT converter reads it back into GLB
#: node extras (``tools/occt-converter/component_ids.py`` mirrors this rule).
_STEP_NAME = re.compile(r"^(?P<name>.*?)\s*\[(?P<cid>CMP-[A-Za-z0-9-]+)\]\s*$")
#: ``PRODUCT('id','name',...`` and ``NEXT_ASSEMBLY_USAGE_OCCURRENCE('id','name',...``.
_STEP_ENTITY = re.compile(
    r"(?<![A-Z_])(?P<kind>PRODUCT|NEXT_ASSEMBLY_USAGE_OCCURRENCE)\s*\(\s*"
    r"'(?P<first>(?:[^']|'')*)'\s*,\s*'(?P<second>(?:[^']|'')*)'"
)
#: How OCCT names a product nobody named ("Open CASCADE STEP translator 7.7 1").
_DEFAULT_PRODUCT_PREFIX = "open cascade"


def product_name_with_id(name: str, component_id: str) -> str:
    """``"Base"`` and ``"CMP-base"`` to ``"Base [CMP-base]"``."""
    base = str(name or "").strip()
    found = _STEP_NAME.match(base)
    if found:
        base = found.group("name").strip()
    return f"{base} [{component_id}]" if base else f"[{component_id}]"


def split_product_name(name: str | None) -> tuple[str, str | None]:
    """``"Base [CMP-base]"`` to ``("Base", "CMP-base")``; a plain name has no id."""
    text = str(name or "").strip()
    found = _STEP_NAME.match(text)
    if found:
        return found.group("name").strip() or found.group("cid"), found.group("cid")
    if is_component_id(text) and re.fullmatch(r"CMP-[A-Za-z0-9-]+", text):
        return text, text
    return text, None


def _step_unescape(value: str) -> str:
    return value.replace("''", "'")


def _step_escape(value: str) -> str:
    # Only ASCII travels unencoded in a STEP string; anything else is dropped
    # from the display part (the id itself is always ASCII).
    return value.encode("ascii", "ignore").decode("ascii").replace("'", "''")


def stamp_step_component_ids(
    step: bytes,
    *,
    item_name: str,
    component_id: str | None = None,
    part_component_ids: dict[str, str] | None = None,
) -> tuple[bytes, int]:
    """Write component ids into a STEP's product names. Pure, never raises.

    Returns ``(step, products stamped)``. An assembly's members are matched
    to ``part_component_ids`` by the exact name key (no substring guess, so a
    wrong product is never stamped), on both the ``PRODUCT`` and the
    ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` (instance) names. ``component_id`` goes
    on the product named like the item, or on the only product there is
    (which, when OCCT left its default name, is renamed after the item). A
    name that already carries an id keeps it. Geometry is never touched.
    """
    members: dict[str, str] = {}
    for member, cid in (part_component_ids or {}).items():
        if is_component_id(cid) and name_key(member):
            members[name_key(member)] = str(cid)
    single = component_id if is_component_id(component_id) else None
    if not members and not single:
        return step, 0
    try:
        text = step.decode("latin-1")
    except Exception:  # noqa: BLE001 - not a text STEP: nothing to stamp
        return step, 0
    if "ISO-10303-21" not in text[:200]:
        return step, 0
    product_count = sum(1 for m in _STEP_ENTITY.finditer(text) if m.group("kind") == "PRODUCT")
    item_key = name_key(item_name)
    stamped = 0

    def replace(found: re.Match[str]) -> str:
        nonlocal stamped
        kind, first, second = found.group("kind"), found.group("first"), found.group("second")
        name = _step_unescape(second)
        if split_product_name(name)[1]:
            return found.group(0)
        cid = members.get(name_key(name))
        display = name
        if cid is None and kind == "PRODUCT" and single:
            if name_key(name) == item_key and item_key:
                cid = single
            elif product_count == 1:
                cid = single
                if not name_key(name) or name.lower().startswith(_DEFAULT_PRODUCT_PREFIX):
                    display = item_name
        if cid is None:
            return found.group(0)
        new_name = _step_escape(product_name_with_id(display, cid))
        # A product's id usually repeats its name; keep the two equal.
        new_first = new_name if (kind == "PRODUCT" and first == second) else first
        stamped += 1
        whole, at = found.group(0), found.start(0)
        fs, fe = found.start("first") - at, found.end("first") - at
        ss, se = found.start("second") - at, found.end("second") - at
        return whole[:fs] + new_first + whole[fe:ss] + new_name + whole[se:]

    out = _STEP_ENTITY.sub(replace, text)
    if not stamped:
        return step, 0
    logger.info("step_component_ids_stamped", item=item_name, products=stamped)
    return out.encode("latin-1", errors="replace"), stamped


def step_ids_for_node(meta: dict[str, Any] | None) -> tuple[str | None, dict[str, str]]:
    """``(component_id, {member: id})`` a CAD revision's metadata names."""
    m = meta or {}
    single = m.get(COMPONENT_ID_KEY)
    parts = m.get(PART_COMPONENT_IDS_KEY)
    members = (
        {str(k): str(v) for k, v in parts.items() if is_component_id(v)}
        if isinstance(parts, dict)
        else {}
    )
    return (str(single) if is_component_id(single) else None), members


def stamp_step_for_node(
    step: bytes, *, name: str, meta: dict[str, Any] | None
) -> tuple[bytes, int]:
    """:func:`stamp_step_component_ids` with the ids a CAD revision carries."""
    single, members = step_ids_for_node(meta)
    if not single and not members:
        return step, 0
    with tracer.start_as_current_span("component_identity.stamp_step") as span:
        out, n = stamp_step_component_ids(
            step, item_name=name, component_id=single, part_component_ids=members
        )
        span.set_attribute("component_identity.products_stamped", n)
        return out, n


def _assembly_parts(meta: dict[str, Any]) -> list[dict[str, Any]]:
    assembly = meta.get("assembly")
    if not isinstance(assembly, dict):
        return []
    return [p for p in assembly.get("parts") or [] if isinstance(p, dict) and p.get("name")]


def assign_assembly_ids(
    assembly_meta: dict[str, Any], prior: dict[str, str] | None = None
) -> dict[str, str]:
    """Write ``component_id`` on each part of an assembly's metadata, in place.

    Returns the ``{name: id}`` map for ``component_identity``.
    """
    parts = _assembly_parts({"assembly": assembly_meta})
    ids = issue_ids([str(p["name"]) for p in parts], prior)
    for p in parts:
        p["component_id"] = ids[str(p["name"])]
    return ids


def _created(wp: Any) -> str:
    stamp = getattr(wp, "created_at", None)
    return stamp.isoformat() if stamp is not None else ""


async def concept_assemblies(twin: Any, project_id: UUID | str | None) -> list[Any]:
    """The project's design decisions that carry an assembly, oldest first."""
    from twin_core.models.enums import WorkProductType

    if twin is None or project_id is None:
        return []
    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    try:
        decisions = await twin.list_work_products(
            work_product_type=WorkProductType.DESIGN_DECISION, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no list API reads as no assembly
        logger.warning("component_identity_decisions_unavailable", error=str(exc))
        return []
    out = [d for d in decisions if _assembly_parts(getattr(d, "metadata", None) or {})]
    return sorted(out, key=_created)


async def project_identity(twin: Any, project_id: UUID | str | None) -> dict[str, Any]:
    """Every component the project's concept assemblies have named.

    ``{"name_to_id": {...}, "components": {id: {name, source, quantity,
    decision_node_id, in_current}}, "current_decision_id": str | None}``.
    The newest assembly is the current one; parts it no longer names stay
    known (their ids are never reissued) with ``in_current`` false.
    """
    with tracer.start_as_current_span("twin.component_identity.project") as span:
        assemblies = await concept_assemblies(twin, project_id)
        name_to_id: dict[str, str] = {}
        components: dict[str, dict[str, Any]] = {}
        current_id: str | None = None
        current: set[str] = set()
        for decision in assemblies:
            meta = getattr(decision, "metadata", None) or {}
            parts = _assembly_parts(meta)
            stored = {
                str(p["name"]): str(p["component_id"])
                for p in parts
                if is_component_id(p.get("component_id"))
            }
            ids = issue_ids([str(p["name"]) for p in parts], {**name_to_id, **stored})
            name_to_id.update(ids)
            current = set()
            for p in parts:
                cid = ids[str(p["name"])]
                current.add(cid)
                components[cid] = {
                    "component_id": cid,
                    "name": str(p["name"]),
                    "source": str(p.get("source") or "made"),
                    "quantity": p.get("quantity", 1),
                    "decision_node_id": str(getattr(decision, "id", "")),
                }
            current_id = str(getattr(decision, "id", ""))
        for cid, comp in components.items():
            comp["in_current"] = cid in current
        span.set_attribute("component_identity.components", len(components))
        return {
            "name_to_id": name_to_id,
            "components": components,
            "current_decision_id": current_id,
        }


def _member_names(meta: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for member in meta.get("parts") or meta.get("components") or []:
        if isinstance(member, dict) and member.get("name"):
            names.append(str(member["name"]))
        elif isinstance(member, str):
            names.append(member)
    return names


def ids_for_cad(
    name: str, meta: dict[str, Any], name_to_id: dict[str, str], prior_meta: dict[str, Any] | None
) -> dict[str, Any]:
    """The component id fields a CAD revision should carry (possibly none).

    An id already on the revision or on its prior revision is kept: matching
    by name happens once, then the id travels with the item.
    """
    out: dict[str, Any] = {}
    prior = prior_meta or {}
    cid = meta.get(COMPONENT_ID_KEY) or prior.get(COMPONENT_ID_KEY) or match_name(name, name_to_id)
    if is_component_id(cid):
        out[COMPONENT_ID_KEY] = cid
    members: dict[str, str] = {}
    known = meta.get(PART_COMPONENT_IDS_KEY) or prior.get(PART_COMPONENT_IDS_KEY) or {}
    for member in _member_names(meta):
        mid = known.get(member) if isinstance(known, dict) else None
        mid = mid or match_name(member, name_to_id)
        if is_component_id(mid):
            members[member] = str(mid)
    if members:
        out[PART_COMPONENT_IDS_KEY] = members
    return out


async def stamp_cad_revision(
    twin: Any,
    *,
    node_id: UUID,
    name: str,
    project_id: UUID | str | None,
    prior_node_id: UUID | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record the component id(s) on one CAD revision. Never raises.

    Returns the fields written (empty when nothing maps or nothing changed).
    """
    from twin_core.geometry_state import set_node_metadata

    try:
        node = await twin.graph.get_node(node_id)
        if node is None:
            return {}
        meta = dict(getattr(node, "metadata", None) or {})
        prior_meta: dict[str, Any] | None = None
        if prior_node_id is not None:
            prior = await twin.graph.get_node(prior_node_id)
            prior_meta = dict(getattr(prior, "metadata", None) or {}) if prior else None
        ident = identity if identity is not None else await project_identity(twin, project_id)
        fields = ids_for_cad(name, meta, ident.get("name_to_id") or {}, prior_meta)
        changed = {k: v for k, v in fields.items() if meta.get(k) != v}
        if changed:
            await set_node_metadata(twin, node, **changed)
            logger.info(
                "component_id_stamped",
                node_id=str(node_id),
                name=name,
                component_id=changed.get(COMPONENT_ID_KEY),
                members=len(changed.get(PART_COMPONENT_IDS_KEY) or {}),
            )
        return changed
    except Exception as exc:  # noqa: BLE001 - an id is never worth failing a commit
        logger.warning("component_id_stamp_failed", node_id=str(node_id), error=str(exc))
        return {}


async def stamp_project_cad(twin: Any, project_id: UUID | str | None) -> int:
    """Record component ids on every CAD item head of a project. Never raises.

    Called after a concept assembly is recorded, so CAD that already existed
    gets its component. Returns how many revisions were stamped.
    """
    from twin_core.items import list_items, supports_items

    if twin is None or project_id is None or not supports_items(twin):
        return 0
    stamped = 0
    try:
        ident = await project_identity(twin, project_id)
        if not ident["name_to_id"]:
            return 0
        for item in await list_items(twin, project_id=project_id, item_type="cad_model"):
            if item.head_node_id is None:
                continue
            fields = await stamp_cad_revision(
                twin,
                node_id=item.head_node_id,
                name=item.name,
                project_id=project_id,
                identity=ident,
            )
            stamped += 1 if fields else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("component_id_project_stamp_failed", error=str(exc))
    return stamped


__all__ = [
    "IDENTITY_KEY",
    "PREFIX",
    "TAG_SEPARATOR",
    "assign_assembly_ids",
    "concept_assemblies",
    "ids_for_cad",
    "is_component_id",
    "issue_ids",
    "match_name",
    "name_key",
    "product_name_with_id",
    "project_identity",
    "slug",
    "split_product_name",
    "stamp_cad_revision",
    "stamp_project_cad",
    "stamp_step_component_ids",
    "stamp_step_for_node",
    "step_ids_for_node",
    "tag_core",
    "tag_with_component",
]
