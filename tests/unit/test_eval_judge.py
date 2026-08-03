"""Unit tests for the LLM-as-judge work-product scorer (MET-571). Network-free."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from judge import (  # noqa: E402
    PROMPT_VERSION,
    VERDICT_SCHEMA,
    build_judge_prompt,
    clamp_score,
    cli_complete,
    cli_judge_cmd,
    judge_request,
    judge_run,
    parse_cli_envelope,
    parse_judge_reply,
    render_deliverables,
    sdk_complete,
    summarize_judgements,
)

_DOD = {"mechanical": "A real FEA ran with a numeric safety factor."}


# --- prompt building -----------------------------------------------------------
def test_prompt_contains_goal_dod_and_deliverables() -> None:
    prompt = build_judge_prompt(_DOD, "- [cad_model] L-bracket", "Design an L-bracket")
    assert "Design an L-bracket" in prompt
    assert "numeric safety factor" in prompt
    assert "L-bracket" in prompt


def test_prompt_marks_empty_deliverables() -> None:
    assert "no work products were recorded" in build_judge_prompt(_DOD, "", "goal")


# --- reply parsing ---------------------------------------------------------------
_VERDICT = {
    "phases": [
        {
            "phase": "mechanical",
            "score": 0.5,
            "verdict": "partial",
            "missing": ["safety factor"],
            "rationale": "FEA present, no SF.",
        }
    ],
    "overall_score": 0.5,
    "summary": "Partial.",
}


def test_parse_clean_json() -> None:
    assert parse_judge_reply(json.dumps(_VERDICT))["overall_score"] == 0.5


def test_parse_fenced_json() -> None:
    text = "```json\n" + json.dumps(_VERDICT) + "\n```"
    assert parse_judge_reply(text)["phases"][0]["phase"] == "mechanical"


def test_parse_json_with_surrounding_prose() -> None:
    text = "Here is my verdict:\n" + json.dumps(_VERDICT) + "\nDone."
    assert parse_judge_reply(text)["summary"] == "Partial."


def test_parse_rejects_shapeless_reply() -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_judge_reply('{"not_a_verdict": true}')


def test_parse_derives_missing_overall_score_from_phases() -> None:
    # claude-cli backend has no structured-output guarantee: the model may
    # omit overall_score — derive it as the mean of phase scores.
    verdict = {
        "phases": [
            {"phase": "a", "score": 1.0, "verdict": "met", "missing": [], "rationale": "r"},
            {"phase": "b", "score": 0.0, "verdict": "unmet", "missing": ["x"], "rationale": "r"},
        ],
        "summary": "half",
    }
    assert parse_judge_reply(json.dumps(verdict))["overall_score"] == 0.5


def test_parse_rejects_missing_overall_with_no_phases() -> None:
    with pytest.raises(ValueError):
        parse_judge_reply('{"phases": [], "summary": "nothing"}')


def test_clamp_score_bounds_and_garbage() -> None:
    assert clamp_score(1.7) == 1.0
    assert clamp_score(-3) == 0.0
    assert clamp_score("0.4") == 0.4
    assert clamp_score(None) == 0.0


# --- deliverable rendering -------------------------------------------------------
def _fake_fetch(nodes: dict) -> Callable[[str], dict]:
    return lambda wp_id: nodes[wp_id]


def test_render_deliverables_includes_type_name_and_props() -> None:
    wps = [{"id": "n1", "type": "design_decision", "name": "fallback"}]
    nodes = {
        "n1": {
            "name": "Enclosure material: ABS",
            "properties": {"rationale": "impact resistance", "count": 3},
        }
    }
    out = render_deliverables(wps, _fake_fetch(nodes))
    assert "[design_decision] Enclosure material: ABS" in out
    assert "rationale=impact resistance" in out
    # Non-string properties are excluded.
    assert "count" not in out


def test_render_deliverables_survives_fetch_failure() -> None:
    def boom(wp_id: str) -> dict:
        raise OSError("gateway down")

    out = render_deliverables([{"id": "n1", "type": "bom", "name": "BOM v1"}], boom)
    assert "[bom] BOM v1" in out


def test_render_deliverables_truncates_at_cap() -> None:
    wps = [{"id": f"n{i}", "type": "doc", "name": "x"} for i in range(50)]
    nodes = {f"n{i}": {"name": "y" * 100, "properties": {}} for i in range(50)}
    out = render_deliverables(wps, _fake_fetch(nodes), max_chars=300)
    assert "truncated" in out


# --- request shape ------------------------------------------------------------------
def test_judge_request_shape() -> None:
    req = judge_request(_DOD, "- deliverables", "goal", "claude-opus-5")
    assert req["model"] == "claude-opus-5"
    assert req["output_config"]["format"]["schema"] is VERDICT_SCHEMA
    assert req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["messages"][0]["role"] == "user"


def test_verdict_schema_avoids_unsupported_constraints() -> None:
    # The structured-outputs schema subset rejects numeric range constraints.
    text = json.dumps(VERDICT_SCHEMA)
    assert "minimum" not in text and "maximum" not in text
    assert VERDICT_SCHEMA["additionalProperties"] is False


# --- judge_run with the SDK backend (fake client) ---------------------------------------
class _FakeClient:
    def __init__(self, response: object) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def create(self, **kwargs: object) -> object:
                outer.calls.append(kwargs)
                return response

        self.beta = SimpleNamespace(messages=_Messages())


def _response(text: str, stop_reason: str = "end_turn") -> object:
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-opus-5",
        content=[SimpleNamespace(type="text", text=text)],
    )


def test_judge_run_skips_projectless_runs() -> None:
    out = judge_run(lambda prompt: ("{}", "m"), {}, _DOD, "g", "http://x")
    assert "skipped" in out


def test_judge_run_happy_path_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import judge as judge_mod

    monkeypatch.setattr(judge_mod, "collect_deliverables", lambda base, pid: "- [bom] B")
    client = _FakeClient(_response(json.dumps(_VERDICT)))
    complete = sdk_complete(client, "claude-opus-5")
    out = judge_run(complete, {"project_id": "p1"}, _DOD, "goal", "http://x")
    assert out["overall_score"] == 0.5
    assert out["prompt_version"] == PROMPT_VERSION
    assert client.calls[0]["model"] == "claude-opus-5"
    assert client.calls[0]["fallbacks"] == "default"


def test_judge_run_handles_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    import judge as judge_mod

    monkeypatch.setattr(judge_mod, "collect_deliverables", lambda base, pid: "")
    complete = sdk_complete(_FakeClient(_response("", stop_reason="refusal")), "m")
    out = judge_run(complete, {"project_id": "p1"}, _DOD, "g", "http://x")
    assert out["error"] == "judge model refused"


def test_judge_run_records_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import judge as judge_mod

    monkeypatch.setattr(judge_mod, "collect_deliverables", lambda base, pid: "")

    def boom(prompt: str) -> tuple[str, str]:
        raise RuntimeError("rate limited")

    out = judge_run(boom, {"project_id": "p1"}, _DOD, "g", "http://x")
    assert "judge failed" in out["error"]


# --- claude-cli backend ---------------------------------------------------------------
def test_cli_judge_cmd_is_headless_json_print_mode() -> None:
    cmd = cli_judge_cmd("opus")
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "opus" in cmd


def test_parse_cli_envelope_extracts_result() -> None:
    stdout = json.dumps({"type": "result", "is_error": False, "result": "verdict text"})
    assert parse_cli_envelope(stdout) == "verdict text"


def test_parse_cli_envelope_rejects_error_envelope() -> None:
    with pytest.raises(ValueError):
        parse_cli_envelope(json.dumps({"is_error": True, "result": "boom"}))


def _cli_proc(stdout: str, returncode: int = 0, stderr: str = "") -> object:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_judge_run_via_cli_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import judge as judge_mod

    monkeypatch.setattr(judge_mod, "collect_deliverables", lambda base, pid: "- [bom] B")
    seen: list[tuple[list[str], str]] = []

    def fake_runner(cmd: list[str], prompt: str) -> object:
        seen.append((cmd, prompt))
        return _cli_proc(json.dumps({"is_error": False, "result": json.dumps(_VERDICT)}))

    complete = cli_complete("opus", runner=fake_runner)
    out = judge_run(complete, {"project_id": "p1"}, _DOD, "goal", "http://x")
    assert out["overall_score"] == 0.5
    assert out["model"] == "claude-cli/opus"
    assert seen[0][0][0] == "claude"
    assert "numeric safety factor" in seen[0][1]  # the dod reached the subagent


def test_cli_backend_records_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import judge as judge_mod

    monkeypatch.setattr(judge_mod, "collect_deliverables", lambda base, pid: "")
    complete = cli_complete("opus", runner=lambda cmd, prompt: _cli_proc("", 1, "not logged in"))
    out = judge_run(complete, {"project_id": "p1"}, _DOD, "g", "http://x")
    assert "judge failed" in out["error"] and "not logged in" in out["error"]


# --- aggregation ------------------------------------------------------------------------
def test_summarize_judgements() -> None:
    records: list[dict] = [
        {"judge": {"overall_score": 1.0}},
        {"judge": {"overall_score": 0.5}},
        {"judge": {"error": "judge failed: boom"}},
        {},  # unjudged run
    ]
    out = summarize_judgements(records)
    assert out == {"runs_judged": 2, "runs_errored": 1, "avg_overall_score": 0.75}


def test_summarize_judgements_empty() -> None:
    assert summarize_judgements([])["avg_overall_score"] is None
