"""The manufacturing release checklist (3D-RC-016, DFM-016).

A revision's manufacturing package cannot be released while required DFM
evidence, physical verification or approvals are unresolved. This module
reads the twin and says, item by item, what is resolved and what blocks:

``components_exact``
    Every required component is at least ``Exact`` (a validated exact B-Rep;
    the per-component maturity of C3D slice 2).
``dfm``
    Every required component made by a process with exact checks has a
    current exact DFM analysis that passed, or whose every open finding a
    person accepted (``keep_design``, with who and when). A stale, pending or
    incomplete analysis blocks.
``verification``
    Every physical or supplier verification item is satisfied: evidence a
    person recorded and a reviewer approved, for the current revision.
``approvals``
    An approved build or manufacturing release approval is on record.
``step_round_trip``
    Every CAD item's head is validated with a round-trip-passed STEP.

**Applies** only when the project is made by a process whose profile has
exact checks (today FDM) or has an exact DFM analysis; for anything else
:func:`manufacturing_checklist` returns ``None`` and the release gate keeps
its earlier checks. :func:`readiness` turns a checklist into what Create
shows: ready for prototype (geometry, DFM and STEP resolved), ready for
production (everything), or the missing items as next steps. Never a
percentage, never a completion it cannot back (section 16.2 item 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.dfm.release_checklist")

ItemStatus = Literal["pass", "fail", "not_applicable"]

#: Items a prototype needs; production needs every item.
PROTOTYPE_ITEMS: tuple[str, ...] = ("components_exact", "dfm", "step_round_trip")
ITEM_ORDER: tuple[str, ...] = (
    "components_exact",
    "dfm",
    "verification",
    "approvals",
    "step_round_trip",
)
ITEM_LABELS: dict[str, str] = {
    "components_exact": "Every required component has a validated exact model",
    "dfm": "Manufacturing analysis passed, or its issues accepted by a person",
    "verification": "Physical and supplier verification recorded and approved",
    "approvals": "Manufacturing release approved",
    "step_round_trip": "STEP export of each CAD revision passed its round trip",
}
#: The release gate's own release approval check covers ``approvals``.
GATE_ITEMS: tuple[str, ...] = ("components_exact", "dfm", "verification", "step_round_trip")

APPROVED_AUTHORITIES = frozenset({"approved", "baselined"})


@dataclass
class ChecklistItem:
    id: str
    label: str
    status: ItemStatus
    detail: str
    #: One plain line per thing still missing.
    blockers: list[str] = field(default_factory=list)
    #: The Create next step for each blocker (section 10 wording).
    next_steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "blockers": list(self.blockers),
            "next_steps": list(self.next_steps),
        }


@dataclass
class ManufacturingChecklist:
    project_id: str
    items: list[ChecklistItem]
    #: ``KEY@n`` revisions the checklist read: the product revision.
    revision_refs: list[str] = field(default_factory=list)

    def item(self, item_id: str) -> ChecklistItem:
        return next(i for i in self.items if i.id == item_id)

    @property
    def blockers(self) -> list[str]:
        return [b for i in self.items if i.status == "fail" for b in i.blockers]

    @property
    def releasable(self) -> bool:
        return all(i.status != "fail" for i in self.items)

    def gate_blockers(self) -> list[str]:
        return [
            b for i in self.items if i.id in GATE_ITEMS and i.status == "fail" for b in i.blockers
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "releasable": self.releasable,
            "blockers": self.blockers,
            "items": [i.as_dict() for i in self.items],
            "revision_refs": list(self.revision_refs),
            "readiness": readiness(self),
        }


def _wp_type(node: Any) -> str:
    t = getattr(node, "type", None)
    return str(getattr(t, "value", t) or "")


def _authority(entity: Any) -> str:
    a = getattr(entity, "authority", None)
    return str(getattr(a, "value", a) or "")


def _approvals_detail(approved: list[Any]) -> str:
    """The approvals item's detail: who approved, and for whom when an agent did.

    FORGE-834: an approval a delegated agent made reads "approved by agent X
    on behalf of Y", never as the person's own approval.
    """
    if not approved:
        return "no approved release approval"
    out = f"{len(approved)} release approval(s) approved"
    delegated = []
    for a in approved:
        meta = getattr(a, "metadata", None) or {}
        agent, person = meta.get("agent"), meta.get("on_behalf_of")
        if meta.get("approval_route") == "delegated" and agent and person:
            delegated.append(f"approved by agent {agent} on behalf of {person}")
    if delegated:
        out += ": " + "; ".join(delegated)
    return out


def _accepted(finding: dict[str, Any]) -> bool:
    d = finding.get("decision")
    return isinstance(d, dict) and d.get("decision") == "keep_design" and bool(d.get("by"))


def evaluate_dfm_item(
    required: list[dict[str, Any]], sets: dict[str, tuple[dict[str, Any], str]]
) -> ChecklistItem:
    """The ``dfm`` item from required components and their current sets.

    ``sets``: component id -> (finding set body with decisions merged into
    its findings, staleness). Pure.
    """
    from twin_core.dfm.profiles import PROFILES

    blockers: list[str] = []
    steps: list[str] = []
    passed: list[str] = []
    not_applicable: list[str] = []
    for comp in required:
        cid, name = comp["component_id"], comp["name"]
        entry = sets.get(cid)
        if entry is None:
            blockers.append(f"{name}: manufacturing not assessed")
            steps.append(f"Manufacturing not assessed for {name}: choose how to make it")
            continue
        fs, staleness = entry
        profile = PROFILES.get(str(fs.get("profile_id") or ""))
        if profile is None:
            blockers.append(f"{name}: the process is not chosen or has no rule profile")
            steps.append(f"Choose how to make {name}")
            continue
        if not profile.exact_checks:
            not_applicable.append(f"{name} ({profile.process}: no exact checks yet)")
            continue
        if fs.get("kind") != "exact":
            if fs.get("exact_checks_scheduled"):
                blockers.append(f"{name}: manufacturing analysis pending")
                steps.append(f"Manufacturing analysis pending for {name}")
            else:
                blockers.append(f"{name}: no exact manufacturing analysis of a validated model")
                steps.append(f"Engineering Update: validation pending for {name}")
            continue
        if staleness not in ("current", "revalidated"):
            blockers.append(f"{name}: the manufacturing analysis is {staleness}")
            steps.append(f"Recheck manufacturing for {name}: the design changed")
            continue
        status = str(fs.get("status") or "")
        if status == "analysis_passed":
            passed.append(name)
            continue
        if status == "issues_found":
            open_ = [f for f in fs.get("findings") or [] if not _accepted(f)]
            if not open_:
                passed.append(f"{name} (issues accepted)")
                continue
            blocking = sum(1 for f in open_ if f.get("severity") == "blocking")
            blockers.append(
                f"{name}: {len(open_)} manufacturing issue(s) open"
                + (f", {blocking} blocking" if blocking else "")
            )
            steps.append(f"Resolve or accept {len(open_)} manufacturing issue(s) on {name}")
            continue
        gaps = [
            f"{str(n.get('title') or n.get('rule_id') or '').lower()}: {n.get('reason') or ''}"
            for n in fs.get("not_assessed") or []
        ]
        reasons = [str(n.get("reason") or "") for n in fs.get("not_assessed") or []]
        blockers.append(
            f"{name}: manufacturing analysis incomplete"
            + (f" ({'; '.join(g for g in gaps if g)[:200]})" if gaps else "")
        )
        steps.append(
            f"Manufacturing analysis incomplete for {name}"
            + (f": {reasons[0][:120]}" if reasons and reasons[0] else "")
        )
    if blockers:
        status_: ItemStatus = "fail"
        detail = "; ".join(blockers)
    elif passed:
        status_ = "pass"
        detail = "passed: " + ", ".join(passed)
        if not_applicable:
            detail += "; not applicable: " + ", ".join(not_applicable)
    else:
        status_ = "not_applicable"
        detail = "no exact checks apply: " + ", ".join(not_applicable)
    return ChecklistItem(
        id="dfm",
        label=ITEM_LABELS["dfm"],
        status=status_,
        detail=detail,
        blockers=blockers,
        next_steps=steps,
    )


def resolved_components(components: list[dict[str, Any]]) -> list[str]:
    """One plain line per concept component a decision retired or realised. Pure."""
    out: list[str] = []
    for c in components:
        res = c.get("resolution")
        if not isinstance(res, dict) or c.get("required"):
            continue
        if res.get("action") == "retired":
            out.append(f"{c['name']} retired ({str(res.get('reason') or '')[:120]})")
        elif res.get("action") == "realised_by":
            out.append(f"{c['name']} realised by {', '.join(res.get('item_keys') or [])}")
    return out


def readiness(checklist: ManufacturingChecklist | None) -> dict[str, Any]:
    """What Create may claim: ``prototype``, ``production`` or neither, and why not."""
    if checklist is None:
        return {
            "level": "not_assessed",
            "label": "Manufacturing not assessed",
            "prototype": False,
            "production": False,
            "next_steps": [],
        }
    by_id = {i.id: i for i in checklist.items}
    proto = all(by_id[i].status != "fail" for i in PROTOTYPE_ITEMS if i in by_id)
    production = proto and checklist.releasable
    steps = [s for i in checklist.items if i.status == "fail" for s in i.next_steps]
    if production:
        level, label = "production", "Ready for production"
    elif proto:
        level, label = "prototype", "Ready for prototype"
    else:
        level, label = "in_progress", "Not ready to make yet"
    return {
        "level": level,
        "label": label,
        "prototype": proto,
        "production": production,
        "next_steps": list(dict.fromkeys(steps)),
    }


async def _current_sets(twin: Any, pid: UUID) -> tuple[dict[str, tuple[dict[str, Any], str]], bool]:
    """Each component's current finding set, and whether any profile has exact checks."""
    from twin_core.consistency.record_pins import VALID_STATUSES, record_staleness
    from twin_core.dfm.profiles import PROFILES
    from twin_core.models.enums import WorkProductType

    try:
        nodes = await twin.list_work_products(
            work_product_type=WorkProductType.DFM_FINDING_SET, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no list API reads as no sets
        logger.debug("checklist_dfm_sets_unavailable", error=str(exc))
        return {}, False
    nodes = sorted(nodes, key=lambda n: n.created_at.isoformat() if n.created_at else "")
    current: dict[str, tuple[dict[str, Any], str]] = {}
    newest: dict[str, tuple[dict[str, Any], str]] = {}
    applies = False
    for node in nodes:
        meta = dict(getattr(node, "metadata", None) or {})
        body = meta.get("dfm_finding_set")
        if not isinstance(body, dict) or not body.get("component_id"):
            continue
        decisions = meta.get("dfm_decisions") or {}
        findings = []
        for f in body.get("findings") or []:
            row = dict(f)
            if isinstance(decisions, dict) and row.get("id") in decisions:
                row["decision"] = decisions[row["id"]]
            findings.append(row)
        body = {**body, "findings": findings}
        staleness = record_staleness(meta)
        cid = str(body["component_id"])
        profile = PROFILES.get(str(body.get("profile_id") or ""))
        if body.get("kind") == "exact" or (profile is not None and profile.exact_checks):
            applies = True
        newest[cid] = (body, staleness)
        if staleness in VALID_STATUSES:
            current[cid] = (body, staleness)
    # A component whose only sets are stale shows its newest one, as stale.
    for cid, entry in newest.items():
        current.setdefault(cid, entry)
    return current, applies


async def manufacturing_checklist(
    twin: Any, project_id: UUID | str
) -> ManufacturingChecklist | None:
    """The checklist for the project's current revision; ``None`` when it does not apply."""
    from twin_core.geometry_state import exact_cad_readiness
    from twin_core.maturity import Maturity, compute_maturity, rank

    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    with tracer.start_as_current_span("twin.dfm.release_checklist") as span:
        span.set_attribute("project.id", str(pid))
        sets, applies = await _current_sets(twin, pid)
        if not applies:
            span.set_attribute("checklist.applies", False)
            return None
        maturity = await compute_maturity(twin, pid)
        comps = [c for c in maturity["components"] if c.get("required")]
        # Components the DFM evaluated but maturity does not know yet still count.
        known = {c["component_id"] for c in maturity["components"]}
        for cid, (body, _s) in sets.items():
            if cid not in known:
                comps.append(
                    {
                        "component_id": cid,
                        "name": body.get("component_name") or cid,
                        "maturity": "Illustrative",
                        "required": True,
                    }
                )
        items: list[ChecklistItem] = []

        # components_exact
        behind = [c for c in comps if rank(str(c.get("maturity"))) < rank(Maturity.EXACT)]
        # Concept components a recorded decision retired or mapped to the CAD
        # items that realise them: no longer required themselves, and named
        # here so the pass says why they are not counted.
        resolved = resolved_components(maturity["components"])
        items.append(
            ChecklistItem(
                id="components_exact",
                label=ITEM_LABELS["components_exact"],
                status="fail" if behind or not comps else "pass",
                detail=(
                    "; ".join(f"{c['name']} is {c['maturity']}" for c in behind)
                    if behind
                    else (
                        f"{len(comps)} required component(s) exact or beyond"
                        if comps
                        else "no required component is known"
                    )
                )
                + (f"; resolved: {'; '.join(resolved)}" if resolved else ""),
                blockers=[
                    f"{c['name']}: {c['maturity']}, needs a validated exact model" for c in behind
                ]
                or ([] if comps else ["no required component is known"]),
                next_steps=[
                    (
                        f"Build the engineering geometry for {c['name']}"
                        if not str(c.get("component_id") or "").startswith("CMP-")
                        else f"Build the engineering geometry for {c['name']}, or record "
                        "which CAD parts realise it or that it is retired"
                    )
                    for c in behind
                ]
                or ([] if comps else ["Describe the parts this product is made of"]),
            )
        )

        # dfm
        items.append(evaluate_dfm_item(comps, sets))

        # verification
        open_items: list[str] = []
        steps: list[str] = []
        total = 0
        for cid, (body, _s) in sets.items():
            if body.get("kind") != "exact":
                continue
            for v in body.get("verification_items") or []:
                total += 1
                if v.get("status") == "satisfied":
                    continue
                name = body.get("component_name") or cid
                state = (
                    "evidence waiting for approval"
                    if v.get("status") == "awaiting_approval"
                    else "no evidence recorded"
                )
                open_items.append(f"{name}: {v.get('title')} ({state})")
                steps.append(
                    f"Physical or supplier confirmation required: {v.get('title')} ({name})"
                )
        items.append(
            ChecklistItem(
                id="verification",
                label=ITEM_LABELS["verification"],
                status="fail" if open_items else ("pass" if total else "not_applicable"),
                detail="; ".join(open_items)
                if open_items
                else (f"{total} verification item(s) approved" if total else "none required"),
                blockers=open_items,
                next_steps=steps,
            )
        )

        # approvals
        try:
            approvals = await twin.list_engineering_entities(
                project_id=pid, entity_type="release_approval"
            )
        except Exception as exc:  # noqa: BLE001 - unreadable approvals are missing approvals
            logger.debug("checklist_approvals_unavailable", error=str(exc))
            approvals = []
        approved = [a for a in approvals if _authority(a) in APPROVED_AUTHORITIES]
        items.append(
            ChecklistItem(
                id="approvals",
                label=ITEM_LABELS["approvals"],
                status="pass" if approved else "fail",
                detail=_approvals_detail(approved),
                blockers=[] if approved else ["no approved manufacturing release approval"],
                next_steps=[] if approved else ["Ask a reviewer to approve the release"],
            )
        )

        # step_round_trip
        readiness_ = await exact_cad_readiness(twin, pid)
        pending = [r for r in readiness_ if not r.ready]
        items.append(
            ChecklistItem(
                id="step_round_trip",
                label=ITEM_LABELS["step_round_trip"],
                status="fail" if pending or not readiness_ else "pass",
                detail=(
                    "; ".join(r.summary() for r in pending)
                    if pending
                    else (
                        f"{len(readiness_)} CAD item(s) round-tripped"
                        if readiness_
                        else "no CAD item to export"
                    )
                ),
                blockers=[r.summary() for r in pending]
                or ([] if readiness_ else ["no CAD item to export"]),
                next_steps=[f"Export a checked STEP of {r.name}" for r in pending]
                or ([] if readiness_ else ["Build the engineering geometry"]),
            )
        )
        refs = sorted({f"{r.item_key}@{r.head_revision}" for r in readiness_})
        out = ManufacturingChecklist(project_id=str(pid), items=items, revision_refs=refs)
        span.set_attribute("checklist.applies", True)
        span.set_attribute("checklist.releasable", out.releasable)
        logger.info(
            "manufacturing_checklist_evaluated",
            project_id=str(pid),
            releasable=out.releasable,
            failing=[i.id for i in items if i.status == "fail"],
        )
        return out


__all__ = [
    "GATE_ITEMS",
    "ITEM_LABELS",
    "ITEM_ORDER",
    "PROTOTYPE_ITEMS",
    "ChecklistItem",
    "ManufacturingChecklist",
    "evaluate_dfm_item",
    "manufacturing_checklist",
    "readiness",
    "resolved_components",
]
