"""Deciding questions asked once at intent; the direction picked at the concept gate (FORGE-766).

The kickoff phase duplicated intent (who uses it, the load, the budget) and
the concept phase (two or three directions and a pick), and fixed a direction
before the requirements and the process existed. It is folded: intent records
the deciding answers with their sources and earlier runs' lessons; every later
brief carries the answers; the concept gate is where a direction is picked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.runs.flow_brain import ReActPhaseBrain
from api_gateway.runs.prior_lessons import _ledger_records, collect_prior_lessons
from api_gateway.twin.decision_recorder import make_decision_recorder
from orchestrator.design_flow.context import (
    CONCEPT_PHASE_ID,
    DECIDING_QUESTIONS_MISSING,
    INTENT_PHASE_ID,
    ConceptOption,
    DecidingAnswers,
    concept_pick_note,
    deciding_answers_gap,
    parse_concept_options,
    parse_deciding_answers,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.prior_lessons import (
    MAX_PRIOR_LESSONS,
    PriorLesson,
    lessons_from_runs,
    merge_lessons,
    render_lessons,
)
from orchestrator.design_flow.spec import Phase
from orchestrator.design_flow.templates import load_templates
from twin_core.api import InMemoryTwinAPI

PROJECT = "11111111-1111-1111-1111-111111111111"

ANSWERS: dict[str, Any] = {
    "questions": [
        {"id": "users", "question": "Who uses it?", "answer": "adults at a desk",
         "source": "stated"},
        {"id": "load", "question": "What load?", "answer": "120 kg person", "source": "person"},
        {"id": "style", "question": "What style?", "answer": "plain birch", "source": "assumed"},
        {"id": "budget", "question": "What budget?", "source": "open"},
    ],
    "lessons": ["the last concept had no joints", {"text": "seat too thin", "source_run": "r0"}],
}  # fmt: skip

OPTIONS = parse_concept_options(
    [
        {"name": "Four-leg dowelled", "summary": "rigid, cheap dowels", "selected": True},
        {"name": "Slot-together flat-pack", "summary": "no fixings, ships flat"},
    ]
)


def _answers(**change: Any) -> DecidingAnswers:
    return parse_deciding_answers({**ANSWERS, **change})


# -- the record --------------------------------------------------------------


class TestTheRecord:
    def test_the_answers_parse_with_their_sources(self) -> None:
        a = _answers()
        assert [q.source for q in a.questions] == ["stated", "person", "assumed", "open"]
        assert [lesson.source_run for lesson in a.lessons] == ["", "r0"]
        assert parse_deciding_answers(a.as_metadata()) == a
        assert "1 assumed, 1 open" in a.summary()

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"questions": []}, "deciding questions"),
            ({"questions": [{"id": f"q{i}", "question": "?", "answer": "a"} for i in range(9)]},
             "at most 8"),
            ({"questions": [{"id": "a", "question": "?", "answer": "x"}] * 2}, "unique"),
            ({"questions": [{"id": "a", "question": "?", "source": "person"}]}, "needs an answer"),
            ({"questions": [{"id": "a", "question": "?", "answer": "x", "source": "guess"}]},
             "source"),
            ({"lessons": [{"source_run": "r"}]}, "needs its text"),
        ],
    )  # fmt: skip
    def test_malformed_answers_are_refused(self, change: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            _answers(**change)

    def test_the_brief_carries_the_answers(self) -> None:
        brief = _answers().brief()
        assert "do not ask them again" in brief
        assert "Who uses it? adults at a desk [stated]" in brief
        assert "What budget? (open: not yet answered) [open]" in brief
        assert "the last concept had no joints" in brief
        assert "the last concept had no joints" not in _answers().brief(include_lessons=False)

    def test_no_record_is_the_gap(self) -> None:
        assert deciding_answers_gap(None) == DECIDING_QUESTIONS_MISSING
        assert deciding_answers_gap(_answers()) is None


@pytest.mark.asyncio
async def test_the_decision_carries_the_answers_and_refuses_bad_ones() -> None:
    twin = InMemoryTwinAPI.create()
    out = await make_decision_recorder(twin)(
        title="Intent: deciding questions",
        rationale="asked once",
        deciding_questions=ANSWERS,
        project_id=str(uuid4()),
    )
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.metadata["deciding_questions"]["questions"][1]["answer"] == "120 kg person"
    with pytest.raises(ValueError, match="twin.record_decision: deciding_questions"):
        await make_decision_recorder(twin)(
            title="Intent 2", rationale="r", deciding_questions={"questions": []}
        )


# -- the concept gate's pick card --------------------------------------------


class TestThePickCard:
    def test_it_lists_the_concepts_and_how_to_pick_another(self) -> None:
        note = concept_pick_note(OPTIONS)
        assert note is not None
        assert "Four-leg dowelled (selected): rigid, cheap dowels" in note
        assert "Approving accepts Four-leg dowelled" in note
        assert "Slot-together flat-pack instead, retry naming it" in note
        assert "you choose" in note

    def test_no_options_means_no_card(self) -> None:
        assert concept_pick_note(None) is None
        assert concept_pick_note(()) is None


# -- reuse of earlier runs ---------------------------------------------------


def _run(run_id: str, at: float, project: str = PROJECT, **request: Any) -> dict[str, Any]:
    return {"id": run_id, "updated_at": at, "request": {"project_id": project, **request}}


RUNS = [
    _run(
        "old",
        1.0,
        decisions=[
            {"decision": "approve", "reason": "fine"},
            {"decision": "retry", "reason": "concept has no joints between seat and legs"},
        ],
    ),
    _run(
        "new",
        2.0,
        gate_reviews=[
            {"phase": "design", "recommendation": "rework", "reasons": "legs overlap the seat",
             "concerns": ["no dowel sizes sourced"]},
            {"phase": "simulation", "recommendation": "approve", "reasons": "SF 3.1 is fine",
             "concerns": []},
        ],
    ),
    _run("other", 3.0, project="22222222-2222-2222-2222-222222222222",
         decisions=[{"decision": "reject", "reason": "another project's lesson"}]),
    _run("me", 4.0, decisions=[{"decision": "reject", "reason": "this run's own rejection"}]),
]  # fmt: skip


class TestReuse:
    def test_rejections_and_findings_of_this_projects_earlier_runs_newest_first(self) -> None:
        lessons = lessons_from_runs(RUNS, project_id=PROJECT, exclude_run_id="me")
        assert [lesson.text for lesson in lessons] == [
            "design: legs overlap the seat",
            "design: no dowel sizes sourced",
            "a gate was retried: concept has no joints between seat and legs",
        ]
        assert [lesson.source_run for lesson in lessons] == ["new", "new", "old"]

    def test_no_project_means_no_lessons(self) -> None:
        assert lessons_from_runs(RUNS, project_id=None) == []

    def test_merge_deduplicates_and_caps(self) -> None:
        many = [PriorLesson(f"lesson {i}") for i in range(20)]
        merged = merge_lessons(
            [PriorLesson("Seat too thin!")], [PriorLesson("seat too thin")], many
        )
        assert merged[0].text == "Seat too thin!" and len(merged) == MAX_PRIOR_LESSONS
        assert sum(1 for m in merged if "thin" in m.text.lower()) == 1

    def test_render_is_empty_without_lessons(self) -> None:
        assert render_lessons([], "Lessons:") == ""
        assert "(run r1)" in render_lessons([PriorLesson("x", "r1")], "Lessons:")

    def test_collect_reads_runs_and_the_recorded_answers(self) -> None:
        class _Eval:
            async def deciding_answers(self, _project: str | None) -> DecidingAnswers:
                return _answers()

        lessons = asyncio.run(
            collect_prior_lessons(PROJECT, exclude_run_id="me", records=RUNS, evaluator=_Eval())
        )
        texts = [lesson.text for lesson in lessons]
        assert texts[0] == "design: legs overlap the seat"
        assert "the last concept had no joints" in texts
        assert "this run's own rejection" not in " ".join(texts)

    def test_a_broken_source_costs_its_lessons_only(self) -> None:
        class _Broken:
            async def deciding_answers(self, _project: str | None) -> DecidingAnswers:
                raise RuntimeError("twin down")

        lessons = asyncio.run(collect_prior_lessons(PROJECT, records=RUNS, evaluator=_Broken()))
        assert lessons and all(lesson.kind != "recorded" for lesson in lessons)

    def test_the_worker_reads_the_ledger_file_the_gateway_writes(self, tmp_path: Path) -> None:
        from orchestrator.harness.ledger import SqliteRunLedger
        from orchestrator.harness.runs import Run, RunStatus

        path = tmp_path / "runs_ledger.db"
        ledger = SqliteRunLedger(str(path))
        ledger.record_run(
            Run(id="old", status=RunStatus.COMPLETED, created_at=1.0, updated_at=1.0,
                request=RUNS[0]["request"])
        )  # fmt: skip
        ledger.close()
        records = _ledger_records(path)
        assert [r["id"] for r in records] == ["old"]
        assert lessons_from_runs(records, project_id=PROJECT)[0].source_run == "old"
        assert _ledger_records(tmp_path / "absent.db") == []


# -- the gates, in both engines ----------------------------------------------


class _Evaluator:
    def __init__(
        self, answers: DecidingAnswers | None, options: tuple[ConceptOption, ...] | None = None
    ) -> None:
        self._answers = answers
        self._options = options

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision", "intent", "design_sketch"}

    async def deciding_answers(self, _project: str | None) -> DecidingAnswers | None:
        return self._answers

    async def concept_options(self, _project: str | None) -> tuple[ConceptOption, ...] | None:
        return self._options


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(evaluator: _Evaluator, phase_id: str, required: list[str]) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(evaluator, _Constraints(), None)
    return asyncio.run(
        checkers.evaluate({"id": phase_id, "required_deliverables": required}, PROJECT)
    )


def _in_process(evaluator: _Evaluator, phase_id: str, required: tuple[str, ...]) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, GateCoordinator
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=evaluator,
    )
    phase = Phase(id=phase_id, title="t", objective="o", required_deliverables=required)
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


class TestTheIntentGate:
    def test_without_the_answers_it_is_not_ready_in_both_engines(self) -> None:
        required = ["intent"]
        check = _temporal(_Evaluator(None), INTENT_PHASE_ID, required)
        assert not check.ready and DECIDING_QUESTIONS_MISSING in check.missing
        report = _in_process(_Evaluator(None), INTENT_PHASE_ID, tuple(required))
        assert DECIDING_QUESTIONS_MISSING in report.missing

    def test_with_them_it_is_ready_and_summarised(self) -> None:
        required = ["intent"]
        check = _temporal(_Evaluator(_answers()), INTENT_PHASE_ID, required)
        assert check.ready and "4 deciding question(s)" in check.reason
        assert _in_process(_Evaluator(_answers()), INTENT_PHASE_ID, tuple(required)).ready


class TestTheConceptGate:
    def test_the_reason_is_the_pick_card_in_both_engines(self) -> None:
        evaluator = _Evaluator(None, OPTIONS)
        check = _temporal(evaluator, CONCEPT_PHASE_ID, ["design_decision"])
        assert check.ready and "Approving accepts Four-leg dowelled" in check.reason
        report = _in_process(evaluator, CONCEPT_PHASE_ID, ("design_decision",))
        assert report.ready and "Slot-together flat-pack" in report.notes[0]


# -- the template ------------------------------------------------------------


def test_mech_v1_starts_with_intent_which_asks_the_deciding_questions() -> None:
    phases = load_templates()["mech_v1"].definition.phases
    ids = [p.id for p in phases]
    assert ids[0] == INTENT_PHASE_ID and "kickoff" not in ids
    intent = phases[0]
    # Intent stays reusable: the answers are expected and gated, not a reuse key.
    assert intent.required_deliverables == ("intent",)
    assert "design_decision" in intent.expected_artifacts
    assert "deciding_questions field" in intent.objective
    assert "Do not choose a design direction here" in intent.objective
    concept = next(p for p in phases if p.id == CONCEPT_PHASE_ID)
    assert "deciding answers from intent" in concept.objective
    assert "the person picks at this gate" in concept.objective


# -- the briefs --------------------------------------------------------------


def _brain(answers: DecidingAnswers | None, lessons: list[PriorLesson]) -> ReActPhaseBrain:
    async def read_answers(_project: str | None) -> DecidingAnswers | None:
        return answers

    async def read_lessons(_project: str | None) -> list[PriorLesson]:
        return lessons

    return ReActPhaseBrain(
        mcp_bridge=None, answers_reader=read_answers, lessons_reader=read_lessons
    )


def _prompt(brain: ReActPhaseBrain, phase_id: str) -> str:
    phase = Phase(id=phase_id, title=phase_id, objective="do the phase")
    ctx = FlowContext(goal="a chair", project_id=PROJECT)
    block = asyncio.run(brain._answers_blocks(phase, ctx))
    return brain._prompt("a chair", phase, ctx, answers_block=block)


LESSONS = [PriorLesson("design: legs overlap the seat", "new")]


class TestTheBriefs:
    def test_the_intent_brief_has_the_lessons_and_no_old_answers(self) -> None:
        prompt = _prompt(_brain(_answers(), LESSONS), INTENT_PHASE_ID)
        assert "legs overlap the seat (run new)" in prompt
        assert "deciding answers' lessons" in prompt
        assert "do not ask them again" not in prompt

    def test_the_concept_brief_has_the_answers_and_the_lessons(self) -> None:
        prompt = _prompt(_brain(_answers(), LESSONS), CONCEPT_PHASE_ID)
        assert "Who uses it? adults at a desk" in prompt
        assert "legs overlap the seat" in prompt
        assert "each concept says how it avoids" in prompt

    def test_every_later_phase_carries_the_answers(self) -> None:
        prompt = _prompt(_brain(_answers(), LESSONS), "design")
        assert "Who uses it? adults at a desk" in prompt
        assert "the last concept had no joints" in prompt  # the record's own lessons
        assert "legs overlap the seat" not in prompt  # fresh lessons go to intent and concept

    def test_no_record_and_no_lessons_add_nothing(self) -> None:
        bare = ReActPhaseBrain(mcp_bridge=None)
        phase = Phase(id=CONCEPT_PHASE_ID, title="c", objective="o")
        ctx = FlowContext(goal="g", project_id=PROJECT)
        assert asyncio.run(_brain(None, [])._answers_blocks(phase, ctx)) == ""
        assert asyncio.run(bare._answers_blocks(phase, ctx)) == ""

    def test_a_failing_reader_costs_the_block_not_the_phase(self) -> None:
        async def boom(_project: str | None) -> Any:
            raise RuntimeError("twin down")

        brain = ReActPhaseBrain(mcp_bridge=None, answers_reader=boom, lessons_reader=boom)
        phase = Phase(id=CONCEPT_PHASE_ID, title="c", objective="o")
        assert asyncio.run(brain._answers_blocks(phase, FlowContext(goal="g"))) == ""
