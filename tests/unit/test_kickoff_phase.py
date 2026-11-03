"""The kickoff phase: deciding questions, earlier runs' lessons, directions, the pick.

mech_v1 starts by settling the direction: the few questions whose answers
change the design most, two or three named directions with their risks, and
the one the person picks (or "auto", the agent's choice with its reason). The
gate shows the directions; every later phase brief carries the answers and the
pick; the kickoff and concept briefs carry what earlier runs on the project
learned.
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
    KICKOFF_MISSING,
    KICKOFF_PHASE_ID,
    Kickoff,
    kickoff_gap,
    parse_kickoff,
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

KICKOFF: dict[str, Any] = {
    "questions": [
        {"id": "users", "question": "Who uses it?", "answer": "adults at a desk",
         "source": "stated"},
        {"id": "load", "question": "What load?", "answer": "120 kg person", "source": "person"},
        {"id": "style", "question": "What style?", "answer": "plain birch", "source": "assumed"},
        {"id": "budget", "question": "What budget?", "source": "open"},
    ],
    "directions": [
        {"name": "Four-leg dowelled", "rationale": "rigid, cheap dowels",
         "risk": "racking if the dowels are loose"},
        {"name": "Slot-together flat-pack", "rationale": "no fixings, ships flat",
         "risk": "slots wear and loosen"},
        {"name": "Cantilever", "rationale": "light look", "risk": "bending at the bend"},
    ],
    "picked": "four-leg DOWELLED",
    "lessons": ["the last concept had no joints", {"text": "seat too thin", "source_run": "r0"}],
}  # fmt: skip


def _kickoff(**change: Any) -> Kickoff:
    return parse_kickoff({**KICKOFF, **change})


# -- the record --------------------------------------------------------------


class TestTheRecord:
    def test_a_kickoff_parses_and_the_pick_names_a_direction(self) -> None:
        k = _kickoff()
        assert k.picked == "Four-leg dowelled"  # matched to the direction's own name
        assert k.direction.risk == "racking if the dowels are loose"
        assert [q.source for q in k.questions] == ["stated", "person", "assumed", "open"]
        assert [lesson.source_run for lesson in k.lessons] == ["", "r0"]
        assert k.as_metadata()["picked"] == "Four-leg dowelled"
        assert parse_kickoff(k.as_metadata()) == k

    def test_auto_records_the_agents_choice_and_why(self) -> None:
        k = _kickoff(picked="auto", chosen="Cantilever", why="the person wants it light")
        assert k.is_auto and k.direction.name == "Cantilever"
        assert "chose it because the person wants it light" in k.pick_line()

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
            ({"directions": KICKOFF["directions"][:1]}, "two or three"),
            ({"directions": KICKOFF["directions"] * 2}, "two or three"),
            ({"directions": [KICKOFF["directions"][0], {"name": "B", "rationale": "r"}]},
             "main risk"),
            ({"directions": [KICKOFF["directions"][0], KICKOFF["directions"][0]]}, "distinct"),
            ({"picked": ""}, "picked"),
            ({"picked": "Rocking chair"}, "not one of"),
            ({"picked": "auto"}, "needs chosen"),
            ({"picked": "auto", "chosen": "Cantilever"}, "needs why"),
        ],
    )  # fmt: skip
    def test_a_malformed_kickoff_is_refused(self, change: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            _kickoff(**change)

    def test_the_gate_note_lists_every_direction_and_how_to_pick_another(self) -> None:
        note = _kickoff().gate_note()
        for name in ("Four-leg dowelled", "Slot-together flat-pack", "Cantilever"):
            assert name in note
        assert "risk: slots wear and loosen" in note
        assert "Approving accepts Four-leg dowelled" in note
        assert "retry naming it" in note and "you choose" in note
        assert "1 assumed, 1 open" in note

    def test_the_brief_carries_the_answers_and_the_pick(self) -> None:
        brief = _kickoff().brief()
        assert "do not ask them again" in brief
        assert "Who uses it? adults at a desk [stated]" in brief
        assert "What budget? (open: not yet answered) [open]" in brief
        assert "Direction: Four-leg dowelled (picked by the person)" in brief
        assert "Directions not taken: Slot-together flat-pack, Cantilever" in brief
        assert "the last concept had no joints" in brief
        assert "the last concept had no joints" not in _kickoff().brief(include_lessons=False)

    def test_no_record_is_the_gap(self) -> None:
        assert kickoff_gap(None) == KICKOFF_MISSING
        assert kickoff_gap(_kickoff()) is None


@pytest.mark.asyncio
async def test_the_decision_carries_the_kickoff_and_refuses_a_bad_one() -> None:
    twin = InMemoryTwinAPI.create()
    out = await make_decision_recorder(twin)(
        title="Kickoff: chair", rationale="direction set", kickoff=KICKOFF, project_id=str(uuid4())
    )
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.metadata["kickoff"]["picked"] == "Four-leg dowelled"
    assert parse_kickoff(wp.metadata["kickoff"]).direction.name == "Four-leg dowelled"
    with pytest.raises(ValueError, match="twin.record_decision: kickoff"):
        await make_decision_recorder(twin)(
            title="Kickoff 2", rationale="r", kickoff={**KICKOFF, "picked": "nope"}
        )


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
        texts = [lesson.text for lesson in lessons]
        assert texts == [
            "design: legs overlap the seat",
            "design: no dowel sizes sourced",
            "a gate was retried: concept has no joints between seat and legs",
        ]
        assert [lesson.source_run for lesson in lessons] == ["new", "new", "old"]
        assert {lesson.kind for lesson in lessons} == {"reviewer_finding", "gate_rejection"}

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

    def test_collect_reads_runs_and_the_recorded_kickoff(self) -> None:
        class _Eval:
            async def kickoff(self, _project: str | None) -> Kickoff:
                return _kickoff()

        lessons = asyncio.run(
            collect_prior_lessons(PROJECT, exclude_run_id="me", records=RUNS, evaluator=_Eval())
        )
        texts = [lesson.text for lesson in lessons]
        assert texts[0] == "design: legs overlap the seat"
        assert "the last concept had no joints" in texts
        assert "this run's own rejection" not in " ".join(texts)

    def test_a_broken_source_costs_its_lessons_only(self) -> None:
        class _Broken:
            async def kickoff(self, _project: str | None) -> Kickoff:
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


# -- the gate, in both engines -----------------------------------------------


class _Evaluator:
    def __init__(self, kickoff: Kickoff | None) -> None:
        self._kickoff = kickoff

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision"}

    async def kickoff(self, _project: str | None) -> Kickoff | None:
        return self._kickoff


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(evaluator: _Evaluator) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(evaluator, _Constraints(), None)
    phase = {"id": KICKOFF_PHASE_ID, "required_deliverables": ["design_decision"]}
    return asyncio.run(checkers.evaluate(phase, PROJECT))


def _in_process(evaluator: _Evaluator) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, GateCoordinator
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=evaluator,
    )
    phase = Phase(
        id=KICKOFF_PHASE_ID, title="t", objective="o", required_deliverables=("design_decision",)
    )
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


class TestTheKickoffGate:
    def test_without_a_kickoff_record_it_is_not_ready_in_both_engines(self) -> None:
        check = _temporal(_Evaluator(None))
        assert not check.ready and KICKOFF_MISSING in check.missing
        assert KICKOFF_MISSING in _in_process(_Evaluator(None)).missing

    def test_with_one_it_is_ready_and_shows_the_directions(self) -> None:
        check = _temporal(_Evaluator(_kickoff()))
        assert check.ready and "Approving accepts Four-leg dowelled" in check.reason
        report = _in_process(_Evaluator(_kickoff()))
        assert report.ready and "Slot-together flat-pack" in report.notes[0]

    def test_the_in_process_gate_reason_carries_the_note(self) -> None:
        from orchestrator.design_flow.executor import PhaseOutcome, ReadinessReport, _gate_reason

        phase = Phase(id=KICKOFF_PHASE_ID, title="Kickoff", objective="o")
        reason = _gate_reason(
            phase,
            PhaseOutcome(summary="done"),
            ReadinessReport(ready=True, notes=[_kickoff().gate_note()]),
        )
        assert "Cantilever" in reason


# -- the template ------------------------------------------------------------


def test_mech_v1_starts_with_the_kickoff_and_the_concept_builds_on_its_pick() -> None:
    template = load_templates()["mech_v1"]
    phases = template.definition.phases
    ids = [p.id for p in phases]
    assert ids[0] == KICKOFF_PHASE_ID and ids[1] == "intent"
    kickoff = phases[0]
    assert kickoff.required_deliverables == ("design_decision",)
    assert "kickoff field" in kickoff.objective and "'auto'" in kickoff.objective
    assert kickoff.gate is not None and "pick a direction" in kickoff.gate.name
    concept = next(p for p in phases if p.id == "concept_selection")
    assert "direction picked at kickoff" in concept.objective


# -- the briefs --------------------------------------------------------------


def _brain(kickoff: Kickoff | None, lessons: list[PriorLesson]) -> ReActPhaseBrain:
    async def read_kickoff(_project: str | None) -> Kickoff | None:
        return kickoff

    async def read_lessons(_project: str | None) -> list[PriorLesson]:
        return lessons

    return ReActPhaseBrain(
        mcp_bridge=None, kickoff_reader=read_kickoff, lessons_reader=read_lessons
    )


def _prompt(brain: ReActPhaseBrain, phase_id: str) -> str:
    phase = Phase(id=phase_id, title=phase_id, objective="do the phase")
    ctx = FlowContext(goal="a chair", project_id=PROJECT)
    return brain._prompt("a chair", phase, ctx, asyncio.run(brain._kickoff_blocks(phase, ctx)))


LESSONS = [PriorLesson("design: legs overlap the seat", "new")]


class TestTheBriefs:
    def test_the_kickoff_brief_has_the_lessons_and_no_old_kickoff(self) -> None:
        prompt = _prompt(_brain(_kickoff(), LESSONS), KICKOFF_PHASE_ID)
        assert "legs overlap the seat (run new)" in prompt
        assert "kickoff record's lessons" in prompt
        assert "do not ask them again" not in prompt

    def test_the_concept_brief_builds_on_the_pick_with_the_lessons(self) -> None:
        prompt = _prompt(_brain(_kickoff(), LESSONS), "concept_selection")
        assert "Direction: Four-leg dowelled (picked by the person)" in prompt
        assert "Build on the picked direction (Four-leg dowelled)" in prompt
        assert "legs overlap the seat" in prompt
        assert prompt.index("Build on the picked") < prompt.index("do the phase")

    def test_every_later_phase_carries_the_answers(self) -> None:
        prompt = _prompt(_brain(_kickoff(), LESSONS), "design")
        assert "Who uses it? adults at a desk" in prompt
        assert "the last concept had no joints" in prompt  # the record's own lessons
        assert "legs overlap the seat" not in prompt  # fresh lessons go to kickoff and concept

    def test_no_record_and_no_lessons_add_nothing(self) -> None:
        bare = ReActPhaseBrain(mcp_bridge=None)
        phase = Phase(id="concept_selection", title="c", objective="o")
        ctx = FlowContext(goal="g", project_id=PROJECT)
        assert asyncio.run(_brain(None, [])._kickoff_blocks(phase, ctx)) == ""
        assert asyncio.run(bare._kickoff_blocks(phase, ctx)) == ""

    def test_a_failing_reader_costs_the_block_not_the_phase(self) -> None:
        async def boom(_project: str | None) -> Any:
            raise RuntimeError("twin down")

        brain = ReActPhaseBrain(mcp_bridge=None, kickoff_reader=boom, lessons_reader=boom)
        phase = Phase(id="concept_selection", title="c", objective="o")
        assert asyncio.run(brain._kickoff_blocks(phase, FlowContext(goal="g"))) == ""
