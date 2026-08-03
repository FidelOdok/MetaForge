#!/usr/bin/env python3
"""LLM-as-judge work-product quality scorer (MET-571).

The deterministic rubrics catch structural hollowness (missing artifacts,
untraceable goals, hand-calcs sold as FEA) but cannot grade *substance*: a
``design_decision`` with a plausible-but-thin rationale, or a CAD model with
the right name and wrong intent, passes them. This scorer closes that gap by
grading each run's work products against the scenario's ``definition_of_done``
with a judge model.

It is a **post-processing pass over an existing report** (from
``run_scenarios.py`` or ``run_chat_scenarios.py``): for each run that created
a project, it fetches the work products that actually landed in the twin,
sends them with the definition of done to the judge, and attaches an advisory
``judge`` block to the run record. Deterministic checks stay authoritative for
pass/fail; judge scores are quality signal for trend diffs.

    python3 evals/judge.py --report evals/report.json --gateway http://fidel-dev:8000
    python3 evals/judge.py --report evals/chat_report.json --only chat_brief_project

Two backends:

- ``--backend sdk`` (default): the ``anthropic`` SDK with an API credential
  (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile). Uses structured
  outputs for guaranteed-valid verdicts and opts into server-side refusal
  fallbacks (``fallbacks="default"``) so a safety decline re-runs on
  Anthropic's recommended fallback model instead of losing the verdict.
- ``--backend claude-cli``: the Claude Code CLI in headless print mode
  (``claude -p``) as a judge subagent — runs on the CLI's own login
  (subscription), no API key needed. Verdict parsing is fence-tolerant since
  print mode has no structured-output guarantee.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))

PROMPT_VERSION = "judge_v1"
DEFAULT_JUDGE_MODEL = "claude-opus-5"

# Keep the deliverable dump bounded so one bloated node can't eat the prompt.
_MAX_DELIVERABLE_CHARS = 30_000
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# Structured-output schema for the verdict. NOTE: numerical range constraints
# are unsupported by the API's schema subset — the 0..1 score range is
# enforced by prompt + clamped in aggregation instead.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phase": {"type": "string"},
                    "score": {"type": "number"},
                    "verdict": {"type": "string", "enum": ["met", "partial", "unmet"]},
                    "missing": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["phase", "score", "verdict", "missing", "rationale"],
                "additionalProperties": False,
            },
        },
        "overall_score": {"type": "number"},
        "summary": {"type": "string"},
    },
    "required": ["phases", "overall_score", "summary"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are an exacting hardware-design reviewer scoring the work products an AI "
    "agent recorded in a digital twin against a definition of done. Judge substance, "
    "not presence: a decision whose rationale merely restates its title, generic "
    "boilerplate that ignores the goal, or an artifact that exists in name only "
    "scores low even though it exists. Score each definition-of-done entry from 0.0 "
    "(absent or hollow) to 1.0 (fully, substantively met), list what is concretely "
    "missing, and keep rationales to one or two sentences."
)


# --- pure helpers (unit-tested without network) --------------------------------
def build_judge_prompt(dod: Any, deliverables: str, goal: str) -> str:
    """Render the user message the judge scores from."""
    dod_text = json.dumps(dod, indent=2) if isinstance(dod, (dict, list)) else str(dod)
    parts = []
    if goal:
        parts.append(f"## Goal given to the agent\n{goal}")
    parts.append(f"## Definition of done\n{dod_text}")
    parts.append(
        "## Work products recorded in the twin\n"
        + (deliverables or "(no work products were recorded)")
    )
    parts.append(
        "Score each definition-of-done entry as one item in `phases` (use the "
        "entry's key as `phase`). Reply with ONLY a JSON object of exactly this "
        'shape: {"phases": [{"phase": str, "score": 0.0-1.0, "verdict": '
        '"met"|"partial"|"unmet", "missing": [str], "rationale": str}], '
        '"overall_score": 0.0-1.0, "summary": str} — all three top-level keys '
        "are required."
    )
    return "\n\n".join(parts)


def parse_judge_reply(text: str) -> dict[str, Any]:
    """Parse the judge's JSON verdict, tolerating a fenced block or stray prose."""
    raw = text.strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    obj = json.loads(raw)
    if not isinstance(obj, dict) or "phases" not in obj:
        raise ValueError(f"judge reply missing phases: {raw[:200]}")
    if "overall_score" not in obj:
        # Backends without structured-output enforcement (claude-cli) sometimes
        # omit the top-level score — derive it from the per-phase scores.
        phases = [p for p in obj.get("phases") or [] if isinstance(p, dict)]
        if not phases:
            raise ValueError(f"judge reply has no scoreable phases: {raw[:200]}")
        obj["overall_score"] = sum(clamp_score(p.get("score")) for p in phases) / len(phases)
    return obj


def clamp_score(value: Any) -> float:
    """Coerce a judge score to a float in [0, 1]."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def render_deliverables(
    work_products: list[dict[str, Any]],
    fetch_node: Callable[[str], dict[str, Any]],
    max_chars: int = _MAX_DELIVERABLE_CHARS,
) -> str:
    """Render a project's work products (name + string properties) as attributed
    text, grouped in listing order, bounded to ``max_chars``."""
    lines: list[str] = []
    total = 0
    for wp in work_products:
        node: dict[str, Any] = {}
        try:
            node = fetch_node(str(wp.get("id", "")))
        except Exception:  # noqa: BLE001 - eval tooling, judge what we can reach
            pass
        props = node.get("properties", {}) or {}
        name = str(node.get("name") or wp.get("name") or "?")
        body = "; ".join(f"{k}={v}" for k, v in props.items() if isinstance(v, str) and v.strip())
        line = f"- [{wp.get('type', '?')}] {name}" + (f": {body}" if body else "")
        if total + len(line) > max_chars:
            lines.append(f"- … truncated at {max_chars} chars …")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def summarize_judgements(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-run judge blocks for the report header."""
    judged = [r["judge"] for r in records if isinstance(r.get("judge"), dict)]
    scored = [j for j in judged if "overall_score" in j]
    errors = [j for j in judged if j.get("error")]
    return {
        "runs_judged": len(scored),
        "runs_errored": len(errors),
        "avg_overall_score": round(
            sum(clamp_score(j["overall_score"]) for j in scored) / len(scored), 3
        )
        if scored
        else None,
    }


# --- gateway + scenario plumbing -------------------------------------------------
def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def collect_deliverables(base: str, project_id: str) -> str:
    """Fetch a project's work products and their twin-node content."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except (urllib.error.URLError, json.JSONDecodeError):
        return ""
    return render_deliverables(
        proj.get("work_products", []),
        lambda wp_id: _get(base, f"/v1/twin/nodes/{wp_id}"),
    )


def load_scenario_index() -> dict[str, dict]:
    """id → scenario fixture, across both suites (for goals + dod fallback)."""
    out: dict[str, dict] = {}
    for sub in ("scenarios", "chat_scenarios"):
        for path in sorted(glob.glob(os.path.join(HERE, sub, "*.json"))):
            sc = json.load(open(path, encoding="utf-8"))
            out[sc["id"]] = sc
    return out


# --- judge invocation --------------------------------------------------------------
class JudgeRefusal(Exception):
    """The judge model's safety classifiers declined the request."""


# A backend turns a prompt into (verdict text, model that produced it).
Complete = Callable[[str], tuple[str, str]]


def judge_request(dod: Any, deliverables: str, goal: str, model: str) -> dict[str, Any]:
    """The messages.create kwargs for one judgement (kept pure for tests)."""
    return {
        "model": model,
        "max_tokens": 4096,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": build_judge_prompt(dod, deliverables, goal)}],
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        # Opt into server-side refusal fallbacks: a safety decline re-runs on
        # Anthropic's recommended fallback model within the same call.
        "fallbacks": "default",
        "betas": ["server-side-fallback-2026-07-01"],
    }


def sdk_complete(client: Any, model: str) -> Complete:
    """Judge through the Anthropic SDK (structured outputs + refusal fallbacks)."""

    def complete(prompt: str) -> tuple[str, str]:
        request = judge_request({}, "", "", model)
        request["messages"] = [{"role": "user", "content": prompt}]
        response = client.beta.messages.create(**request)
        if response.stop_reason == "refusal":
            raise JudgeRefusal()
        text = next(b.text for b in response.content if b.type == "text")
        return text, str(getattr(response, "model", model))

    return complete


def cli_judge_cmd(model: str) -> list[str]:
    """The Claude Code CLI invocation for one headless judge call."""
    return [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--append-system-prompt",
        _SYSTEM,
    ]


def parse_cli_envelope(stdout: str) -> str:
    """Extract the reply text from `claude -p --output-format json` output."""
    envelope = json.loads(stdout)
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        raise ValueError(f"claude CLI returned an error envelope: {stdout[:200]}")
    return str(envelope.get("result", ""))


def cli_complete(model: str, runner: Callable[..., Any] | None = None) -> Complete:
    """Judge through the Claude Code CLI (`claude -p`) as a headless subagent.

    Uses the CLI's own login (a Claude subscription or `claude setup-token`),
    so no ANTHROPIC_API_KEY is needed. The prompt goes in on stdin; the reply
    comes back in the CLI's JSON envelope. No tools run — it is a single
    print-mode completion.
    """
    import subprocess

    def _run(cmd: list[str], prompt: str) -> Any:
        return subprocess.run(  # noqa: S603 - fixed argv, prompt via stdin
            cmd, input=prompt, capture_output=True, text=True, timeout=600
        )

    run = runner or _run

    def complete(prompt: str) -> tuple[str, str]:
        proc = run(cli_judge_cmd(model), prompt)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[:200]}")
        return parse_cli_envelope(proc.stdout), f"claude-cli/{model}"

    return complete


def judge_run(complete: Complete, rec: dict, dod: Any, goal: str, base: str) -> dict:
    """Judge one run record's work products; returns the advisory judge block."""
    project_id = rec.get("project_id")
    if not project_id:
        return {"skipped": "run has no project_id (no work products to judge)"}
    deliverables = collect_deliverables(base, str(project_id))
    try:
        text, model_used = complete(build_judge_prompt(dod, deliverables, goal))
        verdict = parse_judge_reply(text)
    except JudgeRefusal:
        return {"error": "judge model refused", "prompt_version": PROMPT_VERSION}
    except Exception as exc:  # noqa: BLE001 - eval tooling, record not raise
        return {"error": f"judge failed: {exc}"[:300], "prompt_version": PROMPT_VERSION}
    return {
        "model": model_used,
        "prompt_version": PROMPT_VERSION,
        "overall_score": clamp_score(verdict.get("overall_score")),
        "phases": verdict.get("phases", []),
        "summary": str(verdict.get("summary", ""))[:1000],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-as-judge work-product quality scorer")
    ap.add_argument("--report", required=True, help="report JSON from either eval runner")
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--only", default=None, help="judge a single scenario id")
    ap.add_argument("--model", default=os.environ.get("METAFORGE_JUDGE_MODEL"))
    ap.add_argument(
        "--backend",
        choices=["sdk", "claude-cli"],
        default=os.environ.get("METAFORGE_JUDGE_BACKEND", "sdk"),
        help="sdk = Anthropic API (needs ANTHROPIC_API_KEY); "
        "claude-cli = headless `claude -p` subagent (uses the CLI's own login)",
    )
    ap.add_argument("--out", default=None, help="output path (default: augment --report in place)")
    args = ap.parse_args()

    if args.backend == "claude-cli":
        # The CLI resolves its own credentials; model aliases like `opus` are fine.
        model = args.model or "opus"
        complete = cli_complete(model)
    else:
        try:
            from anthropic import Anthropic
        except ImportError:
            print(
                "judge: the `anthropic` SDK is not installed (pip install -e .), "
                "or use --backend claude-cli",
                file=sys.stderr,
            )
            return 2
        model = args.model or DEFAULT_JUDGE_MODEL
        # Client resolves ANTHROPIC_API_KEY / auth profile from env.
        complete = sdk_complete(Anthropic(), model)

    with open(args.report, encoding="utf-8") as fh:
        report = json.load(fh)
    scenarios = load_scenario_index()
    dods: dict[str, Any] = report.get("definitions_of_done") or {}

    judged = 0
    for rec in report.get("runs", []):
        sid = rec.get("scenario", "")
        if args.only and sid != args.only:
            continue
        scenario = scenarios.get(sid, {})
        dod = dods.get(sid) or scenario.get("definition_of_done")
        if not dod:
            continue
        print(f"  ⚖ judging {sid} run {rec.get('run')} …", file=sys.stderr)
        rec["judge"] = judge_run(complete, rec, dod, scenario.get("goal", ""), args.gateway)
        judged += 1
        if "overall_score" in rec["judge"]:
            print(f"    overall={rec['judge']['overall_score']}", file=sys.stderr)
        else:
            print(f"    {rec['judge']}", file=sys.stderr)

    if not judged:
        print("judge: no runs with a definition_of_done to judge", file=sys.stderr)
        return 0

    report["judge"] = {
        "model": model,
        "backend": args.backend,
        "prompt_version": PROMPT_VERSION,
        **summarize_judgements(report.get("runs", [])),
    }
    out = args.out or args.report
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"judge summary: {json.dumps(report['judge'])}\nreport → {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
