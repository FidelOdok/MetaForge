"""Where a creation stands, in the Create view's seven readiness states.

The state is derived, never stored: from the creation's own records (brief,
route, inspection plan, builds) and from the project's work products and
pending approvals. So an Engineering change that affects readiness shows up
in Create on the next read, with the blocker that explains it.

A finished design run is not readiness. Geometry existing means "design in
development"; "checked" needs every check recorded without errors and no
decision waiting; making needs a route, an inspection plan and confirmed
critical dimensions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from api_gateway.create.models import Creation

Stage = Literal[
    "concept",
    "design_in_development",
    "checked_design",
    "ready_for_making",
    "released_for_making",
    "build_completed",
    "accepted_product",
]

STAGES: tuple[Stage, ...] = (
    "concept",
    "design_in_development",
    "checked_design",
    "ready_for_making",
    "released_for_making",
    "build_completed",
    "accepted_product",
)

#: Dimensionally defined engineering outputs. An image or a sketch is a
#: concept, however good it looks.
ENGINEERING_TYPES: frozenset[str] = frozenset({"cad_model", "pcb_layout"})
CONCEPT_TYPES: frozenset[str] = frozenset({"design_sketch", "image"})
FAILING_STATUSES: frozenset[str] = frozenset({"invalid", "error", "failed", "rejected"})


class WorkProductSummary(BaseModel):
    id: str
    name: str = ""
    type: str
    status: str = ""


class Blocker(BaseModel):
    id: str
    message: str
    action: str


class Readiness(BaseModel):
    stage: Stage
    stage_index: int
    # What stands between this state and the next one, with what to do.
    blockers: list[Blocker]
    next_action: str
    has_concept: bool
    has_engineering_model: bool


def _blocker(id_: str, message: str, action: str) -> Blocker:
    return Blocker(id=id_, message=message, action=action)


def derive(
    creation: Creation,
    work_products: Iterable[WorkProductSummary],
    *,
    pending_approvals: int = 0,
) -> Readiness:
    wps = list(work_products)
    engineering = [w for w in wps if w.type in ENGINEERING_TYPES]
    concept = [w for w in wps if w.type in CONCEPT_TYPES]
    failing = [w for w in wps if w.status.lower() in FAILING_STATUSES]
    latest = creation.builds[-1] if creation.builds else None

    def done(stage: Stage, blockers: list[Blocker], next_action: str) -> Readiness:
        return Readiness(
            stage=stage,
            stage_index=STAGES.index(stage),
            blockers=blockers,
            next_action=next_action,
            has_concept=bool(concept),
            has_engineering_model=bool(engineering),
        )

    if latest is not None and latest.acceptance and latest.acceptance.decision == "accept":
        return done("accepted_product", [], "Accepted. Make another, or describe an improvement.")

    if latest is not None and latest.status == "completed":
        blockers = []
        for c in latest.checks:
            result = latest.results.get(c.id)
            status = result.status if result else "awaiting_review"
            if c.blocking and status != "passed":
                blockers.append(
                    _blocker(
                        f"check:{c.id}",
                        f"{c.feature}: {status.replace('_', ' ')}",
                        "Add evidence or record the inspection result",
                    )
                )
        action = (
            "Every required check passed: review and accept the build."
            if not blockers
            else "Inspect the build: add photos or measurements for the open checks."
        )
        return done("build_completed", blockers, action)

    if creation.active_builds:
        b = creation.active_builds[-1]
        verb = "being made" if b.status == "making" else "released"
        return done(
            "released_for_making",
            [
                _blocker(
                    "build:finish",
                    f"Build {b.number} is {verb}",
                    "Mark it finished when making ends",
                )
            ],
            f"Build {b.number} is {verb}. Record it as finished when it comes off the machine.",
        )

    if not engineering:
        blockers = [
            _blocker(
                "design:model",
                "There is no dimensioned model yet"
                + (" (the images so far are concepts)" if concept else ""),
                "Ask for a 3D design in the conversation",
            )
        ]
        return done("concept", blockers, "Describe the product, then ask for a design.")

    check_blockers: list[Blocker] = []
    for w in failing:
        check_blockers.append(
            _blocker(
                f"wp:{w.id}",
                f"{w.name or w.type} has a failing check",
                "Ask for a fix, or open it in Engineering",
            )
        )
    if pending_approvals:
        check_blockers.append(
            _blocker(
                "approvals",
                f"{pending_approvals} decision{'s' if pending_approvals != 1 else ''} waiting",
                "Review the waiting decisions",
            )
        )
    if check_blockers:
        return done(
            "design_in_development", check_blockers, "Resolve the open checks and decisions."
        )

    making: list[Blocker] = []
    if creation.route.route is None:
        making.append(
            _blocker(
                "route", "No way of making it chosen", "Choose home, workshop or a manufacturer"
            )
        )
    if not creation.inspection_plan:
        making.append(
            _blocker(
                "inspection_plan", "No inspection plan", "Turn the success criteria into checks"
            )
        )
    for m in creation.brief.measurements:
        if m.critical and m.status != "user_confirmed":
            making.append(
                _blocker(
                    f"measurement:{m.id}",
                    f"{m.label} is {m.status.replace('_', ' ')}",
                    "Confirm the dimension",
                )
            )
    if making:
        return done("checked_design", making, making[0].action + ".")

    preparing = [b for b in creation.builds if b.status == "preparing"]
    if not preparing:
        return done(
            "checked_design",
            [
                _blocker(
                    "build:prepare", "No build prepared", "Prepare a build of the approved revision"
                )
            ],
            "Prepare a build.",
        )
    return done(
        "ready_for_making",
        [
            _blocker(
                "build:release",
                f"Build {preparing[-1].number} is not released",
                "Review and release it",
            )
        ],
        "Review the build and release it for making.",
    )
