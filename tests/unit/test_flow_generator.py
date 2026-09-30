"""The model tailors a template; it does not write a flow (FORGE-398).

The property worth protecting is not "the generator produces good flows" --
nothing here can assert that. It is that a model cannot produce a *bad* one:
there is no operation for removing a gate, removing a deliverable or switching
enforcement off, so the failure modes that matter are unreachable rather than
caught. The invariant validator still runs, as a backstop.

So most of this file is about what the generator refuses to do with input
designed to make it misbehave, and the three-intent acceptance test asserts
the flows differ *and* that every one is startable.
"""

from __future__ import annotations

import pytest

from orchestrator.design_flow.generator import (
    Operation,
    OperationKind,
    apply_operations,
    build_proposal,
    parse_operations,
)
from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.templates import load_templates


def _hardware():
    return get_flow("hardware_v1")


def _version(flow_id: str) -> str:
    return load_templates()[flow_id].version


# ── what the model can express ───────────────────────────────────────────


class TestTheOperationSetIsClosed:
    def test_an_invented_operation_disappears(self) -> None:
        """A model asking to remove a gate gets silence, not an error.

        Failing the whole proposal would teach it to retry with different
        wording. Dropping the one operation teaches it the capability does not
        exist, and everything real it asked for still applies.
        """
        ops = parse_operations(
            [
                {"op": "remove_gate", "phase": "simulation", "rationale": "faster"},
                {"op": "drop_phase", "phase": "firmware", "rationale": "no electronics"},
            ]
        )
        assert [o.kind for o in ops] == [OperationKind.DROP_PHASE]

    def test_an_operation_without_a_rationale_is_dropped(self) -> None:
        """A change nobody can review is not a change worth keeping."""
        assert parse_operations([{"op": "drop_phase", "phase": "firmware"}]) == []

    def test_an_operation_without_a_phase_is_dropped(self) -> None:
        assert parse_operations([{"op": "drop_phase", "rationale": "x"}]) == []

    def test_junk_input_yields_nothing_rather_than_raising(self) -> None:
        assert parse_operations("not a list") == []
        assert parse_operations([None, 3, "x"]) == []


class TestTailoringCanOnlyTighten:
    def test_a_deliverable_can_be_added(self) -> None:
        flow, applied = apply_operations(
            _hardware(),
            [
                Operation(
                    OperationKind.ADD_DELIVERABLE,
                    "simulation",
                    "thermal risk is the whole product",
                    value="simulation_result",
                )
            ],
        )
        phase = next(p for p in flow.phases if p.id == "simulation")
        assert "simulation_result" in phase.required_deliverables
        assert len(applied) == 1

    def test_enforcement_is_carried_through_untouched(self) -> None:
        """There is no operation to switch enforcement off, and the applier
        copies the original value rather than defaulting -- so the guarantee
        holds because of what the code does, not what an author remembered."""
        base = _hardware()
        flow, _ = apply_operations(
            base,
            [
                Operation(
                    OperationKind.ADD_DELIVERABLE,
                    "design",
                    "needs a loadable model",
                    value="cad_model",
                )
            ],
        )
        before = {p.id: p.enforce_deliverables for p in base.phases}
        after = {p.id: p.enforce_deliverables for p in flow.phases}
        assert after == {k: v for k, v in before.items() if k in after}

    def test_gates_are_carried_through_untouched(self) -> None:
        base = _hardware()
        flow, _ = apply_operations(
            base,
            [Operation(OperationKind.DROP_PHASE, "firmware", "no firmware in a bracket")],
        )
        for phase in flow.phases:
            original = next(p for p in base.phases if p.id == phase.id)
            assert phase.gate == original.gate

    def test_requiring_something_already_required_is_not_a_change(self) -> None:
        """It would put a line in the diff that describes nothing."""
        base = _hardware()
        existing = next(p for p in base.phases if p.required_deliverables)
        _, applied = apply_operations(
            base,
            [
                Operation(
                    OperationKind.ADD_DELIVERABLE,
                    existing.id,
                    "belt and braces",
                    value=existing.required_deliverables[0],
                )
            ],
        )
        assert applied == []


class TestPartialFailureIsNotTotalFailure:
    def test_an_unknown_phase_is_skipped_and_the_rest_apply(self) -> None:
        """A model that misremembers one phase id should not lose the four
        changes it got right."""
        flow, applied = apply_operations(
            _hardware(),
            [
                Operation(OperationKind.DROP_PHASE, "does_not_exist", "typo"),
                Operation(OperationKind.DROP_PHASE, "firmware", "no electronics at all"),
            ],
        )
        assert [o.phase_id for o in applied] == ["firmware"]
        assert "firmware" not in {p.id for p in flow.phases}

    def test_the_diff_describes_what_actually_happened(self) -> None:
        proposal = build_proposal(
            _hardware(),
            base_version=_version("hardware_v1"),
            operations=[
                Operation(OperationKind.DROP_PHASE, "nope", "typo"),
                Operation(OperationKind.DROP_PHASE, "firmware", "purely mechanical product"),
            ],
        )
        diff = proposal.diff()
        assert len(diff) == 1
        assert "firmware" in diff[0]
        assert "purely mechanical product" in diff[0]


class TestTheValidatorIsStillTheBackstop:
    def test_dropping_the_release_gate_produces_an_invalid_proposal(self) -> None:
        """Dropping phases *is* expressible, so this one the validator has to
        catch -- and a proposal that cannot be started must say so rather than
        being handed to a human who assumes it can."""
        base = _hardware()
        gated = [p.id for p in base.phases if p.gate and not p.gate.auto_approve]
        proposal = build_proposal(
            base,
            base_version=_version("hardware_v1"),
            operations=[Operation(OperationKind.DROP_PHASE, pid, "not needed") for pid in gated],
        )
        assert proposal.valid is False
        assert any("release-gate-exists" in str(v) for v in proposal.validation.violations)

    def test_an_untailored_proposal_is_valid(self) -> None:
        # Control: the shipping template passes, so an invalid proposal above
        # is the tailoring's doing and not the template's.
        proposal = build_proposal(_hardware(), base_version=_version("hardware_v1"), operations=[])
        assert proposal.valid, [str(v) for v in proposal.validation.violations]


# ── the acceptance criterion ─────────────────────────────────────────────


#: What a model would plausibly return for each intent. The LLM call is not
#: exercised here -- it is one `run_chat_turn` with a JSON prompt, and mocking
#: a provider to assert it returns what the mock was told to return proves
#: nothing. What is worth asserting is that *given* plausible operations, the
#: three intents produce visibly different flows and every one is startable.
_INTENTS = {
    "kitchen cabinet": (
        "hardware_v1",
        [
            Operation(OperationKind.DROP_PHASE, "electronics", "a cabinet has no electronics"),
            Operation(OperationKind.DROP_PHASE, "firmware", "nothing to program"),
            Operation(
                OperationKind.SET_DISCIPLINES,
                "design",
                "joinery and materials",
                value=["mechanical"],
            ),
        ],
    ),
    "quadcopter drone": (
        "hardware_v1",
        [
            Operation(
                OperationKind.ADD_DELIVERABLE,
                "simulation",
                "flight loads decide the airframe",
                value="simulation_result",
            ),
            Operation(
                OperationKind.SET_DISCIPLINES,
                "electronics",
                "power and control are the risk",
                value=["electronics", "firmware"],
            ),
        ],
    ),
    "6-DOF arm": (
        "hardware_v1",
        [
            Operation(
                OperationKind.ADD_DELIVERABLE,
                "design",
                "joint torques need a real model",
                value="cad_model",
            ),
            Operation(
                OperationKind.SET_DISCIPLINES,
                "simulation",
                "kinematics and structural both matter",
                value=["mechanical", "simulation"],
            ),
        ],
    ),
}


class TestTheFixturesAreNotVacuous:
    def test_every_phase_the_intents_name_actually_exists(self) -> None:
        """Without this, a phase rename turns the acceptance test into three
        identical untailored flows that all pass.

        ``apply_operations`` skips an unknown phase on purpose -- a model
        misremembering one id should not lose the rest -- and that leniency is
        exactly what would let these fixtures quietly stop testing anything.
        """
        for intent, (template_id, operations) in _INTENTS.items():
            real = {p.id for p in get_flow(template_id).phases}
            named = {op.phase_id for op in operations}
            assert named <= real, f"{intent} names phases that do not exist: {named - real}"

    def test_every_intent_actually_changes_its_template(self) -> None:
        for intent, (template_id, operations) in _INTENTS.items():
            proposal = build_proposal(
                get_flow(template_id), base_version=_version(template_id), operations=operations
            )
            assert proposal.operations, f"{intent} applied no operations"
            assert proposal.definition != get_flow(template_id), f"{intent} changed nothing"


class TestThreeIntentsThreeFlows:
    @pytest.mark.parametrize("intent", sorted(_INTENTS))
    def test_each_intent_produces_a_startable_flow(self, intent: str) -> None:
        template_id, operations = _INTENTS[intent]
        proposal = build_proposal(
            get_flow(template_id),
            base_version=_version(template_id),
            operations=operations,
            intent=intent,
        )
        assert proposal.valid, [str(v) for v in proposal.validation.violations]

    def test_the_three_flows_are_visibly_different(self) -> None:
        """ "Visibly" is the word in the ticket, so the comparison is on what a
        person would see: which phases are there, and what each demands."""
        shapes = {}
        for intent, (template_id, operations) in _INTENTS.items():
            proposal = build_proposal(
                get_flow(template_id),
                base_version=_version(template_id),
                operations=operations,
                intent=intent,
            )
            shapes[intent] = tuple(
                (p.id, p.required_deliverables, p.disciplines) for p in proposal.definition.phases
            )
        assert len({*shapes.values()}) == 3, shapes

    def test_the_cabinet_loses_the_electronics_phases(self) -> None:
        template_id, operations = _INTENTS["kitchen cabinet"]
        proposal = build_proposal(
            get_flow(template_id), base_version=_version(template_id), operations=operations
        )
        phase_ids = {p.id for p in proposal.definition.phases}
        assert "electronics" not in phase_ids
        assert "firmware" not in phase_ids
        # ...and still has to be verifiable and releasable.
        assert proposal.valid

    def test_every_change_carries_a_rationale(self) -> None:
        """A flow that differs from its template and cannot say why is a flow
        nobody can review, which is the whole reason it is held for a human."""
        for intent, (template_id, operations) in _INTENTS.items():
            proposal = build_proposal(
                get_flow(template_id),
                base_version=_version(template_id),
                operations=operations,
                intent=intent,
            )
            assert proposal.operations
            for line in proposal.diff():
                assert "—" in line and line.split("—", 1)[1].strip()


class TestProvenance:
    def test_the_proposal_records_which_template_and_version(self) -> None:
        proposal = build_proposal(_hardware(), base_version=_version("hardware_v1"), operations=[])
        assert proposal.base_template_id == "hardware_v1"
        assert proposal.base_version == _version("hardware_v1")
