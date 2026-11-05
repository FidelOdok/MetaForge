"""Exact DFM: a process profile's checks on a validated B-Rep revision (DFM-007).

The CadQuery adapter's ``cadquery.dfm_measure`` measures the exact geometry
(wall thickness by ray casting, every face's outward normal, the extents
along the build direction, complete cylindrical holes and pins) and judges
nothing. :func:`evaluate_exact` compares those measures with the profile's
**exact checks** (``DfmProfile.exact_checks``) and returns a
:class:`DfmFindingSet` of kind ``exact``:

* every finding is anchored to the component and the B-Rep faces it is on
  (face ids ``s<solid>f<face>`` and their geometry signatures), with the
  measured value against the limit, the limit's source and the measurement
  method as evidence (DFM-008);
* a check that could not run (no machine selected, no wall sample, the
  measurement failed) is **not assessed** with its reason, and the set is
  incomplete;
* **status** (DFM-013): ``issues_found`` when any check found a blocking or
  advisory condition; ``analysis_pending`` when nothing was found but a
  required check did not run (the analysis has not concluded);
  ``analysis_passed`` only when every required check ran and none found
  anything. A pass is for the selected process, geometry revision and
  profile version; it is not a guarantee of build success;
* **verification items**: what the profile says needs physical or supplier
  verification for this component, given the manufacturing context. Never
  satisfied here: a person records evidence and a reviewer approves it.

Pure: Pydantic and the standard library only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from observability.tracing import get_tracer
from twin_core.dfm.heuristics import (
    ComponentGeometry,
    DfmFindingSet,
    ProcessContext,
    VerificationItem,
    _fmt,
    _Limit,
    _limit_for,
    _Run,
)
from twin_core.dfm.overhang import compute_overhang_faces
from twin_core.dfm.profiles import DfmProfile, DfmRule
from twin_core.manufacturing.context import DFM_STATUS_LABELS, DfmStatus

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.dfm.exact")

#: The adapter tool that measures the B-Rep.
MEASURE_TOOL = "cadquery.dfm_measure"
#: Production goals that make a part functional, and those that are a batch.
FUNCTIONAL_GOALS = frozenset({"functional_prototype", "pilot", "production"})
PRODUCTION_GOALS = frozenset({"pilot", "production"})


def verification_item_id(component_id: str, rule_id: str) -> str:
    """Stable across re-evaluations, so recorded evidence follows the item."""
    raw = f"{component_id}|{rule_id}"
    return "dfmv-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class _ExactRun(_Run):
    """One profile's exact checks on one component's measured B-Rep."""

    def __init__(
        self,
        geometry: ComponentGeometry,
        context: ProcessContext,
        profile: DfmProfile,
        decisions: dict[str, dict[str, Any]],
        measured: dict[str, Any],
        signatures: dict[str, str],
        geometry_ref: str,
    ) -> None:
        super().__init__(geometry, context, profile, decisions)
        self.measured = measured
        self.signatures = signatures
        self.geometry_ref = geometry_ref
        self.facts: dict[str, Any] = {}

    def sig(self, face_id: str | None) -> str | None:
        if not face_id:
            return None
        return self.signatures.get(face_id) or face_id

    def run_exact(self) -> None:
        for rule_id in self.profile.exact_checks:
            rule = self.profile.rule(rule_id)
            getattr(self, f"_exact_{rule.check}", self._exact_unsupported)(rule)

    def _exact_unsupported(self, rule: DfmRule) -> None:
        self.skip(rule, f"no exact check measures {rule.quantity.replace('_', ' ')} yet")

    # -- wall, feature and hole sizes ----------------------------------------

    def _exact_min(self, rule: DfmRule) -> None:
        if rule.quantity == "wall_thickness":
            self._wall(rule)
        elif rule.quantity == "feature_size":
            self._cylinders(rule, "bosses", "pin or boss")
        elif rule.quantity == "hole_diameter":
            self._cylinders(rule, "holes", "hole")
        else:
            self._exact_unsupported(rule)

    def _limit_or_skip(self, rule: DfmRule) -> _Limit | None:
        limit = _limit_for(rule, self.context.machine)
        if limit.missing:
            self.skip(rule, limit.missing)
            return None
        if not isinstance(limit.value, (int, float)) or isinstance(limit.value, bool):
            self.skip(rule, "the rule has no number to compare with")
            return None
        return limit

    def _wall(self, rule: DfmRule) -> None:
        wall = self.measured.get("wall_thickness")
        if not isinstance(wall, dict) or wall.get("status") != "measured":
            reason = (wall or {}).get("reason") if isinstance(wall, dict) else None
            self.skip(rule, str(reason or "the wall thickness could not be measured"))
            return
        lowest = _num(wall.get("min_mm"))
        if lowest is None:
            self.skip(rule, "the wall thickness could not be measured")
            return
        limit = self._limit_or_skip(rule)
        if limit is None:
            return
        bound = float(limit.value)  # type: ignore[arg-type]
        self.rules_run.append(rule.id)
        per_face = wall.get("per_face_min_mm") or {}
        thin = sorted(
            (
                (str(fid), float(v))
                for fid, v in per_face.items()
                if _num(v) is not None and float(v) < bound - 1e-9
            ),
            key=lambda p: p[1],
        )
        self.facts["wall_thickness_min_mm"] = lowest
        if lowest >= bound - 1e-9:
            return
        thinnest = (wall.get("thinnest") or [{}])[0]
        anchor_face = str(thinnest.get("face_id") or (thin[0][0] if thin else "")) or None
        self.add(
            rule,
            limit,
            measured=round(lowest, 4),
            limit_value=round(bound, 4),
            confidence="exact",
            feature=anchor_face,
            face=self.sig(anchor_face),
            faces=[fid for fid, _ in thin],
            evidence=[self.geometry_ref],
            comparison=">=",
        )

    def _cylinders(self, rule: DfmRule, key: str, words: str) -> None:
        found = self.measured.get(key)
        if not isinstance(found, list):
            self.skip(rule, f"the {words} sizes were not measured")
            return
        limit = self._limit_or_skip(rule)
        if limit is None:
            return
        bound = float(limit.value)  # type: ignore[arg-type]
        self.rules_run.append(rule.id)
        self.facts[f"{key}_count"] = len(found)
        for cyl in found:
            if not isinstance(cyl, dict):
                continue
            diameter = _num(cyl.get("diameter_mm"))
            if diameter is None or diameter >= bound - 1e-9:
                continue
            face_ids = [str(f) for f in cyl.get("face_ids") or []]
            first = face_ids[0] if face_ids else None
            self.add(
                rule,
                limit,
                measured=round(diameter, 4),
                limit_value=round(bound, 4),
                confidence="exact",
                feature=f"{words} {_fmt(diameter)} mm" + (f" ({first})" if first else ""),
                face=str(cyl.get("signature") or "") or self.sig(first),
                faces=face_ids,
                evidence=[self.geometry_ref],
                comparison=">=",
            )

    # -- overhang ------------------------------------------------------------

    def _exact_overhang(self, rule: DfmRule) -> None:
        faces = self.measured.get("faces")
        if not isinstance(faces, list) or not faces:
            self.skip(rule, "the part's faces were not measured")
            return
        limit = _limit_for(rule, self.context.machine)
        threshold = float(limit.value) if isinstance(limit.value, (int, float)) else 45.0
        build = self.measured.get("build_direction") or [0.0, 0.0, 1.0]
        table = [
            {
                "name": str(f.get("face_id")),
                "normal": f.get("normal") or f.get("most_downward_normal"),
                "area_mm2": _num(f.get("area")),
            }
            for f in faces
            if isinstance(f, dict) and not f.get("on_build_plate")
        ]
        checked = compute_overhang_faces(
            table,
            build_axis=(float(build[0]), float(build[1]), float(build[2])),
            threshold_deg=threshold,
            directed=True,
        )
        self.rules_run.append(rule.id)
        flagged = [c for c in checked if c["flagged"]]
        area = round(sum(float(c.get("area_mm2") or 0.0) for c in flagged), 4)
        self.facts["overhang_faces"] = len(flagged)
        self.facts["overhang_area_mm2"] = area
        self.facts["on_build_plate"] = [
            str(f.get("face_id")) for f in faces if isinstance(f, dict) and f.get("on_build_plate")
        ]
        if not flagged:
            return
        largest = max(flagged, key=lambda c: float(c.get("area_mm2") or 0.0))
        anchor = str(largest["name"])
        self.add(
            rule,
            limit if limit.value is not None else _Limit(threshold, rule.kind, rule.source),
            measured=max(float(c["tilt_from_vertical_deg"]) for c in flagged),
            limit_value=threshold,
            confidence="exact",
            feature=f"{len(flagged)} face(s), {_fmt(area)} mm2 needing support",
            face=self.sig(anchor),
            faces=[str(c["name"]) for c in flagged],
            evidence=[self.geometry_ref],
            comparison="<=",
            count=len(flagged),
        )

    # -- build volume ---------------------------------------------------------

    def _exact_envelope(self, rule: DfmRule) -> None:
        frame = self.measured.get("build_frame")
        extents = frame.get("extents_mm") if isinstance(frame, dict) else None
        if not (isinstance(extents, list) and len(extents) == 3):
            self.skip(rule, "the part's extents were not measured")
            return
        if self.context.machine is None:
            self.skip(rule, "no machine is selected in the manufacturing context")
            return
        limit = _limit_for(rule, self.context.machine)
        if limit.missing:
            self.skip(rule, limit.missing)
            return
        dims = limit.value
        if not (isinstance(dims, (list, tuple)) and len(dims) == 3):
            self.skip(rule, "the machine's build volume is not three numbers")
            return
        self.rules_run.append(rule.id)
        part = [float(v) for v in extents]
        machine = [float(d) for d in dims]
        footprint_fits = all(
            p <= m + 1e-9 for p, m in zip(sorted(part[:2]), sorted(machine[:2]), strict=True)
        )
        height_fits = part[2] <= machine[2] + 1e-9
        self.facts["build_extents_mm"] = part
        self.facts["machine_build_volume_mm"] = machine
        if footprint_fits and height_fits:
            return
        self.add(
            rule,
            limit,
            measured=_fmt(part),
            limit_value=_fmt(machine),
            confidence="exact",
            feature="height along the build direction" if not height_fits else "footprint",
            face=None,
            evidence=[self.geometry_ref],
            comparison="fits within",
        )


def _verification_items(
    profile: DfmProfile,
    component_id: str,
    *,
    tolerances_stated: bool,
    production_goal: str | None,
    previous: list[VerificationItem],
    geometry_ref: str,
) -> list[VerificationItem]:
    """The verifications that apply, carrying recorded evidence from ``previous``.

    Evidence approved for another geometry revision no longer settles the
    item: it is kept on record and the item is open again.
    """
    goal = (production_goal or "").strip().lower()
    by_id = {v.id: v for v in previous}
    out: list[VerificationItem] = []
    for rule in profile.verifications:
        reason = {
            "always": f"every {profile.process} part needs it",
            "tolerances_stated": "the manufacturing context states tolerances",
            "functional_use": f"the production goal is {goal.replace('_', ' ')}",
            "production_quantity": f"the production goal is {goal.replace('_', ' ')}",
        }[rule.when]
        applies = {
            "always": True,
            "tolerances_stated": tolerances_stated,
            "functional_use": goal in FUNCTIONAL_GOALS,
            "production_quantity": goal in PRODUCTION_GOALS,
        }[rule.when]
        if not applies:
            continue
        vid = verification_item_id(component_id, rule.id)
        item = VerificationItem(
            id=vid,
            rule_id=rule.id,
            component_id=component_id,
            title=rule.title,
            kind=rule.kind,
            needed=rule.needed,
            reason=reason,
            source=rule.source,
        )
        before = by_id.get(vid)
        if before is not None:
            item.evidence = list(before.evidence)
            if before.status == "satisfied" and before.satisfied_by:
                if str(before.satisfied_by.get("geometry_ref") or "") == geometry_ref:
                    item.status = "satisfied"
                    item.satisfied_by = dict(before.satisfied_by)
            elif before.status == "awaiting_approval":
                pending = [
                    e
                    for e in before.evidence
                    if e.get("state") == "awaiting_approval"
                    and str(e.get("geometry_ref") or "") == geometry_ref
                ]
                item.status = "awaiting_approval" if pending else "open"
        out.append(item)
    return out


def exact_note(
    status: DfmStatus, *, process: str, findings: int, blocking: int, gaps: list[str]
) -> str:
    """The short, truthful note an output and the Create badge show."""
    if status is DfmStatus.ANALYSIS_PASSED:
        return (
            f"Manufacturing analysis passed for {process} (exact checks; not a guarantee of "
            "build success)"
        )
    if status is DfmStatus.ISSUES_FOUND:
        if blocking:
            return (
                f"{blocking} blocking manufacturing issue{'s' if blocking != 1 else ''} for "
                f"{process} (exact checks)"
            )
        return (
            f"{findings} manufacturing issue{'s' if findings != 1 else ''} for {process} "
            "(exact checks)"
        )
    if gaps:
        return "Manufacturing analysis incomplete: " + "; ".join(gaps)[:300]
    return DFM_STATUS_LABELS[status]


def _hash(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def evaluate_exact(
    *,
    component_id: str,
    component_name: str,
    geometry_ref: str,
    context: ProcessContext,
    profile: DfmProfile,
    measured: dict[str, Any] | None,
    measure_error: str | None = None,
    signatures: dict[str, str] | None = None,
    decisions: dict[str, dict[str, Any]] | None = None,
    tolerances_stated: bool = False,
    production_goal: str | None = None,
    previous_items: list[VerificationItem] | None = None,
    build_direction_source: str = "default +Z (no orientation stated)",
) -> DfmFindingSet:
    """The exact findings of one component. See the module docstring."""
    with tracer.start_as_current_span("twin.dfm.evaluate_exact") as span:
        span.set_attribute("dfm.component_id", component_id)
        span.set_attribute("dfm.profile", profile.ref)
        geometry = ComponentGeometry(
            component_id=component_id,
            name=component_name,
            level="exact",
            geometry_refs=[geometry_ref],
            faces_confidence="exact",
            faces_directed=True,
        )
        run = _ExactRun(
            geometry,
            context,
            profile,
            decisions or {},
            measured or {},
            dict(signatures or {}),
            geometry_ref,
        )
        if measured is None:
            why = f"the exact measurement could not run ({measure_error or 'no result'})"
            for rule_id in profile.exact_checks:
                run.skip(profile.rule(rule_id), why[:300])
        else:
            run.run_exact()
        required = list(profile.exact_checks)
        complete = bool(required) and all(r in run.rules_run for r in required)
        blocking = sum(1 for f in run.findings if f.severity == "blocking")
        if run.findings:
            status = DfmStatus.ISSUES_FOUND
        elif not complete:
            status = DfmStatus.ANALYSIS_PENDING
        else:
            status = DfmStatus.ANALYSIS_PASSED
        gaps = [n.text for n in run.not_assessed]
        wall = (measured or {}).get("wall_thickness") or {}
        evidence: dict[str, Any] = {
            "tool": MEASURE_TOOL,
            "geometry_ref": geometry_ref,
            "kernel": (measured or {}).get("kernel"),
            "build_direction": (measured or {}).get("build_direction") or [0.0, 0.0, 1.0],
            "build_direction_source": build_direction_source,
            "wall_method": wall.get("method"),
            "wall_limits": list(wall.get("limits") or []),
            "wall_samples": wall.get("samples"),
            "wall_rays": wall.get("rays"),
            "wall_samples_per_side": wall.get("samples_per_side"),
            "hole_limits": list((measured or {}).get("hole_limits") or []),
            "face_signatures": dict(signatures or {}),
            "measured": run.facts,
            "duration_s": (measured or {}).get("duration_s"),
        }
        if measure_error:
            evidence["measure_error"] = measure_error[:500]
        items = _verification_items(
            profile,
            component_id,
            tolerances_stated=tolerances_stated,
            production_goal=production_goal,
            previous=list(previous_items or []),
            geometry_ref=geometry_ref,
        )
        inputs = {
            "profile": profile.ref,
            "geometry_ref": geometry_ref,
            "context": context.model_dump(mode="json"),
            "measured": measured,
            "error": measure_error,
            "tolerances_stated": tolerances_stated,
            "production_goal": production_goal,
        }
        out = DfmFindingSet(
            component_id=component_id,
            component_name=component_name,
            process=profile.process,
            process_authority=context.process_authority,
            profile_id=profile.id,
            profile_version=profile.version,
            geometry_level="exact",
            geometry_refs=[geometry_ref],
            context_ref=context.context_ref,
            findings=run.findings,
            not_assessed=run.not_assessed,
            rules_run=run.rules_run,
            status=status,
            note=exact_note(
                status,
                process=profile.process,
                findings=len(run.findings),
                blocking=blocking,
                gaps=gaps,
            ),
            kind="exact",
            heuristic_only=False,
            inputs_sha256=_hash(inputs),
            checks_required=required,
            complete=complete,
            guidance_only_rules=[r.id for r in profile.rules if r.id not in required],
            exact_evidence=evidence,
            verification_items=items,
        )
        span.set_attribute("dfm.status", status.value)
        span.set_attribute("dfm.findings", len(out.findings))
        span.set_attribute("dfm.complete", complete)
        logger.info(
            "dfm_exact_evaluated",
            component_id=component_id,
            profile=profile.ref,
            geometry_ref=geometry_ref,
            status=status.value,
            findings=len(out.findings),
            not_assessed=[n.rule_id for n in run.not_assessed],
            verification_items=len(items),
        )
        return out


__all__ = [
    "FUNCTIONAL_GOALS",
    "MEASURE_TOOL",
    "PRODUCTION_GOALS",
    "evaluate_exact",
    "exact_note",
    "verification_item_id",
]
