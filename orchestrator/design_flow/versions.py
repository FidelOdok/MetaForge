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

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from orchestrator.design_flow.frozen import FrozenFlow, freeze_flow
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.slots import bind_slots, effective_slots
from orchestrator.design_flow.spec import FlowDefinition, Gate, Phase
from orchestrator.design_flow.templates import slots_from

logger = structlog.get_logger(__name__)

__all__ = [
    "FlowVersion",
    "FlowVersionStore",
    "VersionNotFoundError",
    "VersionStatus",
    "default_versions_path",
    "diff_flows",
    "get_version_store",
    "init_version_store",
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
        if before.model != after.model:
            lines.append(
                f"phase '{phase_id}' model: {before.model or 'routed by role'} → "
                f"{after.model or 'routed by role'}"
            )
        # FORGE-524: declared items. Compared on the effective slots, so a
        # version whose defaults were materialised at save does not read as a
        # change from the template it was never different from.
        before_slots = {(s.item_type, s.name): s.item_key for s in effective_slots(before)}
        after_slots = {(s.item_type, s.name): s.item_key for s in effective_slots(after)}
        for item_type, name in sorted(after_slots.keys() - before_slots.keys()):
            lines.append(
                f"phase '{phase_id}' declares {item_type} '{name}' "
                f"(item {after_slots[(item_type, name)]})"
            )
        for item_type, name in sorted(before_slots.keys() - after_slots.keys()):
            lines.append(f"phase '{phase_id}' no longer declares {item_type} '{name}'")
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


def default_versions_path() -> Path:
    """Where flow versions live by default (FORGE-482).

    ``METAFORGE_FLOW_VERSIONS_PATH`` override, else
    ``~/.metaforge/flow_versions.db`` -- same convention as the run and
    tool-approval ledgers.
    """
    override = os.environ.get("METAFORGE_FLOW_VERSIONS_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "flow_versions.db"


def _definition_from_dict(data: dict[str, Any]) -> FlowDefinition:
    phases: list[Phase] = []
    for p in data.get("phases", []):
        g = p.get("gate")
        gate = (
            None
            if g is None
            else Gate(
                name=g["name"],
                auto_approve=bool(g.get("auto_approve", False)),
                criteria=tuple(g.get("criteria", ())),
                enforce_constraints=bool(g.get("enforce_constraints", False)),
                gate_id=g.get("gate_id"),
            )
        )
        phases.append(
            Phase(
                id=p["id"],
                title=p["title"],
                objective=p["objective"],
                expected_artifacts=tuple(p.get("expected_artifacts", ())),
                required_deliverables=tuple(p.get("required_deliverables", ())),
                enforce_deliverables=bool(p.get("enforce_deliverables", True)),
                gate=gate,
                disciplines=tuple(p.get("disciplines", ())),
                model=p.get("model"),
                slots=slots_from(p.get("slots")),
                # FORGE-539: absent in rows written before graphs existed.
                depends_on=(None if p.get("depends_on") is None else tuple(p["depends_on"])),
                condition=p.get("condition") or None,
                outcome=str(p.get("outcome") or ""),
            )
        )
    return FlowDefinition(id=data["id"], name=data["name"], phases=tuple(phases))


class FlowVersionStore:
    """Store of flow versions, durable when given a SQLite ``path`` (FORGE-482).

    With no ``path`` it is in memory: the test double. The gateway wires a
    file-backed one at startup (``init_version_store``), because a version in
    process memory meant every proposal and every human approval vanished on
    a restart while the Temporal run using it survived.

    Every write goes through to SQLite; the dict is a read cache restored at
    construction. Approved and rejected versions are immutable: the SQL
    guards refuse to touch a decided row.
    """

    def __init__(self, path: str | None = None) -> None:
        self._versions: dict[str, FlowVersion] = {}
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_versions (
                    id                 TEXT PRIMARY KEY,
                    status             TEXT NOT NULL,
                    definition         TEXT NOT NULL,
                    frozen_version     TEXT NOT NULL,
                    content_hash       TEXT NOT NULL,
                    base_template_id   TEXT NOT NULL,
                    base_version       TEXT NOT NULL,
                    changes            TEXT NOT NULL,
                    origin             TEXT NOT NULL,
                    intent             TEXT NOT NULL,
                    created_at         TEXT NOT NULL,
                    decided_by         TEXT NOT NULL,
                    decided_at         TEXT NOT NULL,
                    approval_id        TEXT NOT NULL
                )
                """
            )
            # FORGE-491: versions written before the flow context existed.
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(flow_versions)")}
            if "flow_context" not in cols:
                self._conn.execute(
                    "ALTER TABLE flow_versions ADD COLUMN flow_context TEXT NOT NULL DEFAULT ''"
                )
            # FORGE-539: the facts phase conditions read, frozen with the version.
            if "flow_facts" not in cols:
                self._conn.execute(
                    "ALTER TABLE flow_versions ADD COLUMN flow_facts TEXT NOT NULL DEFAULT '{}'"
                )
            self._conn.commit()
            self._restore()

    def _restore(self) -> None:
        assert self._conn is not None  # noqa: S101
        restored = 0
        for row in self._conn.execute("SELECT * FROM flow_versions ORDER BY created_at"):
            try:
                definition = _definition_from_dict(json.loads(row["definition"]))
                frozen = freeze_flow(
                    definition,
                    version=row["frozen_version"],
                    context=row["flow_context"] or "",
                    facts=json.loads(row["flow_facts"] or "{}"),
                )
                if frozen.content_hash != row["content_hash"]:
                    raise ValueError(
                        f"stored hash {row['content_hash'][:12]} != recomputed "
                        f"{frozen.content_hash[:12]}"
                    )
                self._versions[row["id"]] = FlowVersion(
                    id=row["id"],
                    definition=definition,
                    frozen=frozen,
                    base_template_id=row["base_template_id"],
                    base_version=row["base_version"],
                    changes=json.loads(row["changes"]),
                    origin=row["origin"],
                    intent=row["intent"],
                    created_at=row["created_at"],
                    status=VersionStatus(row["status"]),
                    decided_by=row["decided_by"],
                    approval_id=row["approval_id"],
                )
                restored += 1
            except Exception as exc:  # noqa: BLE001
                # One bad row must not take the store down, and must not be
                # quietly run either: it is skipped and shouted about.
                logger.error("flow_version_restore_failed", version_id=row["id"], error=str(exc))
        logger.info("flow_versions_restored", count=restored)

    def _persist(self, version: FlowVersion, *, decided_at: str = "") -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO flow_versions (
                id, status, definition, frozen_version, content_hash, base_template_id,
                base_version, changes, origin, intent, created_at, decided_by, decided_at,
                approval_id, flow_context, flow_facts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                decided_by=excluded.decided_by,
                decided_at=CASE WHEN excluded.decided_at != '' THEN excluded.decided_at
                                ELSE flow_versions.decided_at END,
                approval_id=excluded.approval_id
            """,
            (
                version.id,
                version.status.value,
                json.dumps(asdict(version.definition), sort_keys=True),
                version.frozen.version,
                version.frozen.content_hash,
                version.base_template_id,
                version.base_version,
                json.dumps(version.changes),
                version.origin,
                version.intent,
                version.created_at,
                version.decided_by,
                decided_at,
                version.approval_id,
                version.frozen.context,
                json.dumps(version.frozen.facts, sort_keys=True),
            ),
        )
        self._conn.commit()

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
        context: str = "",
        facts: dict[str, str] | None = None,
    ) -> FlowVersion:
        """Write a new version. Refuses one that breaks an invariant.

        FORGE-524: the deliverable slots and their item keys are bound here,
        so they are part of the version's frozen, hashed content from the
        moment it exists, and an approval approves them too.
        """
        definition = bind_slots(definition)
        result = validate_flow(definition)
        if not result.ok:
            result.raise_if_invalid(definition.id)

        version_id = f"flowv_{uuid.uuid4().hex[:12]}"
        version = FlowVersion(
            id=version_id,
            definition=definition,
            frozen=freeze_flow(
                definition,
                version=f"{base_version}+{version_id}",
                context=context,
                facts=facts,
            ),
            base_template_id=base_template_id,
            base_version=base_version,
            changes=changes,
            origin=origin,
            intent=intent,
            created_at=datetime.now(UTC).isoformat(),
            approval_id=approval_id,
        )
        with self._lock:
            self._persist(version)
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

    def attach_approval(self, version_id: str, approval_id: str) -> FlowVersion:
        """Record the approval-ledger entry holding a still-proposed version."""
        with self._lock:
            version = self.get(version_id)
            if version.status is not VersionStatus.PROPOSED:
                raise ValueError(
                    f"flow version '{version_id}' is {version.status.value}; it is immutable"
                )
            version.approval_id = approval_id
            self._persist(version)
        return version

    def decide(self, version_id: str, *, approved: bool, decided_by: str) -> FlowVersion:
        """Record a human's decision on a version.

        ``decided_by`` comes from the approval record (FORGE-393), never from
        whoever is calling this.
        """
        with self._lock:
            version = self.get(version_id)
            if version.status is not VersionStatus.PROPOSED:
                raise ValueError(
                    f"flow version '{version_id}' was already {version.status.value}; "
                    "a decision is made once"
                )
            version.status = VersionStatus.APPROVED if approved else VersionStatus.REJECTED
            version.decided_by = decided_by
            self._persist(version, decided_at=datetime.now(UTC).isoformat())
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


def init_version_store(path: str | None) -> FlowVersionStore:
    """Wire the durable store (FORGE-482); ``None`` restores the in-memory double."""
    global _store  # noqa: PLW0603
    _store = FlowVersionStore(path)
    return _store


def reset_version_store() -> None:
    """Fresh in-memory store -- tests only, mirrors ``reset_run_store``."""
    global _store  # noqa: PLW0603
    _store = FlowVersionStore()
