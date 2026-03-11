"""Unit tests for the constraint engine."""

from uuid import uuid4

import pytest

from twin_core.constraint_engine import InMemoryConstraintEngine
from twin_core.constraint_engine.context import ConstraintContext, build_context
from twin_core.constraint_engine.resolver import (
    find_constrained_artifacts,
    resolve_constraints,
)
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import (
    Artifact,
    ArtifactType,
    Component,
    ConstrainedByEdge,
    Constraint,
    ConstraintSeverity,
    ConstraintStatus,
    DependsOnEdge,
    EdgeType,
)

# --- Helpers ---


def _make_artifact(
    name: str = "test",
    domain: str = "mechanical",
    art_type: ArtifactType = ArtifactType.CAD_MODEL,
) -> Artifact:
    return Artifact(
        name=name,
        type=art_type,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash="hash123",
        format="step",
        created_by="human",
    )


def _make_constraint(
    name: str = "test_constraint",
    expression: str = "True",
    severity: ConstraintSeverity = ConstraintSeverity.ERROR,
    domain: str = "mechanical",
    cross_domain: bool = False,
) -> Constraint:
    return Constraint(
        name=name,
        expression=expression,
        severity=severity,
        domain=domain,
        cross_domain=cross_domain,
        source="user",
    )


def _make_component(part_number: str = "STM32F4", manufacturer: str = "ST") -> Component:
    return Component(
        part_number=part_number,
        manufacturer=manufacturer,
        description="MCU",
    )


@pytest.fixture
def graph():
    return InMemoryGraphEngine()


@pytest.fixture
def engine(graph):
    return InMemoryConstraintEngine(graph)


@pytest.fixture
async def artifact(graph):
    a = _make_artifact()
    return await graph.add_node(a)


# --- TestAddConstraint ---


class TestAddConstraint:
    async def test_creates_constraint_node(self, engine, graph, artifact):
        c = _make_constraint()
        result = await engine.add_constraint(c, [artifact.id])
        assert result.id == c.id

        node = await graph.get_node(c.id)
        assert node is not None
        assert isinstance(node, Constraint)
        assert node.name == "test_constraint"

    async def test_creates_constrained_by_edges(self, engine, graph, artifact):
        c = _make_constraint()
        await engine.add_constraint(c, [artifact.id])

        edges = await graph.get_edges(
            artifact.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        assert len(edges) == 1
        assert edges[0].target_id == c.id

    async def test_sets_scope_local(self, engine, graph, artifact):
        c = _make_constraint(cross_domain=False)
        await engine.add_constraint(c, [artifact.id])

        edges = await graph.get_edges(
            artifact.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        assert edges[0].scope == "local"

    async def test_sets_scope_global(self, engine, graph, artifact):
        c = _make_constraint(cross_domain=True)
        await engine.add_constraint(c, [artifact.id])

        edges = await graph.get_edges(
            artifact.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        assert edges[0].scope == "global"

    async def test_multiple_artifacts(self, engine, graph):
        a1 = await graph.add_node(_make_artifact("a1"))
        a2 = await graph.add_node(_make_artifact("a2"))
        c = _make_constraint()
        await engine.add_constraint(c, [a1.id, a2.id])

        edges1 = await graph.get_edges(
            a1.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        edges2 = await graph.get_edges(
            a2.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        assert len(edges1) == 1
        assert len(edges2) == 1

    async def test_duplicate_raises(self, engine, artifact):
        c = _make_constraint()
        await engine.add_constraint(c, [artifact.id])
        with pytest.raises(ValueError, match="already exists"):
            await engine.add_constraint(c, [artifact.id])

    async def test_nonexistent_artifact_raises(self, engine):
        c = _make_constraint()
        with pytest.raises(ValueError, match="does not exist"):
            await engine.add_constraint(c, [uuid4()])


# --- TestGetConstraint ---


class TestGetConstraint:
    async def test_existing(self, engine, artifact):
        c = _make_constraint()
        await engine.add_constraint(c, [artifact.id])
        result = await engine.get_constraint(c.id)
        assert result is not None
        assert result.id == c.id
        assert result.name == "test_constraint"

    async def test_nonexistent(self, engine):
        result = await engine.get_constraint(uuid4())
        assert result is None


# --- TestRemoveConstraint ---


class TestRemoveConstraint:
    async def test_existing(self, engine, graph, artifact):
        c = _make_constraint()
        await engine.add_constraint(c, [artifact.id])

        result = await engine.remove_constraint(c.id)
        assert result is True

        # Node gone
        assert await graph.get_node(c.id) is None

        # Edges gone
        edges = await graph.get_edges(
            artifact.id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        assert len(edges) == 0

    async def test_nonexistent(self, engine):
        result = await engine.remove_constraint(uuid4())
        assert result is False


# --- TestConstraintResolver ---


class TestConstraintResolver:
    async def test_finds_direct_constraints(self, graph):
        a = await graph.add_node(_make_artifact())
        c = await graph.add_node(_make_constraint())
        await graph.add_edge(ConstrainedByEdge(source_id=a.id, target_id=c.id))

        result = await resolve_constraints(graph, [a.id])
        assert len(result) == 1
        assert result[0].id == c.id

    async def test_finds_cross_domain(self, graph):
        a = await graph.add_node(_make_artifact())
        c = await graph.add_node(_make_constraint(name="cross", cross_domain=True))
        # No edge linking them — cross-domain is discovered via flag

        result = await resolve_constraints(graph, [a.id])
        assert len(result) == 1
        assert result[0].id == c.id

    async def test_deduplicates(self, graph):
        a1 = await graph.add_node(_make_artifact("a1"))
        a2 = await graph.add_node(_make_artifact("a2"))
        c = await graph.add_node(_make_constraint())
        await graph.add_edge(ConstrainedByEdge(source_id=a1.id, target_id=c.id))
        await graph.add_edge(ConstrainedByEdge(source_id=a2.id, target_id=c.id))

        result = await resolve_constraints(graph, [a1.id, a2.id])
        assert len(result) == 1

    async def test_empty_graph(self, graph):
        result = await resolve_constraints(graph, [uuid4()])
        assert result == []

    async def test_find_constrained_artifacts(self, graph):
        a1 = await graph.add_node(_make_artifact("a1"))
        a2 = await graph.add_node(_make_artifact("a2"))
        c = await graph.add_node(_make_constraint())
        await graph.add_edge(ConstrainedByEdge(source_id=a1.id, target_id=c.id))
        await graph.add_edge(ConstrainedByEdge(source_id=a2.id, target_id=c.id))

        result = await find_constrained_artifacts(graph, c.id)
        assert set(result) == {a1.id, a2.id}


# --- TestConstraintContext ---


class TestConstraintContext:
    async def test_artifact_by_name(self, graph):
        a = await graph.add_node(_make_artifact("bracket"))
        ctx = await build_context(graph)
        found = ctx.artifact("bracket")
        assert found.id == a.id

    async def test_artifact_not_found_raises(self, graph):
        await graph.add_node(_make_artifact("bracket"))
        ctx = await build_context(graph)
        with pytest.raises(KeyError, match="not found"):
            ctx.artifact("nonexistent")

    async def test_filter_by_domain(self, graph):
        await graph.add_node(_make_artifact("mech1", domain="mechanical"))
        await graph.add_node(_make_artifact("elec1", domain="electronics"))
        ctx = await build_context(graph)

        mech = ctx.artifacts(domain="mechanical")
        assert len(mech) == 1
        assert mech[0].name == "mech1"

    async def test_filter_by_type(self, graph):
        await graph.add_node(_make_artifact("sch1", art_type=ArtifactType.SCHEMATIC))
        await graph.add_node(_make_artifact("cad1", art_type=ArtifactType.CAD_MODEL))
        ctx = await build_context(graph)

        schematics = ctx.artifacts(type=ArtifactType.SCHEMATIC)
        assert len(schematics) == 1
        assert schematics[0].name == "sch1"

    async def test_components(self, graph):
        await graph.add_node(_make_component("STM32F4"))
        await graph.add_node(_make_component("LM1117"))
        ctx = await build_context(graph)

        comps = ctx.components()
        assert len(comps) == 2

    async def test_dependents(self, graph):
        a1 = await graph.add_node(_make_artifact("source"))
        a2 = await graph.add_node(_make_artifact("target"))
        await graph.add_edge(DependsOnEdge(source_id=a1.id, target_id=a2.id))
        ctx = await build_context(graph)

        deps = ctx.dependents(a2.id)
        assert len(deps) == 1
        assert deps[0].id == a1.id

    async def test_no_dependents(self, graph):
        a = await graph.add_node(_make_artifact())
        ctx = await build_context(graph)
        assert ctx.dependents(a.id) == []


# --- TestExpressionEvaluation ---


class TestExpressionEvaluation:
    def test_true_passes(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("True", ctx)
        assert status == ConstraintStatus.PASS

    def test_false_fails(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("False", ctx)
        assert status == ConstraintStatus.FAIL

    def test_context_access(self, graph):
        a = _make_artifact("bracket")
        ctx = ConstraintContext(
            artifacts_by_name={"bracket": a},
            artifacts_by_id={a.id: a},
            all_components=[],
            dependency_map={},
        )
        status, _ = InMemoryConstraintEngine._eval_expression(
            "ctx.artifact('bracket').domain == 'mechanical'", ctx
        )
        assert status == ConstraintStatus.PASS

    def test_all_builtin_works(self):
        a1 = _make_artifact("a1", domain="mechanical")
        a2 = _make_artifact("a2", domain="mechanical")
        ctx = ConstraintContext(
            artifacts_by_name={"a1": a1, "a2": a2},
            artifacts_by_id={a1.id: a1, a2.id: a2},
            all_components=[],
            dependency_map={},
        )
        status, _ = InMemoryConstraintEngine._eval_expression(
            "all(a.domain == 'mechanical' for a in ctx.artifacts())", ctx
        )
        assert status == ConstraintStatus.PASS

    def test_len_builtin_works(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, _ = InMemoryConstraintEngine._eval_expression("len([1, 2, 3]) == 3", ctx)
        assert status == ConstraintStatus.PASS

    def test_syntax_error_skipped(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("def foo():", ctx)
        assert status == ConstraintStatus.SKIPPED
        assert "Syntax error" in msg

    def test_runtime_error_skipped(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("1 / 0", ctx)
        assert status == ConstraintStatus.SKIPPED
        assert "Runtime error" in msg

    def test_import_blocked(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("__import__('os')", ctx)
        assert status == ConstraintStatus.SKIPPED

    def test_open_blocked(self):
        ctx = ConstraintContext({}, {}, [], {})
        status, msg = InMemoryConstraintEngine._eval_expression("open('/etc/passwd')", ctx)
        assert status == ConstraintStatus.SKIPPED


# --- TestEvaluate ---


class TestEvaluate:
    async def test_all_pass(self, engine, graph, artifact):
        c = _make_constraint(expression="True")
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.passed is True
        assert result.violations == []
        assert result.warnings == []
        assert result.evaluated_count == 1

    async def test_error_fail_blocks(self, engine, graph, artifact):
        c = _make_constraint(
            expression="False",
            severity=ConstraintSeverity.ERROR,
        )
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.violations[0].constraint_id == c.id
        assert result.violations[0].severity == ConstraintSeverity.ERROR

    async def test_warning_fail_allows(self, engine, graph, artifact):
        c = _make_constraint(
            expression="False",
            severity=ConstraintSeverity.WARNING,
        )
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.passed is True
        assert len(result.warnings) == 1
        assert result.warnings[0].severity == ConstraintSeverity.WARNING

    async def test_info_fail_allows(self, engine, graph, artifact):
        c = _make_constraint(
            expression="False",
            severity=ConstraintSeverity.INFO,
        )
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.passed is True
        assert len(result.warnings) == 1
        assert result.warnings[0].severity == ConstraintSeverity.INFO

    async def test_updates_constraint_status(self, engine, graph, artifact):
        c = _make_constraint(expression="True")
        await engine.add_constraint(c, [artifact.id])

        await engine.evaluate([artifact.id])

        updated = await engine.get_constraint(c.id)
        assert updated is not None
        assert updated.status == ConstraintStatus.PASS
        assert updated.last_evaluated is not None

    async def test_updates_failed_status(self, engine, graph, artifact):
        c = _make_constraint(expression="False", severity=ConstraintSeverity.ERROR)
        await engine.add_constraint(c, [artifact.id])

        await engine.evaluate([artifact.id])

        updated = await engine.get_constraint(c.id)
        assert updated is not None
        assert updated.status == ConstraintStatus.FAIL

    async def test_skipped_count(self, engine, graph, artifact):
        c = _make_constraint(expression="def foo():")
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.skipped_count == 1
        assert result.evaluated_count == 0
        assert result.passed is True

    async def test_duration_positive(self, engine, graph, artifact):
        c = _make_constraint(expression="True")
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.duration_ms >= 0

    async def test_no_constraints_passes(self, engine, artifact):
        result = await engine.evaluate([artifact.id])
        assert result.passed is True
        assert result.evaluated_count == 0

    async def test_context_expression(self, engine, graph, artifact):
        c = _make_constraint(
            expression="ctx.artifact('test').domain == 'mechanical'",
        )
        await engine.add_constraint(c, [artifact.id])

        result = await engine.evaluate([artifact.id])
        assert result.passed is True


# --- TestEvaluateAll ---


class TestEvaluateAll:
    async def test_runs_every_constraint(self, engine, graph):
        a1 = await graph.add_node(_make_artifact("a1"))
        a2 = await graph.add_node(_make_artifact("a2"))

        c1 = _make_constraint(name="c1", expression="True")
        c2 = _make_constraint(name="c2", expression="True")
        await engine.add_constraint(c1, [a1.id])
        await engine.add_constraint(c2, [a2.id])

        result = await engine.evaluate_all()
        assert result.passed is True
        assert result.evaluated_count == 2

    async def test_includes_cross_domain(self, engine, graph):
        a = await graph.add_node(_make_artifact())
        c_local = _make_constraint(name="local", expression="True")
        c_cross = _make_constraint(name="cross", expression="True", cross_domain=True)
        await engine.add_constraint(c_local, [a.id])
        await engine.add_constraint(c_cross, [a.id])

        result = await engine.evaluate_all()
        assert result.evaluated_count == 2


# --- TestIntegration ---


class TestIntegration:
    async def test_cross_domain_constraint_evaluation(self, engine, graph):
        mech = await graph.add_node(_make_artifact("bracket", domain="mechanical"))
        elec = await graph.add_node(_make_artifact("pcb", domain="electronics"))

        # Cross-domain constraint: all artifacts must have a non-empty name
        c = _make_constraint(
            name="non_empty_names",
            expression="all(len(a.name) > 0 for a in ctx.artifacts())",
            cross_domain=True,
        )
        await engine.add_constraint(c, [mech.id])

        # Evaluate only electronics artifact — cross-domain still picked up
        result = await engine.evaluate([elec.id])
        assert result.passed is True
        assert result.evaluated_count == 1

    async def test_mixed_results(self, engine, graph):
        a = await graph.add_node(_make_artifact())

        c_pass = _make_constraint(
            name="pass_constraint",
            expression="True",
            severity=ConstraintSeverity.ERROR,
        )
        c_warn = _make_constraint(
            name="warn_constraint",
            expression="False",
            severity=ConstraintSeverity.WARNING,
        )
        c_fail = _make_constraint(
            name="fail_constraint",
            expression="False",
            severity=ConstraintSeverity.ERROR,
        )
        await engine.add_constraint(c_pass, [a.id])
        await engine.add_constraint(c_warn, [a.id])
        await engine.add_constraint(c_fail, [a.id])

        result = await engine.evaluate([a.id])
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.violations[0].constraint_name == "fail_constraint"
        assert len(result.warnings) == 1
        assert result.warnings[0].constraint_name == "warn_constraint"
        assert result.evaluated_count == 3

    async def test_component_count_constraint(self, engine, graph):
        a = await graph.add_node(_make_artifact())
        await graph.add_node(_make_component("STM32F4"))
        await graph.add_node(_make_component("LM1117"))

        c = _make_constraint(
            name="max_components",
            expression="len(ctx.components()) <= 10",
        )
        await engine.add_constraint(c, [a.id])

        result = await engine.evaluate([a.id])
        assert result.passed is True

    async def test_dependency_constraint(self, engine, graph):
        source = await graph.add_node(_make_artifact("firmware"))
        target = await graph.add_node(_make_artifact("pinmap"))
        await graph.add_edge(DependsOnEdge(source_id=source.id, target_id=target.id))

        c = _make_constraint(
            name="pinmap_has_dependents",
            expression="len(ctx.dependents(ctx.artifact('pinmap').id)) > 0",
        )
        await engine.add_constraint(c, [target.id])

        result = await engine.evaluate([target.id])
        assert result.passed is True
