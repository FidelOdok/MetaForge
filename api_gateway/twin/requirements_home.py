"""One home for requirements: the constraint set (FORGE-528, epic FORGE-521).

Requirements used to be stored three times: as a table in a free-standing
``prd`` document, as Constraint nodes in a ``constraint_set``, and restated in
the "verifiable requirements" design decision. The three drifted. Now:

- The **constraint set item** is the only source of requirement values. Its
  current revision (``CS-KEY@n``) is what the gates, the Requirements page and
  the prd read.
- The **prd** a reader sees is *derived* (:func:`render_prd`): the prd prose
  item (background, scope, non-requirements) plus the current intent, needs,
  objectives and constraint set, rendered to markdown on read, each part
  labelled with the ``KEY@n`` it came from.
- A **prd write** (``twin.record_document`` with ``document_type='prd'``)
  records only the prose, as the next revision of the project's prd item
  (:func:`record_prd_prose`). Requirement values in the text that the current
  constraint set does not have are returned as a warning, never dropped and
  never refused.

Reads see what everyone else sees: approved heads. A design-flow run reads
its own drafts over the heads (FORGE-525), so a phase that just recorded a
constraint set and then a prd checks the prd against its own draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.requirements_home")

#: Where requirement values live, said in every prd write result.
REQUIREMENTS_HOME = (
    "Requirement values live in the project's constraint set "
    "(twin.record_constraint_set); the prd stores only prose (background, scope, "
    "non-requirements). The prd you read is rendered from that prose plus the "
    "current intent, needs, objectives and constraint set revision "
    "(GET /v1/twin/projects/{project_id}/prd)."
)

#: Prose kept on the node itself so the derived view needs no blob fetch.
_PROSE_METADATA_MAX = 50_000
_MAX_STRAY_REPORTED = 20

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-requirements")
    return _metrics


# ---------------------------------------------------------------------------
# Requirement values in free text
# ---------------------------------------------------------------------------

#: Unit spellings a requirement value is written with, mapped to one form.
_UNIT_ALIASES: dict[str, str] = {
    "grams": "g",
    "gram": "g",
    "kilograms": "kg",
    "kilogram": "kg",
    "millimetres": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimeter": "mm",
    "watts": "w",
    "watt": "w",
    "volts": "v",
    "volt": "v",
    "newtons": "n",
    "newton": "n",
    "°c": "c",
    "degc": "c",
    "deg c": "c",
    "seconds": "s",
    "sec": "s",
    "hours": "h",
    "hr": "h",
    "hrs": "h",
    "minutes": "min",
    "lbs": "lb",
    "n·m": "nm",
    "n-m": "nm",
    "usd": "$",
}
_UNITS = sorted(
    {
        "kg",
        "g",
        "mg",
        "mm",
        "cm",
        "m",
        "km",
        "um",
        "µm",
        "ft",
        "lb",
        "lbs",
        "oz",
        "kn",
        "n",
        "nm",
        "n·m",
        "n-m",
        "mpa",
        "gpa",
        "kpa",
        "pa",
        "psi",
        "w",
        "mw",
        "kw",
        "v",
        "mv",
        "a",
        "ma",
        "ah",
        "mah",
        "wh",
        "hz",
        "khz",
        "mhz",
        "ghz",
        "ms",
        "s",
        "sec",
        "min",
        "h",
        "hr",
        "hrs",
        "°c",
        "degc",
        "k",
        "%",
        "db",
        "rpm",
        "deg",
        "°",
        "usd",
        "grams",
        "gram",
        "kilograms",
        "kilogram",
        "millimetres",
        "millimeters",
        "millimetre",
        "millimeter",
        "watts",
        "watt",
        "volts",
        "volt",
        "newtons",
        "newton",
        "seconds",
        "hours",
        "minutes",
    },
    key=len,
    reverse=True,
)
_UNIT_PATTERN = "|".join(re.escape(u) for u in _UNITS)
_VALUE_RE = re.compile(
    rf"(?<![\w.@-])(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PATTERN})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"\$\s?(?P<num>\d+(?:\.\d+)?)(?!\d|\.\d)")
_BARE_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\d)")


def _norm_unit(unit: str) -> str:
    u = unit.strip().lower()
    return _UNIT_ALIASES.get(u, u)


@dataclass(frozen=True)
class RequirementValue:
    """One quantity written in text: ``15 g`` -> (15.0, "g", "15 g")."""

    number: float
    unit: str
    text: str


def extract_values(text: str) -> list[RequirementValue]:
    """Numbers written with a unit (or as money), in order of appearance."""
    found: list[RequirementValue] = []
    for m in _VALUE_RE.finditer(text or ""):
        found.append(
            RequirementValue(float(m.group("num")), _norm_unit(m.group("unit")), m.group(0).strip())
        )
    for m in _MONEY_RE.finditer(text or ""):
        found.append(RequirementValue(float(m.group("num")), "$", m.group(0).strip()))
    return found


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


@dataclass
class _KnownValues:
    """The values a constraint set states, with and without units."""

    with_unit: list[tuple[float, str]] = field(default_factory=list)
    bare: list[float] = field(default_factory=list)

    def add_constraint(self, c: Any) -> None:
        limit = getattr(c, "limit", None)
        unit = _norm_unit(str(getattr(c, "unit", "") or ""))
        if isinstance(limit, (int, float)):
            if unit:
                self.with_unit.append((float(limit), unit))
            else:
                self.bare.append(float(limit))
        for attr in ("message", "acceptance_criteria", "name"):
            text = str(getattr(c, attr, "") or "")
            self.with_unit.extend((v.number, v.unit) for v in extract_values(text))
        # A generated expression carries the limit as a bare number.
        for m in _BARE_NUMBER_RE.finditer(str(getattr(c, "expression", "") or "")):
            self.bare.append(float(m.group(0)))

    def has(self, value: RequirementValue) -> bool:
        if any(_close(n, value.number) and u == value.unit for n, u in self.with_unit):
            return True
        return any(_close(n, value.number) for n in self.bare)


def stray_values(text: str, constraints: list[Any]) -> tuple[list[str], int]:
    """Values in ``text`` no constraint states, and how many values matched."""
    known = _KnownValues()
    for c in constraints:
        known.add_constraint(c)
    stray: list[str] = []
    matched = 0
    seen: set[tuple[float, str]] = set()
    for value in extract_values(text):
        ident = (value.number, value.unit)
        if ident in seen:
            continue
        seen.add(ident)
        if known.has(value):
            matched += 1
        else:
            stray.append(value.text)
    return stray, matched


# ---------------------------------------------------------------------------
# Current revisions
# ---------------------------------------------------------------------------


@dataclass
class CurrentRevision:
    """One item as a reader sees it now."""

    key: str
    item_type: str
    revision: int
    node_id: UUID
    name: str

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.revision}"


async def current_revisions(
    twin: Any, project_id: UUID | None, item_type: str, *, run_id: str | None = None
) -> list[CurrentRevision]:
    """Every item of ``item_type`` in the project at its current revision.

    Approved heads, or for a read inside ``run_id`` that run's own drafts
    over the heads (FORGE-525). Empty when the twin cannot hold items.
    """
    from twin_core.items import list_items, supports_items, visible_head

    if project_id is None or not supports_items(twin):
        return []
    items = await list_items(twin, project_id, item_type, include_unheaded=bool(run_id))
    out: list[CurrentRevision] = []
    for item in items:
        seen = visible_head(item, run_id)
        if seen is None:
            continue
        out.append(CurrentRevision(item.key, item.item_type, seen[0], seen[1], item.name))
    return out


@dataclass
class CurrentRequirements:
    """The project's requirements as of now, grouped by the revision that holds them."""

    #: (constraint set revision, its Constraint nodes), ordered by item key.
    sets: list[tuple[CurrentRevision, list[Any]]] = field(default_factory=list)
    #: Constraints recorded outside any item (no project at write time, the
    #: dashboard editor, or written before items existed).
    unversioned: list[Any] = field(default_factory=list)

    def all(self) -> list[Any]:
        return [c for _, cs in self.sets for c in cs] + list(self.unversioned)

    def ref_for(self, constraint_id: UUID) -> str | None:
        for rev, cs in self.sets:
            if any(c.id == constraint_id for c in cs):
                return rev.ref
        return None


async def current_requirements(
    twin: Any, project_id: UUID | None, *, run_id: str | None = None
) -> CurrentRequirements:
    """The project's current requirements: one home, the constraint set.

    A Constraint belongs to the ``constraint_set`` work product it arrived in
    (``metadata.constraint_set_wp``). It is current when that work product is
    the current revision of its item. Constraints of an older revision, or of
    a draft another run has not had approved, are left out, so the reader
    never sees two values for one requirement.
    """
    from twin_core.items import item_for_node, supports_items

    with tracer.start_as_current_span("twin.requirements.current") as span:
        constraints = [
            c
            for c in await twin.list_constraints(project_id=project_id)
            if not (c.metadata or {}).get("candidate")
        ]
        heads = await current_revisions(twin, project_id, "constraint_set", run_id=run_id)
        by_node = {h.node_id: h for h in heads}
        grouped: dict[UUID, list[Any]] = {h.node_id: [] for h in heads}
        result = CurrentRequirements()
        itemized_cache: dict[UUID, bool] = {}
        for c in constraints:
            raw = (c.metadata or {}).get("constraint_set_wp")
            try:
                set_id = UUID(str(raw)) if raw else None
            except ValueError:
                set_id = None
            if set_id is not None and set_id in by_node:
                grouped[set_id].append(c)
                continue
            if set_id is not None and supports_items(twin):
                if set_id not in itemized_cache:
                    itemized_cache[set_id] = await item_for_node(twin, set_id) is not None
                if itemized_cache[set_id]:
                    continue  # an older revision or someone else's draft
            result.unversioned.append(c)
        result.sets = [(h, grouped[h.node_id]) for h in sorted(heads, key=lambda h: h.key)]
        span.set_attribute("requirements.sets", len(result.sets))
        span.set_attribute("requirements.count", len(result.all()))
        return result


# ---------------------------------------------------------------------------
# The derived prd
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _requirement_rows(ref: str, constraints: list[Any]) -> list[str]:
    rows = []
    for c in sorted(constraints, key=lambda c: c.name):
        acceptance = c.acceptance_criteria or c.message
        rows.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    ref,
                    c.name,
                    c.metric,
                    c.operator if c.metric or c.limit is not None else "",
                    c.limit,
                    c.unit,
                    c.verification_method,
                    acceptance,
                )
            )
            + " |"
        )
    return rows


_TABLE_HEADER = [
    "| Ref | Requirement | Metric | Operator | Limit | Unit | Verification method "
    "| Acceptance criteria |",
    "|---|---|---|---|---|---|---|---|",
]


async def _entity_lines(
    twin: Any, project_id: UUID | None, entity_type: str, run_id: str | None
) -> tuple[list[str], list[dict[str, Any]]]:
    revs = await current_revisions(twin, project_id, entity_type, run_id=run_id)
    lines: list[str] = []
    sources: list[dict[str, Any]] = []
    entities: list[tuple[str | None, Any]] = []
    if revs:
        for rev in revs:
            node = await twin.graph.get_node(rev.node_id)
            if node is not None:
                entities.append((rev.ref, node))
                sources.append(
                    {"ref": rev.ref, "item_type": entity_type, "node_id": str(rev.node_id)}
                )
    elif project_id is not None:
        # Recorded before items existed: no revision to cite, still shown.
        for e in await twin.list_engineering_entities(
            project_id=project_id, entity_type=entity_type
        ):
            entities.append((None, e))
    for ref, e in entities:
        title = getattr(e, "title", None) or ""
        statement = getattr(e, "statement", None) or ""
        text = f"**{title}**: {statement}" if title and statement else (statement or title)
        meta = getattr(e, "metadata", None) or {}
        if entity_type == "objective":
            target = " ".join(
                str(meta[k]) for k in ("metric", "direction", "target", "unit") if meta.get(k)
            )
            if target:
                text += f" ({target})"
        lines.append(f"- {text}" + (f" `{ref}`" if ref else ""))
    return lines, sources


def _prose_of(node: Any) -> str:
    meta = getattr(node, "metadata", None) or {}
    prose = meta.get("prose")
    if isinstance(prose, str) and prose.strip():
        return prose
    key = meta.get("minio_object_key")
    if key:
        try:
            from digital_twin.storage.work_product_blobs import fetch_work_product_blob

            blob = fetch_work_product_blob(str(key))
            if blob:
                return bytes(blob).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 -- the view still renders without prose
            logger.warning(
                "prd_prose_fetch_failed", node_id=str(getattr(node, "id", "")), error=str(exc)
            )
    return ""


async def render_prd(
    twin: Any,
    project_id: UUID | None,
    *,
    prd_ref: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The prd as a reader sees it: prose plus the live requirements.

    ``prd_ref`` (``KEY`` or ``KEY@n``) picks the prose revision; by default
    the project's current prd item. Intent, needs, objectives and
    requirements are always the current revisions: the prd never carries an
    older copy of a requirement.
    """
    from twin_core.items import resolve_item_ref

    with tracer.start_as_current_span("twin.prd.render") as span:
        sources: list[dict[str, Any]] = []
        prose_rev: CurrentRevision | None = None
        if prd_ref:
            item, rev = await resolve_item_ref(twin, prd_ref, project_id, run_id=run_id)
            if item.item_type != "prd":
                from twin_core.items import ItemError

                raise ItemError(f"{item.key} is a {item.item_type} item, not a prd")
            if project_id is None:
                project_id = item.project_id
            prose_rev = CurrentRevision(item.key, "prd", rev.revision, rev.node_id, item.name)
        else:
            prds = await current_revisions(twin, project_id, "prd", run_id=run_id)
            prose_rev = prds[0] if prds else None

        title = "Product requirements"
        prose = ""
        if prose_rev is not None:
            node = await twin.graph.get_node(prose_rev.node_id)
            if node is not None:
                title = getattr(node, "name", None) or prose_rev.name or title
                prose = _prose_of(node)
            sources.append(
                {"ref": prose_rev.ref, "item_type": "prd", "node_id": str(prose_rev.node_id)}
            )

        reqs = await current_requirements(twin, project_id, run_id=run_id)
        lines = [f"# {title}", ""]
        refs = [s["ref"] for s in sources] + [rev.ref for rev, _ in reqs.sets]
        lines += [
            "> Rendered from the twin. Prose: "
            + (f"`{prose_rev.ref}`" if prose_rev else "none recorded")
            + ". Requirements: "
            + (", ".join(f"`{rev.ref}`" for rev, _ in reqs.sets) or "no constraint set yet")
            + ". Requirement values are edited in the constraint set, not here.",
            "",
        ]
        if prose.strip():
            lines += ["## Background and scope", "", prose.strip(), ""]

        for heading, entity_type in (
            ("Intent", "intent"),
            ("Stakeholder needs", "stakeholder_need"),
            ("Objectives", "objective"),
        ):
            entity_lines, entity_sources = await _entity_lines(
                twin, project_id, entity_type, run_id
            )
            sources += entity_sources
            if entity_lines:
                lines += [f"## {heading}", "", *entity_lines, ""]

        lines += ["## Requirements", ""]
        if reqs.sets or reqs.unversioned:
            lines += _TABLE_HEADER
            for rev, constraints in reqs.sets:
                lines += _requirement_rows(rev.ref, constraints)
                sources.append(
                    {
                        "ref": rev.ref,
                        "item_type": "constraint_set",
                        "node_id": str(rev.node_id),
                        "name": rev.name,
                        "requirement_count": len(constraints),
                    }
                )
            lines += _requirement_rows("", reqs.unversioned)
        else:
            lines.append(
                "No requirements recorded yet. Record them with twin.record_constraint_set."
            )
        lines.append("")

        markdown = "\n".join(lines)
        count = len(reqs.all())
        span.set_attribute("prd.requirement_count", count)
        span.set_attribute("prd.sources", len(sources))
        logger.info(
            "prd_rendered",
            project_id=str(project_id) if project_id else None,
            prose_ref=prose_rev.ref if prose_rev else None,
            requirement_sets=[rev.ref for rev, _ in reqs.sets],
            requirement_count=count,
        )
        return {
            "project_id": str(project_id) if project_id else None,
            "title": title,
            "markdown": markdown,
            "prose_ref": prose_rev.ref if prose_rev else None,
            "requirement_refs": [rev.ref for rev, _ in reqs.sets],
            "requirement_count": count,
            "refs": refs,
            "sources": sources,
        }


# ---------------------------------------------------------------------------
# A prd write records prose
# ---------------------------------------------------------------------------


async def record_prd_prose(
    twin: Any,
    record_raw: Any,
    *,
    content: str,
    name: str,
    project_id: str | None,
    extra_metadata: dict[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Record a prd write as the next revision of the project's prd prose item.

    ``record_raw`` is the plain document recorder. The text is stored as
    given (nothing is dropped); values in it that the current constraint set
    does not state are listed in ``requirement_warning`` so the caller can
    move them into ``twin.record_constraint_set``.
    """
    from api_gateway.twin.item_revisions import (
        finish_definition_revision,
        plan_definition_revision,
        revision_run_id,
    )

    pid = UUID(project_id) if project_id else None
    run_id = revision_run_id()
    with tracer.start_as_current_span("twin.record_prd") as span:
        # One prd per project: a re-recorded prd under a new title is still
        # the next revision of the project's prd, not a second prd.
        existing = await current_revisions(twin, pid, "prd", run_id=run_id)
        item_key = existing[0].key if len(existing) == 1 else None
        plan = await plan_definition_revision(
            twin,
            item_type="prd",
            name=name,
            project_id=project_id,
            default_author=str(kwargs.get("source_tool") or "twin.record_document"),
            item_key=item_key,
        )
        reqs = await current_requirements(twin, pid, run_id=run_id)
        stray, matched = stray_values(content, reqs.all())
        span.set_attribute("prd.stray_values", len(stray))
        span.set_attribute("prd.matched_values", matched)

        metadata: dict[str, Any] = dict(extra_metadata or {})
        metadata["prd_part"] = "prose"
        metadata["prose"] = content[:_PROSE_METADATA_MAX]
        if len(content) > _PROSE_METADATA_MAX:
            metadata["prose_truncated"] = True
        metadata["requirement_refs"] = [rev.ref for rev, _ in reqs.sets]
        if stray:
            metadata["stray_requirement_values"] = stray[:_MAX_STRAY_REPORTED]
        if plan is not None:
            metadata.update(plan.stamp())

        result: dict[str, Any] = await record_raw(
            content=content,
            name=name,
            project_id=project_id,
            extra_metadata=metadata,
            **kwargs,
        )
        node_id = result.get("node_id")
        if node_id:
            await finish_definition_revision(
                twin, plan, UUID(str(node_id)), name=name, result=result
            )
        result["requirements_home"] = REQUIREMENTS_HOME.replace(
            "{project_id}", project_id or "{project_id}"
        )
        result["requirement_refs"] = [rev.ref for rev, _ in reqs.sets]
        if stray:
            shown = stray[:_MAX_STRAY_REPORTED]
            more = f" (and {len(stray) - len(shown)} more)" if len(stray) > len(shown) else ""
            target = ", ".join(result["requirement_refs"]) or "no constraint set yet"
            result["stray_requirement_values"] = shown
            result["requirement_warning"] = (
                f"The prd text states {len(stray)} requirement value(s) that the current "
                f"constraint set ({target}) does not: {', '.join(shown)}{more}. The prose was "
                "saved as written, but the prd shows requirement values only from the "
                "constraint set, so the gates never see these. If they are requirements, "
                "record them with twin.record_constraint_set."
            )
        collector = _collector()
        collector.record_twin_prd_requirement_value("stray", len(stray))
        collector.record_twin_prd_requirement_value("matched", matched)
        log = logger.warning if stray else logger.info
        log(
            "prd_stray_requirement_values" if stray else "prd_prose_recorded",
            node_id=node_id,
            project_id=project_id,
            item_ref=result.get("item_ref"),
            requirement_refs=result["requirement_refs"],
            stray=stray[:_MAX_STRAY_REPORTED],
            matched=matched,
        )
        return result
