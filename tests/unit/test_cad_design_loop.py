"""The self-validating CAD design loop (api_gateway/twin/cad_design_loop.py).

Every model, the vision critic, the OCCT converter and the render adapter are
stubbed: CI has no CAD kernel, no browser and no model key. The CadQuery
adapter is the exact CAD tests' stub, extended with ``cadquery.dfm_measure``
and the render adapter's ``render.model_views``.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.twin import cad_design_loop_routes as routes
from api_gateway.twin.cad_design_loop import (
    LOOP_TOOL,
    Budget,
    CadDesignLoopService,
    CadLoopError,
    CadLoopRequest,
    LoopContext,
    ModelReply,
    StopReason,
    Unknown,
    author_prompt,
    dimension_checks,
    parse_author,
    parse_critique,
    unknowns_unanswered,
)
from tests.unit.test_exact_cad import (  # noqa: F401 - _isolated is an autouse fixture
    BOX_BBOX,
    PROJECT,
    FakeCad,
    Harness,
    _isolated,
    _parametric,
)
from tool_registry import jobs as jobs_module
from twin_core.dfm import ProcessContext, profile_for_process
from twin_core.items import find_item

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SCRIPT = "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 10, 5)"


def _png() -> str:
    return base64.b64encode(PNG).decode("ascii")


class LoopCad(FakeCad):
    """The CadQuery stub plus the DFM measure and the render adapter."""

    def __init__(self) -> None:
        super().__init__()
        self.bbox = dict(BOX_BBOX)
        self.wall_mm = 2.0
        self.render_raises = False
        self.render_params: list[dict[str, Any]] = []
        self.build_error: str | None = None

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        if tool_id == "cadquery.execute_script":
            self.calls.append(tool_id)
            if self.build_error:
                raise RuntimeError(self.build_error)
            out = await super().invoke(tool_id, params, timeout)
            self.calls.pop()
            out["bounding_box"] = dict(self.bbox)
            return out
        if tool_id == "cadquery.dfm_measure":
            self.calls.append(tool_id)
            return {
                "build_direction": params.get("build_direction"),
                "wall_thickness": {
                    "status": "measured",
                    "min_mm": self.wall_mm,
                    "per_face_min_mm": {"s0f1": self.wall_mm},
                    "thinnest": [{"face_id": "s0f1"}],
                    "method": "ray casting",
                },
                "faces": [],
                "holes": [],
                "bosses": [],
                "bounding_box": dict(self.bbox),
                "kernel": "stub",
            }
        if tool_id == "render.model_views":
            self.calls.append(tool_id)
            self.render_params.append(params)
            if self.render_raises:
                raise RuntimeError("tool 'render.model_views' is not served (adapter down)")
            return {
                "views": [
                    {"view": v, "label": v, "projection": "orthographic", "png_base64": _png()}
                    for v in params["views"]
                ],
                "sheet_png_base64": _png(),
                "bbox_mm": dict(self.bbox),
                "renderer": {"engine": "three.js r169", "browser": "chromium stub"},
            }
        return await super().invoke(tool_id, params, timeout)


def _script_reply(**over: Any) -> str:
    import json

    body = {
        "status": "script",
        "script": SCRIPT,
        "summary": "a box",
        "focus": "first shape",
        "assumptions": [],
        "questions": [],
    }
    body.update(over)
    return "```json\n" + json.dumps(body) + "\n```"


def _critique(satisfied: bool, *issues: dict[str, Any]) -> str:
    import json

    return json.dumps({"satisfied": satisfied, "reasons": ["looked"], "issues": list(issues)})


class Models:
    """Scripted author and critic replies, with the prompts they were given."""

    def __init__(self, authors: list[str], critiques: list[str], cost: float | None = 0.01):
        self.authors = list(authors)
        self.critiques = list(critiques)
        self.cost = cost
        self.author_prompts: list[str] = []
        self.critic_prompts: list[str] = []
        self.images: list[bytes] = []

    async def author(self, prompt: str) -> ModelReply:
        self.author_prompts.append(prompt)
        text = self.authors.pop(0) if len(self.authors) > 1 else self.authors[0]
        return ModelReply(text, "stub", "author-model", self.cost)

    async def critic(self, image: bytes, mime: str, prompt: str) -> ModelReply:
        self.critic_prompts.append(prompt)
        self.images.append(image)
        text = self.critiques.pop(0) if len(self.critiques) > 1 else self.critiques[0]
        return ModelReply(text, "stub", "vision-model", self.cost)


class LoopHarness:
    def __init__(self, models: Models) -> None:
        self.h = Harness()
        self.cad = LoopCad()
        self.h.service._bridge = self.cad  # noqa: SLF001
        self.blobs: dict[str, bytes] = {}
        self.models = models
        self.loop = CadDesignLoopService(
            self.h.twin,
            exact_cad=self.h.service,
            author=models.author,
            critic=models.critic,
            glb_converter=lambda step: b"glTF" + step[:8],
            blob_writer=self._blob,
            publisher=self.h._collect,  # noqa: SLF001
            job_runner=self.h.runner,
        )

    def _blob(self, node_id: str, filename: str, content: bytes, content_type: str) -> str:
        key = f"wp/{node_id}/{filename}"
        self.blobs[key] = content
        return key

    async def run(self, **request: Any) -> dict[str, Any]:
        req = CadLoopRequest(sketch_key="PSK-SLOT", **request)
        return await self.loop.run(PROJECT, req)


@pytest.fixture
def make() -> Any:
    def _make(authors: list[str], critiques: list[str], cost: float | None = 0.01) -> LoopHarness:
        return LoopHarness(Models(authors, critiques, cost))

    yield _make
    jobs_module._retry_factories.pop(LOOP_TOOL, None)  # noqa: SLF001


# ── pure parts ───────────────────────────────────────────────────────────


def test_replies_are_parsed_from_fenced_or_bare_json() -> None:
    reply = parse_author(_script_reply(focus="fixing wall thickness"))
    assert reply.status == "script" and reply.focus == "fixing wall thickness"
    assert parse_author('ok {"status": "question", "questions": []}').status == "question"
    with pytest.raises(ValueError):
        parse_author("no json here")
    critique = parse_critique(_critique(True))
    assert critique.satisfied and critique.issues == []
    odd = parse_critique('{"issues": [{"severity": "catastrophic", "description": "x"}]}')
    assert odd.issues[0].severity == "minor"


def _ctx(**over: Any) -> LoopContext:
    ctx = LoopContext(
        project_id=PROJECT,
        component_id="CMP-slot",
        component_name="Slot",
        sketch_key="PSK-SLOT",
        sketch_ref="PSK-SLOT@1",
        cad_key="CAD-SLOT",
        base_revision=None,
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx


def test_an_unknown_the_model_ignores_becomes_a_question() -> None:
    ctx = _ctx(unknowns=[Unknown("feat.depth", "How thick?", "parametric plan")])
    silent = parse_author(_script_reply())
    asked = unknowns_unanswered(ctx, silent, allow_working_values=True)
    assert [q["name"] for q in asked] == ["feat.depth"]
    labelled = parse_author(
        _script_reply(assumptions=[{"name": "feat.depth", "value": 5, "why": "a guess"}])
    )
    assert unknowns_unanswered(ctx, labelled, allow_working_values=True) == []
    # With working values off, even a labelled one goes to the person.
    assert unknowns_unanswered(ctx, labelled, allow_working_values=False)[0]["name"] == "feat.depth"


def test_dimensions_are_checked_against_targets_and_the_envelope() -> None:
    bbox = {"min_x": 0, "min_y": 0, "min_z": 0, "max_x": 20, "max_y": 10, "max_z": 5}
    rows = dimension_checks(
        _ctx(targets={"x": 20.2, "z": 8.0}, envelope={"y": 9.0}, tolerance_mm=0.5), bbox
    )
    by = {(r["kind"], r["axis"]): r["ok"] for r in rows}
    assert by == {("target", "x"): True, ("target", "z"): False, ("envelope", "y"): False}


def test_the_budget_counts_calls_and_priced_spend() -> None:
    b = Budget(max_calls=3, max_usd=0.05)
    b.add(0.02)
    b.add(None)
    assert b.refusal() is None and b.unpriced == 1
    b.add(0.04)
    assert "model calls" in (b.refusal() or "")
    assert "spend" in (Budget(max_calls=9, max_usd=0.01, usd=0.02).refusal() or "")


# ── the loop ─────────────────────────────────────────────────────────────


async def test_the_loop_converges_when_the_critic_is_satisfied(make: Any) -> None:
    lh = make(
        [_script_reply(), _script_reply(focus="fixing the slot ends")],
        [
            _critique(False, {"severity": "major", "view": "top", "description": "ends square"}),
            _critique(True),
        ],
    )
    await _parametric(lh.h, depth=5)
    out = await lh.run()
    assert out["stop_reason"] == StopReason.SATISFIED
    assert len(out["iterations"]) == 2
    first, second = out["iterations"]
    assert first["gate"]["passed"] is False and first["gate"]["critic_satisfied"] is False
    assert second["gate"] == {
        "critic_satisfied": True,
        "validity_pass": True,
        "no_blocking_dfm": True,
        "dimensions_in_tolerance": True,
        "passed": True,
        "dimensions_checked": 0,
    }
    # The critic saw the rendered sheet; the second author prompt had the critique.
    assert lh.models.images == [PNG, PNG]
    assert "ends square" in lh.models.author_prompts[1]
    # A Z-up render of the measured extents, through the render adapter.
    params = lh.cad.render_params[0]
    assert params["up_axis"] == "z" and params["dimensions"]["x"] == 20.0
    assert out["result"]["status"] == "proposed" and out["result"]["iteration"] == 2


async def test_records_are_kept_per_iteration(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    out = await lh.run()
    node = await lh.h.twin.get_work_product(UUID(out["node_id"]))
    assert node.type.value == "cad_design_loop"
    rec = node.metadata["cad_design_loop"]["iterations"][0]
    for key in ("script", "build", "validity", "measurements", "render", "critique", "dfm"):
        assert key in rec, key
    assert rec["models"]["author"] == {
        "provider": "stub",
        "model": "author-model",
        "cost_usd": 0.01,
    }
    assert rec["models"]["critic"]["model"] == "vision-model"
    assert rec["manufacturing_context_ref"] == out["context"]["manufacturing_context_ref"]
    # The views are named blobs on the record, served like a render's images.
    files = node.metadata["image_files"]
    assert "iter-1-sheet.png" in files and "iter-1-iso.png" in files
    assert lh.blobs[files["iter-1-sheet.png"]] == PNG
    # One status line per iteration on the activity overlay, each a valid event.
    from api_gateway.operations.schemas import OperationEventIn

    titles = [e.get("stage_title") for e in lh.h.events]
    assert "Iteration 1: first shape" in titles
    for event in lh.h.events:
        OperationEventIn.model_validate(event)
    views = next(e for e in lh.h.events if e.get("type") == "partial_result")
    assert views["artifact_refs"][0]["kind"] == "image"
    assert views["artifact_refs"][0]["url"].endswith("/files/iter-1-sheet.png")
    assert lh.h.events[-1]["type"] == "review_required"


async def test_the_iteration_cap_stops_with_open_issues(make: Any) -> None:
    issue = {"severity": "major", "view": "front", "description": "too tall"}
    lh = make([_script_reply()], [_critique(False, issue)])
    await _parametric(lh.h, depth=5)
    out = await lh.run(max_iterations=2)
    assert out["stop_reason"] == StopReason.ITERATION_CAP
    assert out["stop_detail"] == "2 iterations ran"
    assert len(out["iterations"]) == 2
    assert out["result"]["satisfied"] is False
    assert any("too tall" in i for i in out["result"]["open_issues"])


async def test_the_spend_cap_stops_honestly(make: Any) -> None:
    lh = make([_script_reply()], [_critique(False)], cost=0.30)
    await _parametric(lh.h, depth=5)
    out = await lh.run(max_iterations=5, max_spend_usd=0.5)
    assert out["stop_reason"] == StopReason.SPEND_CAP
    assert "spend" in out["stop_detail"]
    assert out["budget"]["model_calls"] == 2 and out["budget"]["spend_usd"] == 0.6


async def test_the_call_cap_stops_before_the_review(make: Any) -> None:
    lh = make([_script_reply()], [_critique(False)], cost=None)
    await _parametric(lh.h, depth=5)
    out = await lh.run(max_model_calls=3)
    assert out["stop_reason"] == StopReason.SPEND_CAP
    # Iteration 2's script was built, but no call was left for its review.
    assert len(out["iterations"]) == 2 and "critique" not in out["iterations"][1]
    assert out["budget"]["unpriced_calls"] == 3


async def test_an_unknown_dimension_is_a_question_not_a_value(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h)  # the slot's depth is unknown
    out = await lh.run(allow_working_values=False)
    assert out["stop_reason"] == StopReason.NEEDS_INPUT
    assert out["questions"][0]["name"] == "feat-extrude-1.depth"
    assert "How thick" in out["questions"][0]["question"]
    # Nothing was built from an invented value.
    assert "cadquery.execute_script" not in lh.cad.calls
    assert out["result"] is None
    prompt = lh.models.author_prompts[0]
    assert "UNKNOWN dimensions" in prompt and "feat-extrude-1.depth" in prompt


async def test_a_labelled_working_value_is_recorded_and_surfaced(make: Any) -> None:
    reply = _script_reply(
        assumptions=[{"name": "feat-extrude-1.depth", "value": 4, "unit": "mm", "why": "guess"}]
    )
    lh = make([reply], [_critique(True)])
    await _parametric(lh.h)
    out = await lh.run()
    assert out["stop_reason"] == StopReason.SATISFIED
    assert out["result"]["assumptions"][0]["name"] == "feat-extrude-1.depth"
    assert any("please confirm" in i for i in out["result"]["open_issues"])


async def test_an_answer_makes_the_unknown_stated(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h)
    out = await lh.run(answers={"feat-extrude-1.depth": 6}, allow_working_values=False)
    assert out["stop_reason"] == StopReason.SATISFIED
    assert "feat-extrude-1.depth = 6 mm" in lh.models.author_prompts[0]


async def test_dfm_context_and_findings_reach_the_model(make: Any) -> None:
    lh = make(
        [_script_reply(), _script_reply(focus="fixing wall thickness")],
        [_critique(False, {"severity": "minor", "description": "thin"}), _critique(True)],
    )
    await _parametric(lh.h, depth=5)
    lh.cad.wall_mm = 0.5
    ctx = await lh.loop.prepare(PROJECT, CadLoopRequest(sketch_key="PSK-SLOT"))
    ctx.process = ProcessContext(process="FDM", process_authority="user_stated")
    ctx.profile = profile_for_process("FDM")
    ctx.dfm_current = {
        "findings": [
            {
                "severity": "advisory",
                "rule_title": "Minimum wall thickness",
                "measured": 0.6,
                "unit": "mm",
                "comparison": ">=",
                "limit": 0.8,
                "suggested_fix": "Thicken the wall",
                "anchor": {"feature": "side wall"},
            }
        ]
    }
    out = await lh.loop.run(PROJECT, CadLoopRequest(sketch_key="PSK-SLOT"), prepared=ctx)
    first = lh.models.author_prompts[0]
    assert "Manufacturing context:" in first and ctx.manufacturing_text in first
    assert "Current DFM findings" in first and "side wall" in first
    assert "Minimum wall thickness" in first  # the profile's rules
    # The candidate was measured and the profile's exact checks ran on it.
    assert "cadquery.dfm_measure" in lh.cad.calls
    dfm = out["iterations"][0]["dfm"]
    assert dfm["kind"] == "exact" and dfm["findings"][0]["rule_id"] == "fdm.min_wall"
    assert (
        "fdm.min_wall" in lh.models.author_prompts[1]
        or "Minimum wall" in lh.models.author_prompts[1]
    )
    assert "Minimum wall thickness" in lh.models.critic_prompts[0]


async def test_a_blocking_dfm_finding_keeps_the_loop_going(make: Any, monkeypatch: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)

    async def blocking(ctx: Any, step: Any, bbox: Any, record: Any) -> dict[str, Any]:
        return {"status": "issues_found", "findings": [{"severity": "blocking", "message": "x"}]}

    monkeypatch.setattr(lh.loop, "_dfm", blocking)
    out = await lh.run(max_iterations=2)
    assert out["stop_reason"] == StopReason.ITERATION_CAP
    assert out["iterations"][-1]["gate"]["no_blocking_dfm"] is False


async def test_a_dimension_out_of_tolerance_is_not_satisfied(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    out = await lh.run(max_iterations=1, target_dimensions={"x": 30})
    assert out["stop_reason"] == StopReason.ITERATION_CAP
    assert out["iterations"][0]["gate"]["dimensions_in_tolerance"] is False


async def test_the_critic_never_validates_anything(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    out = await lh.run()
    assert out["stop_reason"] == StopReason.SATISFIED
    node = await lh.h.twin.get_work_product(UUID(out["node_id"]))
    meta = node.metadata
    assert meta["authority"] == "proposal"
    assert "geometry_state" not in meta and "validity" not in meta
    assert out["result"]["validated"] is False
    assert out["iterations"][0]["critique"]["authority"] == "advisory"
    # Nothing in the twin was validated or verified by the run.
    for wp in await lh.h.twin.list_work_products(project_id=UUID(PROJECT)):
        assert (wp.metadata or {}).get("geometry_state") not in ("validated", "released")
        assert (wp.metadata or {}).get("verified") is not True


async def test_the_result_is_proposed_not_applied(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    out = await lh.run()
    # No CAD item exists until a person accepts.
    assert await find_item(lh.h.twin, "CAD-SLOT", PROJECT) is None
    assert out["result"]["base_revision"] is None
    decided = await lh.loop.decide(
        PROJECT, out["node_id"], decision="accept", actor="person:ana", session_id="s"
    )
    job = await lh.h.runner.wait(decided["decision"]["build"]["job_id"], 5.0)
    assert job.status == "succeeded", job.error
    cad = await find_item(lh.h.twin, "CAD-SLOT", PROJECT)
    head = await lh.h.twin.get_work_product(cad.head_node_id)
    assert head.metadata["geometry_state"] == "validated"  # by the exact path's own check
    assert head.metadata["exact_source"]["script"] == SCRIPT
    with pytest.raises(CadLoopError):
        await lh.loop.decide(PROJECT, out["node_id"], decision="reject", actor="p", session_id="s")


async def test_accepting_after_the_head_moved_is_refused(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    first = await lh.run()
    await lh.loop.decide(
        PROJECT, first["node_id"], decision="accept", actor="person:ana", session_id="s"
    )
    for job in list(lh.h.runner._tasks):  # noqa: SLF001
        await lh.h.runner.wait(job, 5.0)
    second = await lh.run()  # bound to CAD-SLOT@1
    assert second["result"]["base_revision"] == "1"
    # Someone else moves the head on (another version) before the person decides.
    lh.models.authors = [_script_reply(script=SCRIPT + ".edges('|Z').fillet(1)")]
    third = await lh.run()
    await lh.loop.decide(
        PROJECT, third["node_id"], decision="accept", actor="person:ana", session_id="s"
    )
    for job in list(lh.h.runner._tasks):  # noqa: SLF001
        await lh.h.runner.wait(job, 5.0)
    with pytest.raises(CadLoopError) as caught:
        await lh.loop.decide(
            PROJECT, second["node_id"], decision="accept", actor="person:ana", session_id="s"
        )
    assert caught.value.status == 409


async def test_a_build_error_goes_back_to_the_model(make: Any) -> None:
    lh = make([_script_reply(), _script_reply(focus="fixing the syntax")], [_critique(True)])
    await _parametric(lh.h, depth=5)
    lh.cad.build_error = "SyntaxError: invalid syntax (line 2)"

    async def heal() -> None:
        lh.cad.build_error = None

    out_first: dict[str, Any] = {}
    original = lh.models.author

    async def author(prompt: str) -> ModelReply:
        if "did not build" in prompt:
            await heal()
            out_first["saw_error"] = "SyntaxError" in prompt
        return await original(prompt)

    lh.loop._author = author  # noqa: SLF001
    out = await lh.run()
    assert out_first["saw_error"] is True
    assert out["iterations"][0]["build"]["ok"] is False
    assert out["stop_reason"] == StopReason.SATISFIED


async def test_a_render_failure_stops_the_run_honestly(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    lh.cad.render_raises = True
    out = await lh.run()
    assert out["stop_reason"] == StopReason.FAILED
    assert "render" in out["stop_detail"]
    assert lh.models.critic_prompts == []  # nothing judged what nobody saw


async def test_a_model_that_cannot_be_reached_fails_the_run(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)

    async def down(prompt: str) -> ModelReply:
        raise RuntimeError("no model provider is configured")

    lh.loop._author = down  # noqa: SLF001
    out = await lh.run()
    assert out["stop_reason"] == StopReason.FAILED and "model" in out["stop_detail"]


async def test_a_plan_with_complete_inputs_seeds_the_first_script(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    request = CadLoopRequest(sketch_key="PSK-SLOT", answers={"material": "PLA"})
    ctx = await lh.loop.prepare(PROJECT, request)
    assert ctx.unknowns == []
    assert ctx.component_name == "Slot" and ctx.cad_key == "CAD-SLOT"
    assert ctx.seed_script is not None and "result = " in ctx.seed_script, ctx.seed_note
    await lh.loop.run(PROJECT, request, prepared=ctx)
    assert ctx.seed_script in lh.models.author_prompts[0]


async def test_a_bad_request_is_refused_at_once(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    with pytest.raises(CadLoopError):
        await lh.loop.prepare(PROJECT, CadLoopRequest(sketch_key="PSK-NOPE"))
    with pytest.raises(CadLoopError):
        await lh.loop.prepare(PROJECT, CadLoopRequest())
    with pytest.raises(ValueError):
        CadLoopRequest(target_dimensions={"w": 3})


async def test_the_loop_runs_as_a_job(make: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    reply = await lh.loop.start(PROJECT, CadLoopRequest(sketch_key="PSK-SLOT"), session_id="s")
    job = await lh.h.runner.wait(reply["job_id"], 5.0)
    assert job.status == "succeeded", job.error
    assert job.result["stop_reason"] == "satisfied"
    runs = await lh.loop.runs(PROJECT)
    assert runs[0]["node_id"] == job.result["node_id"]


def test_the_prompt_asks_for_json_and_never_invents() -> None:
    ctx = _ctx(unknowns=[Unknown("a.depth", "How deep?", "plan")], seed_script="result = 1")
    text = author_prompt(
        ctx, iteration=1, max_iterations=5, previous=None, allow_working_values=False
    )
    assert "never invent" in text and "Working values are not allowed" in text
    assert "result = 1" in text and '"status": "script" or "question"' in text


# ── routes ───────────────────────────────────────────────────────────────


async def test_routes_start_list_and_refuse_agents(make: Any, monkeypatch: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    monkeypatch.setattr("api_gateway.twin.routes.get_twin", lambda: lh.h.twin)
    routes.set_service(lh.loop)
    routes.set_job_runner(lh.h.runner)
    try:
        app = FastAPI()
        app.include_router(routes.router)
        client = TestClient(app)
        refused = client.post(
            f"/v1/twin/projects/{PROJECT}/cad-loops",
            json={"sketch_key": "PSK-SLOT"},
            headers={"X-MetaForge-Surface": "agent"},
        )
        assert refused.status_code == 403
        bad = client.post(f"/v1/twin/projects/{PROJECT}/cad-loops", json={"sketch_key": "PSK-X"})
        assert bad.status_code == 404
        listed = client.get(f"/v1/twin/projects/{PROJECT}/cad-loops")
        assert listed.status_code == 200 and listed.json()["spend"]["cap"] == 3
        out = await lh.run()
        one = client.get(f"/v1/twin/projects/{PROJECT}/cad-loops/{out['node_id']}")
        assert one.json()["stop_reason"] == "satisfied"
        agent = client.post(
            f"/v1/twin/projects/{PROJECT}/cad-loops/{out['node_id']}/decision",
            json={"decision": "accept"},
            headers={"X-MetaForge-Surface": "agent"},
        )
        assert agent.status_code == 403
        kept = client.post(
            f"/v1/twin/projects/{PROJECT}/cad-loops/{out['node_id']}/decision",
            json={"decision": "reject"},
        )
        assert kept.status_code == 200 and kept.json()["result"]["status"] == "rejected"
    finally:
        routes.set_service(None)
        routes.set_job_runner(None)


async def test_routes_ask_to_confirm_spend_past_the_hourly_cap(make: Any, monkeypatch: Any) -> None:
    lh = make([_script_reply()], [_critique(True)])
    await _parametric(lh.h, depth=5)
    monkeypatch.setattr("api_gateway.twin.routes.get_twin", lambda: lh.h.twin)
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_CAD_LOOP_PER_HOUR", "1")
    await lh.run()
    routes.set_service(lh.loop)
    routes.set_job_runner(lh.h.runner)
    try:
        app = FastAPI()
        app.include_router(routes.router)
        client = TestClient(app)
        held = client.post(
            f"/v1/twin/projects/{PROJECT}/cad-loops", json={"sketch_key": "PSK-SLOT"}
        )
        assert held.status_code == 409
        assert held.json()["detail"]["code"] == "spend_confirmation_required"
    finally:
        routes.set_service(None)
        routes.set_job_runner(None)


async def test_the_twin_adapter_registers_the_loop_tool(make: Any) -> None:
    from mcp_core.annotations import annotations_for
    from tool_registry.tools.twin.adapter import TwinServer

    lh = make([_script_reply()], [_critique(True)])
    server = TwinServer(lh.h.twin, cad_design_loop_service=lh.loop)
    assert "twin.design_cad_loop" in server.tool_ids and "twin.job_status" in server.tool_ids
    notes = annotations_for("twin.design_cad_loop")
    assert notes["readOnlyHint"] is False and notes["destructiveHint"] is False
    assert notes["openWorldHint"] is True
