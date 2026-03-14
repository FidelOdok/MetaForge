"""Integration test: work_products -> constraints -> gate evaluation -> readiness score.

End-to-end workflow testing the constraint engine and gate engine together.
"""

import pytest

from twin_core.constraint_engine import InMemoryConstraintEngine
from twin_core.constraint_engine.yaml_loader import convert_to_constraints, load_rules_from_file
from twin_core.gate_engine.engine import InMemoryGateEngine
from twin_core.gate_engine.models import GateCriterion, GateDefinition, GatePhase
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

# --- Helpers ---


def _make_work_product(
    name: str,
    domain: str = "mechanical",
    art_type: WorkProductType = WorkProductType.CAD_MODEL,
    metadata: dict | None = None,
) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=art_type,
        domain=domain,
        file_path=f"test/{name}.step",
        content_hash="hash",
        format="step",
        created_by="test",
        metadata=metadata or {},
    )


@pytest.fixture
def graph():
    return InMemoryGraphEngine()


@pytest.fixture
def constraint_engine(graph):
    return InMemoryConstraintEngine(graph)


@pytest.fixture
def gate_engine():
    return InMemoryGateEngine()


# --- TestEndToEndGateWorkflow ---


class TestEndToEndGateWorkflow:
    """End-to-end: create work_products -> run constraints -> evaluate gate -> readiness."""

    async def test_full_workflow_passing(self, graph, constraint_engine, gate_engine):
        """Full pipeline: work_products pass constraints, gate passes."""
        # 1. Create work_products in the graph
        bracket = await graph.add_node(
            _make_work_product("bracket", domain="mechanical", metadata={"weight_grams": 50})
        )
        pcb = await graph.add_node(
            _make_work_product(
                "main_pcb",
                domain="electronics",
                art_type=WorkProductType.SCHEMATIC,
                metadata={"erc_errors": 0},
            )
        )

        # 2. Add constraints
        from twin_core.models.constraint import Constraint

        c1 = Constraint(
            name="weight_ok",
            expression=(
                "sum(a.metadata.get('weight_grams', 0)"
                " for a in ctx.work_products(domain='mechanical')) <= 500"
            ),
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
        )
        c2 = Constraint(
            name="erc_clean",
            expression=(
                "all(a.metadata.get('erc_errors', 0) == 0"
                " for a in ctx.work_products(domain='electronics'))"
            ),
            severity=ConstraintSeverity.ERROR,
            domain="electronics",
            source="test",
        )
        await constraint_engine.add_constraint(c1, [bracket.id])
        await constraint_engine.add_constraint(c2, [pcb.id])

        # 3. Run constraints
        result = await constraint_engine.evaluate_all()
        assert result.passed is True
        assert result.evaluated_count == 2

        # 4. Define gate with criteria based on constraint results
        gate = GateDefinition(
            phase=GatePhase.EVT,
            name="EVT Gate",
            threshold=0.8,
            criteria=[
                GateCriterion(
                    name="constraints_pass",
                    weight=3.0,
                    score=1.0 if result.passed else 0.0,
                    required=True,
                ),
                GateCriterion(
                    name="bom_review",
                    weight=1.0,
                    score=0.9,
                ),
            ],
        )
        await gate_engine.define_gate(gate)

        # 5. Evaluate gate readiness
        readiness = await gate_engine.evaluate(gate.id)
        assert readiness.passed is True
        assert readiness.weighted_score >= 0.8

        # 6. Attempt transition
        transition = await gate_engine.attempt_transition(gate.id)
        assert transition.allowed is True

    async def test_full_workflow_constraint_blocks_gate(
        self, graph, constraint_engine, gate_engine
    ):
        """Constraint failure should cause gate to fail."""
        # 1. Create work_product that will fail constraint
        heavy_part = await graph.add_node(
            _make_work_product("heavy", domain="mechanical", metadata={"weight_grams": 9999})
        )

        # 2. Add constraint that will fail
        from twin_core.models.constraint import Constraint

        c = Constraint(
            name="weight_limit",
            expression=(
                "sum(a.metadata.get('weight_grams', 0)"
                " for a in ctx.work_products(domain='mechanical')) <= 500"
            ),
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
        )
        await constraint_engine.add_constraint(c, [heavy_part.id])

        # 3. Constraints fail
        result = await constraint_engine.evaluate_all()
        assert result.passed is False

        # 4. Gate reflects failure
        gate = GateDefinition(
            phase=GatePhase.EVT,
            name="EVT Gate",
            threshold=0.8,
            criteria=[
                GateCriterion(
                    name="constraints_pass",
                    weight=3.0,
                    score=0.0,
                    required=True,
                ),
            ],
        )
        await gate_engine.define_gate(gate)

        readiness = await gate_engine.evaluate(gate.id)
        assert readiness.passed is False
        assert len(readiness.blockers) >= 1

        transition = await gate_engine.attempt_transition(gate.id)
        assert transition.allowed is False

    async def test_multi_phase_gate_progression(self, graph, constraint_engine, gate_engine):
        """EVT -> DVT progression with constraint-driven scores."""
        art = await graph.add_node(_make_work_product("part", metadata={"weight_grams": 100}))

        from twin_core.models.constraint import Constraint

        c = Constraint(
            name="weight_ok",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
        )
        await constraint_engine.add_constraint(c, [art.id])
        result = await constraint_engine.evaluate_all()
        assert result.passed is True

        # EVT gate
        evt = GateDefinition(
            phase=GatePhase.EVT,
            name="EVT",
            threshold=0.8,
            criteria=[GateCriterion(name="basic", weight=1.0, score=1.0, required=True)],
        )
        await gate_engine.define_gate(evt)
        evt_transition = await gate_engine.attempt_transition(evt.id)
        assert evt_transition.allowed is True

        # DVT gate
        dvt = GateDefinition(
            phase=GatePhase.DVT,
            name="DVT",
            threshold=0.8,
            criteria=[
                GateCriterion(name="thermal_test", weight=1.0, score=0.95),
                GateCriterion(name="vibration_test", weight=1.0, score=0.85),
            ],
        )
        await gate_engine.define_gate(dvt)
        dvt_transition = await gate_engine.attempt_transition(dvt.id)
        assert dvt_transition.allowed is True
        assert dvt_transition.from_phase == GatePhase.EVT

    async def test_yaml_rules_to_gate_evaluation(
        self, graph, constraint_engine, gate_engine, tmp_path
    ):
        """Load YAML rules, evaluate constraints, use result in gate."""
        # Write a rule file
        rule_file = tmp_path / "test_rules.yaml"
        rule_file.write_text(
            """
domain: mechanical
version: "1.0"
rules:
  - name: has_work_products
    description: "Must have at least one work_product"
    condition: "len(ctx.work_products()) > 0"
    severity: critical
""",
            encoding="utf-8",
        )

        # Load and register rules
        ruleset = load_rules_from_file(rule_file)
        constraints = convert_to_constraints(ruleset)
        assert len(constraints) == 1

        art = await graph.add_node(_make_work_product("test_part"))
        await constraint_engine.add_constraint(constraints[0], [art.id])

        # Evaluate
        result = await constraint_engine.evaluate_all()
        assert result.passed is True

        # Feed into gate
        gate = GateDefinition(
            phase=GatePhase.EVT,
            name="YAML-driven EVT",
            threshold=0.8,
            criteria=[
                GateCriterion(
                    name="yaml_constraints",
                    weight=1.0,
                    score=1.0 if result.passed else 0.0,
                    required=True,
                ),
            ],
        )
        await gate_engine.define_gate(gate)
        readiness = await gate_engine.evaluate(gate.id)
        assert readiness.passed is True

    async def test_snapshot_history_tracks_progress(self, gate_engine):
        """Verify snapshots capture score improvements over time."""
        gate = GateDefinition(
            phase=GatePhase.EVT,
            name="Progress Gate",
            threshold=0.9,
            criteria=[
                GateCriterion(name="testing", weight=1.0, score=0.3),
                GateCriterion(name="review", weight=1.0, score=0.5),
            ],
        )
        await gate_engine.define_gate(gate)

        # First evaluation (failing)
        score1 = await gate_engine.evaluate(gate.id)
        assert score1.passed is False

        # Improve scores
        await gate_engine.update_criterion(gate.id, "testing", 0.9)
        await gate_engine.update_criterion(gate.id, "review", 1.0)

        # Second evaluation (passing)
        score2 = await gate_engine.evaluate(gate.id)
        assert score2.passed is True

        # Verify snapshots
        snapshots = await gate_engine.get_snapshots(gate.id)
        assert len(snapshots) == 2
        assert snapshots[0].score.weighted_score < snapshots[1].score.weighted_score
