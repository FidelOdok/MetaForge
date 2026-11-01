"""Editing a flow: validation, diff, versioning (FORGE-399).

FORGE-398 generated a proposal, held it for approval, and stored only a text
diff -- so approving one gave nobody a flow to start. That gap is closed here,
because an edited flow needs exactly the same thing, and two stores would have
been two answers to "which flow did this run use".

The properties worth protecting:

* a version is **immutable**. A run pins the version it started on, so a
  version changing underneath makes a completed run's provenance a lie -- and
  an approval that can be edited afterwards is not an approval.
* an edit that breaks an invariant **cannot be saved**, not "is saved with a
  warning".
* the diff calls out **relaxations** explicitly. Tightening a gate is normal;
  loosening one is the change a reviewer most needs to see and the easiest to
  miss in a long list.
"""

from __future__ import annotations

import pytest

from orchestrator.design_flow.invariants import FlowInvariantError
from orchestrator.design_flow.spec import FlowDefinition, Phase, flow_version, get_flow
from orchestrator.design_flow.versions import (
    FlowVersionStore,
    VersionNotFoundError,
    VersionStatus,
    diff_flows,
)


def _store() -> FlowVersionStore:
    return FlowVersionStore()


def _without(flow_id: str, phase_id: str) -> FlowDefinition:
    base = get_flow(flow_id)
    return FlowDefinition(
        id=base.id,
        name=base.name,
        phases=tuple(p for p in base.phases if p.id != phase_id),
    )


def _with_extra_deliverable(flow_id: str, phase_id: str, artifact: str) -> FlowDefinition:
    base = get_flow(flow_id)
    phases = []
    for phase in base.phases:
        if phase.id != phase_id:
            phases.append(phase)
            continue
        phases.append(
            Phase(
                id=phase.id,
                title=phase.title,
                objective=phase.objective,
                expected_artifacts=phase.expected_artifacts,
                required_deliverables=tuple([*phase.required_deliverables, artifact]),
                enforce_deliverables=phase.enforce_deliverables,
                gate=phase.gate,
                disciplines=phase.disciplines,
            )
        )
    return FlowDefinition(id=base.id, name=base.name, phases=tuple(phases))


# ── the diff ─────────────────────────────────────────────────────────────


class TestTheDiffIsReadableByAReviewer:
    def test_a_removed_phase_is_named(self) -> None:
        lines = diff_flows(get_flow("hardware_v1"), _without("hardware_v1", "firmware"))
        assert any("removed phase 'firmware'" in line for line in lines)

    def test_an_added_requirement_is_named(self) -> None:
        candidate = _with_extra_deliverable("hardware_v1", "simulation", "simulation_result")
        lines = diff_flows(get_flow("hardware_v1"), candidate)
        assert any("now requires 'simulation_result'" in line for line in lines)

    def test_a_relaxation_is_shouted_not_mentioned(self) -> None:
        """Tightening a gate is normal. Loosening one is the change a reviewer
        most needs to see, and the easiest to lose in a twenty-line diff."""
        base = _with_extra_deliverable("hardware_v1", "simulation", "simulation_result")
        lines = diff_flows(base, get_flow("hardware_v1"))
        relaxation = next(line for line in lines if "simulation_result" in line)
        assert "NO LONGER" in relaxation

    def test_disabled_enforcement_is_called_out(self) -> None:
        base = get_flow("hardware_v1")
        phases = []
        for phase in base.phases:
            phases.append(
                Phase(
                    id=phase.id,
                    title=phase.title,
                    objective=phase.objective,
                    expected_artifacts=phase.expected_artifacts,
                    required_deliverables=phase.required_deliverables,
                    enforce_deliverables=False,
                    gate=phase.gate,
                    disciplines=phase.disciplines,
                )
            )
        lines = diff_flows(base, FlowDefinition(id=base.id, name=base.name, phases=tuple(phases)))
        assert any("NO LONGER enforces" in line for line in lines)

    def test_reordering_is_described(self) -> None:
        base = get_flow("mech_v1")
        reversed_phases = tuple(reversed(base.phases))
        lines = diff_flows(base, FlowDefinition(id=base.id, name=base.name, phases=reversed_phases))
        assert any("reordered phases" in line for line in lines)

    def test_an_identical_flow_diffs_to_nothing(self) -> None:
        # The control. A diff that reports changes against an unchanged flow
        # would make every real change unreadable.
        assert diff_flows(get_flow("hardware_v1"), get_flow("hardware_v1")) == []


# ── the store ────────────────────────────────────────────────────────────


class TestVersionsAreImmutable:
    def test_saving_twice_makes_two_versions(self) -> None:
        store = _store()
        first = store.save(
            _without("hardware_v1", "firmware"),
            base_template_id="hardware_v1",
            base_version=flow_version("hardware_v1"),
            changes=["removed firmware"],
        )
        second = store.save(
            _without("hardware_v1", "electronics"),
            base_template_id="hardware_v1",
            base_version=flow_version("hardware_v1"),
            changes=["removed electronics"],
        )
        assert first.id != second.id
        assert store.get(first.id).definition != store.get(second.id).definition

    def test_a_version_records_what_it_descends_from(self) -> None:
        store = _store()
        version = store.save(
            _without("hardware_v1", "firmware"),
            base_template_id="hardware_v1",
            base_version="1.0.0",
            changes=["removed firmware"],
        )
        assert version.base_template_id == "hardware_v1"
        assert version.base_version == "1.0.0"
        # The frozen flow carries a hash, so the run that starts from it can
        # prove it ran what was approved (FORGE-401).
        assert version.frozen.content_hash
        version.frozen.verify()

    def test_an_unknown_version_raises(self) -> None:
        with pytest.raises(VersionNotFoundError):
            _store().get("flowv_nope")


class TestAnInvalidEditCannotBeSaved:
    def test_saving_a_flow_that_breaks_an_invariant_raises(self) -> None:
        """The ticket's own acceptance criterion. Not "saved with a warning":
        a stored invalid version is one somebody can approve, and an approval
        of an unstartable flow is a decision that means nothing."""
        base = get_flow("hardware_v1")
        gated = FlowDefinition(
            id=base.id,
            name=base.name,
            phases=tuple(p for p in base.phases if not (p.gate and not p.gate.auto_approve)),
        )
        with pytest.raises(FlowInvariantError) as exc:
            _store().save(
                gated,
                base_template_id="hardware_v1",
                base_version="1.0.0",
                changes=["removed every gate"],
            )
        assert "release-gate-exists" in str(exc.value)

    def test_nothing_is_stored_when_the_save_is_refused(self) -> None:
        store = _store()
        empty = FlowDefinition(id="hardware_v1", name="x", phases=())
        with pytest.raises(FlowInvariantError):
            store.save(empty, base_template_id="hardware_v1", base_version="1.0.0", changes=[])
        assert store.list() == []


class TestApproval:
    def _saved(self, store: FlowVersionStore):
        return store.save(
            _without("hardware_v1", "firmware"),
            base_template_id="hardware_v1",
            base_version=flow_version("hardware_v1"),
            changes=["removed firmware"],
        )

    def test_a_new_version_is_not_startable(self) -> None:
        """Nothing runs on an unapproved flow."""
        version = self._saved(_store())
        assert version.status is VersionStatus.PROPOSED
        assert version.startable is False

    def test_approval_makes_it_startable_and_records_who(self) -> None:
        store = _store()
        version = self._saved(store)
        store.decide(version.id, approved=True, decided_by="user:reviewer")
        assert store.get(version.id).startable is True
        assert store.get(version.id).decided_by == "user:reviewer"

    def test_a_rejected_version_is_kept_but_never_startable(self) -> None:
        """ "Who rejected what, and why" is the part of a review trail people
        actually go looking for."""
        store = _store()
        version = self._saved(store)
        store.decide(version.id, approved=False, decided_by="user:reviewer")
        stored = store.get(version.id)
        assert stored.status is VersionStatus.REJECTED
        assert stored.startable is False

    def test_a_decision_is_made_once(self) -> None:
        store = _store()
        version = self._saved(store)
        store.decide(version.id, approved=False, decided_by="user:a")
        with pytest.raises(ValueError, match="already rejected"):
            store.decide(version.id, approved=True, decided_by="user:b")

    def test_startable_checks_validity_as_well_as_approval(self) -> None:
        """An approved-but-invalid version should be impossible, because the
        save path refuses invalid flows. "Impossible" and "checked" are
        different things, and this is what would catch a rule added after a
        version was approved.
        """
        store = _store()
        version = self._saved(store)
        store.decide(version.id, approved=True, decided_by="user:r")
        assert version.startable is True
        # Reach past the save-time check the way a new rule effectively would.
        version.definition = FlowDefinition(id="hardware_v1", name="x", phases=())
        assert version.valid is False
        assert version.startable is False


class TestARunningFlowIsUnaffectedByLaterEdits:
    def test_the_frozen_flow_is_a_snapshot_not_a_reference(self) -> None:
        """The acceptance criterion "edits never change a running flow".

        It holds structurally rather than by policy: FORGE-401 freezes the
        flow into the workflow input and verifies its content hash, so a later
        edit produces a *different version* and cannot reach a run already
        started. Mutating the stored definition afterwards must leave the
        frozen snapshot -- the thing a run actually executes -- untouched.
        """
        store = _store()
        version = store.save(
            _without("hardware_v1", "firmware"),
            base_template_id="hardware_v1",
            base_version=flow_version("hardware_v1"),
            changes=["removed firmware"],
        )
        frozen_before = version.frozen.content_hash
        phases_before = [p.id for p in version.frozen.phases]

        # Someone edits the flow afterwards. In the real system this is a new
        # version; here we mutate in place, which is strictly worse than what
        # can actually happen, to show the snapshot still holds.
        version.definition = _without("hardware_v1", "electronics")

        assert version.frozen.content_hash == frozen_before
        assert [p.id for p in version.frozen.phases] == phases_before
        version.frozen.verify()

    def test_editing_produces_a_new_version_rather_than_changing_one(self) -> None:
        store = _store()
        first = store.save(
            _without("hardware_v1", "firmware"),
            base_template_id="hardware_v1",
            base_version="1.0.0",
            changes=["a"],
        )
        second = store.save(
            _without("hardware_v1", "electronics"),
            base_template_id="hardware_v1",
            base_version="1.0.0",
            changes=["b"],
        )
        # Both still retrievable, both unchanged: a run pinned to the first is
        # unaffected by the existence of the second.
        assert store.get(first.id).frozen.content_hash != store.get(second.id).frozen.content_hash
        assert "firmware" not in {p.id for p in store.get(first.id).definition.phases}
        assert "firmware" in {p.id for p in store.get(second.id).definition.phases}
