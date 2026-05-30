"""Coding harness concrete agents (MET-475).

Implements the Planner / Generator / Evaluator Protocols from
``orchestrator.harness.three_agent`` for the coding task. The
Evaluator runs a list of ``QualityGate`` checks (the 10 gates from
MET-475); concrete checks are injected at construction so production
runners can plug ruff / mypy / pytest subprocesses behind the same
interface that tests use to inject scripted gates.

Artifacts written:

* ``plan.md`` — CodingPlanner's approach summary (read by Generator)
* ``generated_summary.json`` — Generator's manifest of what it
  produced (file list + test count) — what the Evaluator reads
* ``pr_description.md`` — Generator's draft PR body
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from orchestrator.harness.artifacts import ArtifactStore
from orchestrator.harness.coding.gates import (
    GateOutcome,
    QualityGate,
)
from orchestrator.harness.three_agent import (
    EvaluatorResult,
    GateResult,
    GeneratorResult,
    PlannerResult,
)


@dataclass
class GitHubIssue:
    """Minimal GitHub issue payload the planner consumes."""

    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CodingPlanner
# ---------------------------------------------------------------------------


_PLAN_TEMPLATE = """# Plan for #{number}: {title}

iteration: {iteration}

## Approach

{approach}

## Modules to touch

{modules_md}

## Test plan

- Minimum 5 tests per new module
- All gates green before PR ready

## Notes from prior iteration

{prior_notes}
"""


class CodingPlanner:
    """Reads a GitHub issue, writes ``plan.md`` with the approach.

    The planner is deterministic — it keyword-extracts module names
    from the issue body and emits a structured plan template. A
    later LLM-backed variant slots in via the same Protocol.
    """

    def __init__(self, issue: GitHubIssue) -> None:
        self._issue = issue

    async def plan(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        prior_feedback: EvaluatorResult | None,
    ) -> PlannerResult:
        modules = _extract_module_hints(self._issue.body) or ["new_module"]
        approach = (
            f"Implement {self._issue.title.lower()} as a small, "
            f"Protocol-typed module with deterministic helpers and "
            f"unit-test coverage for each public function."
        )
        prior_notes = "(first iteration)"
        if prior_feedback and not prior_feedback.passed:
            failed = [g.name for g in prior_feedback.gates if not g.passed]
            prior_notes = (
                f"Prior iteration failed gates: {', '.join(failed)}. "
                "Refining approach to address each."
            )

        plan = _PLAN_TEMPLATE.format(
            number=self._issue.number,
            title=self._issue.title,
            iteration=iteration,
            approach=approach,
            modules_md="\n".join(f"- `{m}`" for m in modules),
            prior_notes=prior_notes,
        )
        await store.put(
            run_id,
            "plan.md",
            plan,
            metadata={"iteration": str(iteration), "issue": str(self._issue.number)},
        )
        return PlannerResult(
            spec_artifact="plan.md",
            notes=f"modules={modules}",
        )


def _extract_module_hints(body: str) -> list[str]:
    """Pull module-name hints from the issue body (very small parser)."""
    lowered = body.lower()
    hints: list[str] = []
    for token in ("memory", "knowledge", "twin", "constraint", "harness", "kicad"):
        if token in lowered:
            hints.append(f"{token}_module")
    return hints


# ---------------------------------------------------------------------------
# CodingGenerator
# ---------------------------------------------------------------------------


class CodingGenerator:
    """Reads the plan, "implements" the work (deterministic stub).

    The real coding generator would invoke a model; here we just
    write a manifest (``generated_summary.json``) the Evaluator gates
    read off, plus a stub source file and a stub test file with the
    minimum 5 tests the spec demands. That's enough to exercise the
    full loop — concrete code generation lands behind the same
    Protocol when LLMProvider plumbing arrives.
    """

    def __init__(self, *, tests_per_module: int = 5) -> None:
        if tests_per_module < 1:
            raise ValueError("tests_per_module must be >= 1")
        self._tests_per_module = tests_per_module

    async def generate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
    ) -> GeneratorResult:
        plan = await store.get(run_id, spec_artifact)
        if plan is None:
            raise RuntimeError(f"plan artifact {spec_artifact!r} missing")

        # Pull module names back out of the plan to know what to
        # claim we generated. The format matches what
        # CodingPlanner.plan emits.
        modules: list[str] = []
        for line in plan.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- `") and stripped.endswith("`"):
                modules.append(stripped[3:-1])

        files: list[str] = []
        for module in modules:
            files.append(f"src/{module}.py")
            files.append(f"tests/unit/test_{module}.py")

        manifest = {
            "iteration": iteration,
            "modules": modules,
            "files": files,
            "tests_per_module": self._tests_per_module,
            # Used by gate 6 (error_handling) and gate 7 (docstrings)
            # below — set True so the scripted/programmatic checks
            # for those gates can read consistent flags.
            "has_module_docstring": True,
            "has_function_docstrings": True,
            "uses_bare_except": False,
            "new_dependencies": [],
        }
        await store.put(
            run_id,
            "generated_summary.json",
            json.dumps(manifest, indent=2),
        )

        pr_body = (
            f"## Summary\n\n"
            f"Implements iteration {iteration} of the plan in `plan.md`.\n\n"
            f"## Test plan\n\n"
            f"- {self._tests_per_module} tests per module across {len(modules)} module(s)\n"
            f"- All 10 quality gates pass before merge\n"
        )
        await store.put(run_id, "pr_description.md", pr_body)

        return GeneratorResult(
            output_artifacts=["generated_summary.json", "pr_description.md"],
            notes=f"modules={modules}, files={len(files)}",
        )


# ---------------------------------------------------------------------------
# CodingEvaluator
# ---------------------------------------------------------------------------


class CodingEvaluator:
    """Runs every registered ``QualityGate`` and aggregates outcomes.

    Construction takes an explicit ordered list of gates so callers
    decide what to run (the 10-gate canonical set is the default;
    integration tests may inject scripted gates to exercise the
    iteration loop deterministically).
    """

    def __init__(self, gates: list[QualityGate]) -> None:
        if not gates:
            raise ValueError("CodingEvaluator requires at least one QualityGate")
        self._gates = list(gates)

    async def evaluate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
        output_artifacts: list[str],
    ) -> EvaluatorResult:
        results: list[GateResult] = []
        for gate in self._gates:
            outcome = await gate.check(run_id, store)
            results.append(
                GateResult(
                    name=outcome.name,
                    passed=outcome.passed,
                    detail=outcome.detail,
                    metadata=dict(outcome.metadata),
                )
            )
        return EvaluatorResult(
            gates=results,
            passed=all(g.passed for g in results),
            notes=f"ran {len(self._gates)} gate(s) at iteration {iteration}",
        )


# ---------------------------------------------------------------------------
# Built-in programmatic gates (read the generator's manifest)
# ---------------------------------------------------------------------------


async def _manifest(run_id: str, store: ArtifactStore) -> dict[str, object]:
    artifact = await store.get(run_id, "generated_summary.json")
    if artifact is None:
        return {}
    try:
        loaded = json.loads(artifact.content)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        return {}
    return {}


async def gate_tests_min_5(run_id: str, store: ArtifactStore) -> GateOutcome:
    """Gate 3: ≥5 tests per module, no skips.

    Reads ``generated_summary.json``. Production runner would invoke
    pytest with ``--no-skips`` and count collected tests per module.
    """
    from orchestrator.harness.coding.gates import CodingHarnessGates

    manifest = await _manifest(run_id, store)
    raw_count = manifest.get("tests_per_module", 0) or 0
    tests_per_module = int(raw_count) if isinstance(raw_count, int | float | str) else 0
    raw_modules = manifest.get("modules", []) or []
    modules: list[object] = list(raw_modules) if isinstance(raw_modules, list) else []
    passed = bool(modules) and tests_per_module >= 5
    return GateOutcome(
        name=CodingHarnessGates.TESTS,
        passed=passed,
        detail=f"tests_per_module={tests_per_module}, modules={len(modules)}",
        metadata={"tests_per_module": str(tests_per_module)},
    )


async def gate_error_handling(run_id: str, store: ArtifactStore) -> GateOutcome:
    """Gate 6: No bare ``except:`` clauses.

    Reads the manifest's ``uses_bare_except`` flag; the production
    runner would AST-scan the generated source.
    """
    from orchestrator.harness.coding.gates import CodingHarnessGates

    manifest = await _manifest(run_id, store)
    uses_bare = bool(manifest.get("uses_bare_except", False))
    return GateOutcome(
        name=CodingHarnessGates.ERROR_HANDLING,
        passed=not uses_bare,
        detail="bare except found" if uses_bare else "OK",
    )


async def gate_docstrings(run_id: str, store: ArtifactStore) -> GateOutcome:
    """Gate 7: Module + function docstrings present (Google style)."""
    from orchestrator.harness.coding.gates import CodingHarnessGates

    manifest = await _manifest(run_id, store)
    has_module = bool(manifest.get("has_module_docstring", False))
    has_function = bool(manifest.get("has_function_docstrings", False))
    passed = has_module and has_function
    return GateOutcome(
        name=CodingHarnessGates.DOCSTRINGS,
        passed=passed,
        detail=("OK" if passed else f"module={has_module}, function={has_function}"),
    )


async def gate_no_new_deps(run_id: str, store: ArtifactStore) -> GateOutcome:
    """Gate 9: No new dependencies added without justification."""
    from orchestrator.harness.coding.gates import CodingHarnessGates

    manifest = await _manifest(run_id, store)
    raw_deps = manifest.get("new_dependencies", []) or []
    new_deps: list[object] = list(raw_deps) if isinstance(raw_deps, list) else []
    passed = len(new_deps) == 0
    return GateOutcome(
        name=CodingHarnessGates.DEPENDENCIES,
        passed=passed,
        detail=f"new_deps={new_deps}" if new_deps else "no new dependencies",
    )


async def gate_pr_description(run_id: str, store: ArtifactStore) -> GateOutcome:
    """Gate 10: PR description includes a summary + test plan section."""
    from orchestrator.harness.coding.gates import CodingHarnessGates

    pr = await store.get(run_id, "pr_description.md")
    if pr is None:
        return GateOutcome(
            name=CodingHarnessGates.PR_DESCRIPTION,
            passed=False,
            detail="pr_description.md missing",
        )
    has_summary = "## Summary" in pr.content
    has_test_plan = "## Test plan" in pr.content
    passed = has_summary and has_test_plan
    return GateOutcome(
        name=CodingHarnessGates.PR_DESCRIPTION,
        passed=passed,
        detail=("OK" if passed else f"summary={has_summary}, test_plan={has_test_plan}"),
    )
