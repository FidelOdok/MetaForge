"""Where a tailored or edited flow actually lives (FORGE-399).

FORGE-398 generates a proposal and holds it for approval — and stores only a
text diff of it. So approving one did not give anybody a flow to start: the
tailored definition existed for the length of an HTTP response and then went
away. That is the gap this closes, and it is shared, because an edited flow
(this ticket) needs exactly the same thing.

A version is immutable once written. Editing produces a *new* version with a
new id, never a mutation of an existing one, because:

* a run pins the version it started on (FORGE-401 freezes the flow into the
  workflow input), so a version changing underneath would make a completed
  run's provenance a lie;
* a diff needs something stable to diff against;
* an approval answers a specific flow. If the flow can change after the
  approval, the approval means nothing.

Templates on disk stay read-only. They are the repo's, and a user editing
their own flow must not write into them — which is also why a version records
the template and version it descends from rather than replacing it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import structlog

from orchestrator.design_flow.frozen import FrozenFlow, freeze_flow
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.spec import FlowDefinition

logger = structlog.get_logger(__name__)

__all__ = [
    "FlowVersion",
    "FlowVersionStore",
    "VersionNotFoundError",
    "VersionStatus",
    "diff_flows",
    "get_version_store",
    "reset_version_store",
]


class VersionStatus(StrEnum):
    #: Written, waiting for a human. Cannot start a run.
    PROPOSED = "proposed"
    #: A human approved it. Startable.
    APPROVED = "approved"
    #: A human refused it. Kept, because "who rejected what and why" is the
    #: part of a review trail people actually go looking for.
    REJECTED = "rejected"


class VersionNotFoundError(KeyError):
    """No stored flow version with that id."""


@dataclass
class FlowVersion:
    """One immutable flow, and where it came from."""

    id: str
    definition: FlowDefinition
    frozen: FrozenFlow
    base_template_id: str
    base_version: str
    #: Human-readable lines describing how this differs from its base.
    changes: list[str] = field(default_factory=list)
    origin: str = "generated"
    intent: str = ""
    created_at: str = ""
    status: VersionStatus = VersionStatus.PROPOSED
    #: Who approved or rejected, from the approval record (FORGE-393).
    decided_by: str = ""
    #: The approval-ledger entry holding this version.
    approval_id: str = ""

    @property
    def valid(self) -> bool:
        return validate_flow(self.definition).ok

    @property
    def startable(self) -> bool:
        """A run may be created from this version.

        Both conditions, deliberately. An approved-but-invalid version should
        be impossible — the save path refuses invalid flows — but "impossible"
        and "checked" are different, and this is the check that would catch a
        rule added after a version was approved.
        """
        return self.status is VersionStatus.APPROVED and self.valid


def diff_flows(base: FlowDefinition, candidate: FlowDefinition) -> list[str]:
    """Describe how ``candidate`` differs from ``base``, one line per change.

    Deliberately prose rather than a structural diff: this is read by a person
    deciding whether to approve, and "requires 'simulation_result' from
    'design'" answers that question in a way a JSON patch does not.
    """
    lines: list[str] = []
    base_phases = {p.id: p for p in base.phases}
    candidate_phases = {p.id: p for p in candidate.phases}

    for phase_id, phase in base_phases.items():
        if phase_id not in candidate_phases:
            lines.append(f"removed phase '{phase_id}' ({phase.title})")
    for phase_id, phase in candidate_phases.items():
        if phase_id not in base_phases:
            lines.append(f"added phase '{phase_id}' ({phase.title})")

    base_order = [p.id for p in base.phases if p.id in candidate_phases]
    candidate_order = [p.id for p in candidate.phases if p.id in base_phases]
    if base_order != candidate_order:
        lines.append(f"reordered phases: {' → '.join(candidate_order)}")

    for phase_id in candidate_phases.keys() & base_phases.keys():
        before, after = base_phases[phase_id], candidate_phases[phase_id]
        added = set(after.required_deliverables) - set(before.required_deliverables)
        removed = set(before.required_deliverables) - set(after.required_deliverables)
        for artifact in sorted(added):
            lines.append(f"phase '{phase_id}' now requires '{artifact}'")
        for artifact in sorted(removed):
            # Called out explicitly: relaxing a gate is the change most worth
            # a reviewer's attention, and the easiest to miss in a long diff.
            lines.append(f"phase '{phase_id}' NO LONGER requires '{artifact}'")
        if set(after.disciplines) != set(before.disciplines):
            lines.append(
                f"phase '{phase_id}' disciplines: "
                f"{list(before.disciplines) or 'none'} → {list(after.disciplines) or 'none'}"
            )
        if before.enforce_deliverables and not after.enforce_deliverables:
            lines.append(f"phase '{phase_id}' NO LONGER enforces its deliverables")
        if (before.gate is None) != (after.gate is None):
            lines.append(f"phase '{phase_id}' gate {'removed' if after.gate is None else 'added'}")
        elif before.gate is not None and after.gate is not None:
            if before.gate.name != after.gate.name:
                lines.append(
                    f"phase '{phase_id}' gate renamed: '{before.gate.name}' → '{after.gate.name}'"
                )
            if set(before.gate.criteria) != set(after.gate.criteria):
                lines.append(f"phase '{phase_id}' gate criteria changed")

    return lines


class FlowVersionStore:
    """Process-level store of flow versions.

    In-memory, mirroring ``InMemoryRunStore``. A version outliving a gateway
    restart matters once flows are edited in anger; until then, saying so is
    better than a half-durable store that looks persistent.
    """

    def __init__(self) -> None:
        self._versions: dict[str, FlowVersion] = {}

    def save(
        self,
        definition: FlowDefinition,
        *,
        base_template_id: str,
        base_version: str,
        changes: list[str],
        origin: str = "generated",
        intent: str = "",
        approval_id: str = "",
    ) -> FlowVersion:
        """Write a new version. Refuses one that breaks an invariant."""
        result = validate_flow(definition)
        if not result.ok:
            result.raise_if_invalid(definition.id)

        version_id = f"flowv_{uuid.uuid4().hex[:12]}"
        version = FlowVersion(
            id=version_id,
            definition=definition,
            frozen=freeze_flow(definition, version=f"{base_version}+{version_id}"),
            base_template_id=base_template_id,
            base_version=base_version,
            changes=changes,
            origin=origin,
            intent=intent,
            created_at=datetime.now(UTC).isoformat(),
            approval_id=approval_id,
        )
        self._versions[version_id] = version
        logger.info(
            "flow_version_saved",
            version_id=version_id,
            base=base_template_id,
            base_version=base_version,
            origin=origin,
            changes=len(changes),
        )
        return version

    def get(self, version_id: str) -> FlowVersion:
        try:
            return self._versions[version_id]
        except KeyError as exc:
            raise VersionNotFoundError(f"no flow version '{version_id}'") from exc

    def list(self) -> list[FlowVersion]:
        return list(self._versions.values())

    def decide(self, version_id: str, *, approved: bool, decided_by: str) -> FlowVersion:
        """Record a human's decision on a version.

        ``decided_by`` comes from the approval record (FORGE-393), never from
        whoever is calling this.
        """
        version = self.get(version_id)
        if version.status is not VersionStatus.PROPOSED:
            raise ValueError(
                f"flow version '{version_id}' was already {version.status.value}; "
                "a decision is made once"
            )
        version.status = VersionStatus.APPROVED if approved else VersionStatus.REJECTED
        version.decided_by = decided_by
        logger.info(
            "flow_version_decided",
            version_id=version_id,
            status=version.status.value,
            decided_by=decided_by,
        )
        return version


_store = FlowVersionStore()


def get_version_store() -> FlowVersionStore:
    return _store


def reset_version_store() -> None:
    """Fresh store — tests only, mirrors ``reset_run_store``."""
    global _store  # noqa: PLW0603
    _store = FlowVersionStore()
