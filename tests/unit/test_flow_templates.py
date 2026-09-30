"""Flow templates and the rules a tailored flow cannot break (FORGE-397).

Two jobs here, and the first is the one that would have hurt to get wrong.

**The migration changed nothing.** 530 lines of Python flow literals became
YAML. Hand-copying that much prose would have introduced differences nobody
finds for months, and the difference that matters -- a dropped
``required_deliverables`` entry -- turns a real gate into a decorative one.
The files were generated from the definitions; this proves the generator and
the loader agree.

**The validator actually fires.** A validator that passes everything is worse
than none, because it makes the flow look checked. Every rule here has a
negative control: a flow that breaks it, asserting the specific rule name
comes back.
"""

from __future__ import annotations

import pytest
import yaml

from orchestrator.design_flow.invariants import (
    FlowInvariantError,
    validate_flow,
)
from orchestrator.design_flow.spec import (
    FLOWS,
    FlowDefinition,
    Gate,
    Phase,
    flow_version,
    get_flow,
)
from orchestrator.design_flow.templates import (
    TEMPLATE_DIR,
    from_mapping,
    load_template,
    load_templates,
    to_mapping,
)

# ── the migration ────────────────────────────────────────────────────────


class TestTheTemplatesAreTheFlows:
    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_a_template_round_trips_to_an_identical_definition(self, flow_id: str) -> None:
        """Serialise the loaded flow and read it back: same object.

        Catches a field the writer emits and the loader ignores, which is how
        ``enforce_deliverables`` would quietly become True everywhere.
        """
        original = get_flow(flow_id)
        rebuilt = from_mapping(to_mapping(original, version="1.0.0"))
        assert rebuilt == original

    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_every_flow_has_a_template_file(self, flow_id: str) -> None:
        assert (TEMPLATE_DIR / f"{flow_id}.yaml").exists()

    def test_the_registry_is_exactly_what_is_on_disk(self) -> None:
        # A flow present in one and not the other is a flow that either
        # cannot be started or cannot be edited.
        assert sorted(FLOWS) == sorted(load_templates())

    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_required_deliverables_survived_the_move(self, flow_id: str) -> None:
        """The specific thing that would have been lost silently.

        A phase that lost its required deliverables still runs, still shows a
        gate, and no longer checks anything -- so the flow keeps passing and
        the gate becomes decoration.
        """
        raw = yaml.safe_load((TEMPLATE_DIR / f"{flow_id}.yaml").read_text())
        on_disk = {p["id"]: sorted(p.get("required_deliverables") or []) for p in raw["phases"]}
        in_memory = {p.id: sorted(p.required_deliverables) for p in get_flow(flow_id).phases}
        assert on_disk == in_memory

    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_every_template_declares_a_version(self, flow_id: str) -> None:
        assert flow_version(flow_id) == load_template(flow_id).version

    def test_a_template_without_a_version_is_refused(self, tmp_path, monkeypatch) -> None:
        """A run started from an unversioned template can never say what it
        ran. Refusing now is cheaper than finding out six weeks later."""
        import orchestrator.design_flow.templates as mod

        (tmp_path / "broken.yaml").write_text(
            yaml.dump({"id": "broken", "name": "B", "phases": []})
        )
        monkeypatch.setattr(mod, "TEMPLATE_DIR", tmp_path)
        with pytest.raises(ValueError, match="no 'version'"):
            mod.load_template("broken")

    def test_a_template_whose_id_disagrees_with_its_filename_is_refused(
        self, tmp_path, monkeypatch
    ) -> None:
        import orchestrator.design_flow.templates as mod

        (tmp_path / "a.yaml").write_text(
            yaml.dump({"id": "b", "version": "1.0.0", "name": "B", "phases": []})
        )
        monkeypatch.setattr(mod, "TEMPLATE_DIR", tmp_path)
        with pytest.raises(ValueError, match="declares id"):
            mod.load_template("a")


# ── the invariants ───────────────────────────────────────────────────────


def _phase(
    phase_id: str,
    *,
    produces: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    gate: Gate | None = None,
    enforce: bool = True,
) -> Phase:
    return Phase(
        id=phase_id,
        title=phase_id.title(),
        objective="do the thing",
        expected_artifacts=produces,
        required_deliverables=requires,
        enforce_deliverables=enforce,
        gate=gate,
    )


def _flow(*phases: Phase) -> FlowDefinition:
    return FlowDefinition(id="test", name="Test", phases=tuple(phases))


def _valid() -> FlowDefinition:
    """A minimal flow that satisfies every rule.

    Each negative-control test below breaks exactly one thing about this, so a
    failure names the rule that fired rather than the four that always do.
    """
    return _flow(
        _phase(
            "requirements",
            produces=("constraint_set",),
            requires=("constraint_set",),
            gate=Gate(name="Requirements sign-off"),
        ),
        _phase(
            "verify",
            produces=("test_plan",),
            requires=("test_plan",),
            gate=Gate(name="Release sign-off"),
        ),
    )


class TestTheBaselineIsValid:
    def test_the_fixture_passes(self) -> None:
        # If this ever fails, every negative control below is passing for the
        # wrong reason.
        assert validate_flow(_valid()).ok

    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_every_shipping_flow_passes_its_own_rules(self, flow_id: str) -> None:
        result = validate_flow(get_flow(flow_id))
        assert result.ok, [str(v) for v in result.violations]


def _rules(definition: FlowDefinition) -> set[str]:
    return {v.rule for v in validate_flow(definition).violations}


class TestEachRuleFires:
    def test_a_flow_with_no_phases(self) -> None:
        assert "has-phases" in _rules(_flow())

    def test_duplicate_phase_ids(self) -> None:
        base = _valid()
        dupe = _flow(*base.phases, _phase("requirements", produces=("x",)))
        assert "unique-phase-ids" in _rules(dupe)

    def test_no_release_gate(self) -> None:
        """A flow that runs to completion with nobody approving the result."""
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements check"),
            ),
            _phase(
                "verify",
                produces=("test_plan",),
                requires=("test_plan",),
                gate=Gate(name="Verification check"),
            ),
        )
        assert "release-gate-exists" in _rules(broken)

    def test_a_gate_with_nothing_required_of_it(self) -> None:
        """The no-pass-without-data rule. Such a gate cannot tell an empty
        phase from a complete one, and the human answering it cannot either."""
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase("verify", produces=("test_plan",), gate=Gate(name="Release sign-off")),
        )
        assert "no-pass-without-data" in _rules(broken)

    def test_enforcement_switched_off_under_a_gate(self) -> None:
        """Renders as a gate, a human answers it, and the check never runs --
        worse than no gate, because the approval now looks like evidence."""
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase(
                "verify",
                produces=("test_plan",),
                requires=("test_plan",),
                enforce=False,
                gate=Gate(name="Release sign-off"),
            ),
        )
        assert "gates-enforce-what-they-require" in _rules(broken)

    def test_a_gate_requiring_something_no_phase_produces(self) -> None:
        """The dependency error the ticket names: a gate that can never pass.

        It fails at the gate rather than where somebody could have noticed
        while writing it, which is the whole reason to check statically.
        """
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("simulation_result",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase(
                "verify",
                produces=("test_plan",),
                requires=("test_plan",),
                gate=Gate(name="Release sign-off"),
            ),
        )
        violations = validate_flow(broken).violations
        assert "deliverable-is-producible" in {v.rule for v in violations}
        named = next(v for v in violations if v.rule == "deliverable-is-producible")
        assert "simulation_result" in named.message
        assert named.phase_id == "requirements"

    def test_a_phase_may_require_what_it_produces_itself(self) -> None:
        """Negative control for the rule above. The phase runs before its own
        gate is evaluated, so requiring its own output is correct -- and every
        shipping flow does it. A rule that forbade this would be unusable."""
        fine = _valid()
        assert "deliverable-is-producible" not in _rules(fine)

    def test_requirements_with_nothing_verifying_them(self) -> None:
        """A twin full of claims nothing checks reads, on every dashboard,
        exactly like a product that passed."""
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase(
                "build",
                produces=("cad_model",),
                requires=("cad_model",),
                gate=Gate(name="Release sign-off"),
            ),
        )
        assert "requirements-are-verified" in _rules(broken)

    def test_a_flow_that_records_no_requirements_is_not_asked_to_verify_them(self) -> None:
        # Negative control: the rule must not fire on a flow that never
        # claimed anything in the first place.
        fine = _flow(
            _phase(
                "build",
                produces=("cad_model",),
                requires=("cad_model",),
                gate=Gate(name="Release sign-off"),
            ),
        )
        assert "requirements-are-verified" not in _rules(fine)


class TestTheErrorIsActionable:
    def test_it_names_every_broken_rule_not_just_the_first(self) -> None:
        """A model fixing one violation per round trip needs three attempts to
        learn what one response could have told it."""
        broken = _flow(_phase("only", gate=Gate(name="Check")))
        rules = _rules(broken)
        assert len(rules) >= 2

    def test_it_names_the_rule_and_the_phase(self) -> None:
        broken = _flow(
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase("verify", produces=("test_plan",), gate=Gate(name="Release sign-off")),
        )
        with pytest.raises(FlowInvariantError) as exc:
            validate_flow(broken).raise_if_invalid("test")
        text = str(exc.value)
        assert "no-pass-without-data" in text
        assert "verify" in text

    def test_auto_approved_gates_are_not_held_to_the_human_gate_rules(self) -> None:
        """An auto-approve gate is a checkpoint, not a human decision. Holding
        it to rules about what a human can tell apart would force every flow
        to carry deliverables on phases nobody reviews."""
        fine = _flow(
            _phase("setup", gate=Gate(name="Auto", auto_approve=True)),
            _phase(
                "requirements",
                produces=("constraint_set",),
                requires=("constraint_set",),
                gate=Gate(name="Requirements sign-off"),
            ),
            _phase(
                "verify",
                produces=("test_plan",),
                requires=("test_plan",),
                gate=Gate(name="Release sign-off"),
            ),
        )
        assert validate_flow(fine).ok
