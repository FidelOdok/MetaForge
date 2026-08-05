#!/usr/bin/env python3
"""Multi-turn chat context-engineering eval runner (MET-570).

Drives scripted multi-turn conversations through the *live* harness-backed
chat surface (the agent turn runs synchronously inside
``POST /v1/chat/threads/{id}/messages``) and scores what came back: needle
recall across turns, project-brief adherence, window/trim telemetry, and
tool-call trajectory quality. ``agent.step`` + ``context.stats`` are SSE-only
(never persisted), so a background listener subscribes to the thread's
``/stream`` for the whole conversation and events are sliced per turn.

Baseline semantics: a scenario may declare ``expected_today`` — checks KNOWN
to fail on the current harness (e.g. facts past the 20-turn history slice,
until MET-568 lands compaction). Those score ``xfail`` (confirmed baseline)
and flip to ``xpass`` when the fix ships. A green baseline run is one with no
*unexpected* failures; ``--strict`` turns any unexpected failure into a
non-zero exit for threshold gating.

    python3 evals/run_chat_scenarios.py --gateway http://fidel-dev:8000
    python3 evals/run_chat_scenarios.py --scenario chat_mem_needle --variant native

Stdlib only; talks HTTP to a running gateway with METAFORGE_CHAT_HARNESS=1.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:  # sibling rubric imports when invoked from another cwd
    sys.path.insert(0, HERE)

from chat_rubric_common import (  # noqa: E402
    agent_replies_after,
    evaluate_declared,
    expand_turns,
    expected_for_variant,
    resolve_outcomes,
    step_payload,
    substitute,
    unwrap_sse_data,
)

_DEFAULT_VARIANTS = [{"name": "default", "provider": None, "model": None}]
_TURN_DONE_EVENTS = {"agent.done", "error"}


def api(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def load_scenarios(only: str | None) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "chat_scenarios", "*.json"))):
        sc = json.load(open(path, encoding="utf-8"))
        if only is None or sc["id"] == only:
            out.append(sc)
    return out


class StreamListener:
    """Background SSE subscriber for one thread's event stream.

    ``agent.step`` and ``context.stats`` are broadcast-only — they exist ONLY
    on the live stream, and all of a turn's events are emitted before the
    message POST returns. One listener covers the whole conversation; the
    runner slices ``events`` by index around each POST.
    """

    def __init__(self, url: str, read_timeout: float) -> None:
        self._url = url
        self._read_timeout = read_timeout
        self.events: list[dict[str, Any]] = []
        self.connected = threading.Event()
        self.stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> StreamListener:
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            req = urllib.request.Request(self._url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(  # noqa: S310 - dev gateway
                req, timeout=self._read_timeout
            ) as resp:
                self.connected.set()
                event_type: str | None = None
                for raw in resp:
                    if self.stopped.is_set():
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        payload = unwrap_sse_data(line[len("data:") :].strip())
                        self.events.append({"event": event_type, "data": payload})
                        event_type = None
        except Exception:  # noqa: BLE001 - listener is best-effort; turns still work
            pass
        finally:
            self.stopped.set()

    def wait_turn_events(self, start_idx: int, grace_s: float = 8.0) -> list[dict[str, Any]]:
        """Events after ``start_idx``, waiting briefly for the turn's terminator."""
        deadline = time.time() + grace_s
        while time.time() < deadline and not self.stopped.is_set():
            slice_ = self.events[start_idx:]
            if any(e.get("event") in _TURN_DONE_EVENTS for e in slice_):
                break
            time.sleep(0.25)
        return list(self.events[start_idx:])


_RUN_ID_RE = re.compile(r"run_[0-9a-f]{8,}")


def find_run_id(turns: list[dict[str, Any]]) -> str | None:
    """The most recent design-flow run id mentioned in prior turns.

    Scans tool observations first (authoritative — the launch tool's result
    carries it), then replies, newest turn first.
    """
    for turn in reversed(turns):
        for source in (
            *[str(s.get("observation") or "") for s in reversed(turn.get("steps") or [])],
            str(turn.get("reply") or ""),
        ):
            m = _RUN_ID_RE.search(source)
            if m:
                return m.group(0)
    return None


def approve_run_action(base: str, turns: list[dict[str, Any]], timeout_s: float) -> dict[str, Any]:
    """Runner-performed gate approval (the ``approve_run`` gateway action).

    Finds the run the agent launched earlier in the conversation, waits for it
    to pause at its gate, approves, and confirms the run resumed — so a later
    agent turn can report the post-approval state. Every step is recorded;
    failure never raises (the ``action_succeeded`` check surfaces it).
    """
    record: dict[str, Any] = {"action": "approve_run", "action_ok": False}
    run_id = find_run_id(turns)
    if not run_id:
        record["action_detail"] = "no run id found in prior turns"
        return record
    record["run_id"] = run_id
    try:
        deadline = time.time() + timeout_s
        status = None
        while time.time() < deadline:
            status = api(base, "GET", f"/v1/runs/{run_id}").get("status")
            if status in ("awaiting_approval", "completed", "failed", "rejected"):
                break
            time.sleep(3)
        record["status_before"] = status
        if status != "awaiting_approval":
            record["action_detail"] = f"run never paused at a gate (status={status})"
            return record
        api(base, "POST", f"/v1/runs/{run_id}/approval", {"decision": "approve"})
        # Approval flips the run back to running immediately.
        record["status_after"] = api(base, "GET", f"/v1/runs/{run_id}").get("status")
        record["action_ok"] = record["status_after"] != "awaiting_approval"
        if not record["action_ok"]:
            record["action_detail"] = "run still awaiting approval after approve"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        record["action_detail"] = f"approval failed: {exc}"[:300]
    return record


def path_mechanism(variant: dict, force_label: str | None) -> str:
    if force_label:
        return "gateway_env"
    return "provider_override" if variant.get("provider") else "default"


def run_conversation(
    base: str, scenario: dict, variant: dict, idx: int, args: argparse.Namespace
) -> dict:
    started = time.time()
    vname = str(variant.get("name", "default"))
    rec: dict = {
        "scenario": scenario["id"],
        "variant": vname,
        "path_mechanism": path_mechanism(variant, args.force_path_label),
        "run": idx,
        "turns": [],
        "error": None,
    }
    if args.force_path_label:
        rec["forced_path_label"] = args.force_path_label

    project_id: str | None = None
    project_name: str | None = None
    try:
        if scenario.get("scope") == "project":
            project_name = f"eval-{scenario['id']}-{vname}-{idx}-{int(started)}"
            body: dict[str, Any] = {"name": project_name}
            # The API rejects an explicit null description (422) — only send
            # the field when the scenario declares one.
            if scenario.get("project_description"):
                body["description"] = scenario["project_description"]
            proj = api(base, "POST", "/v1/projects", body)
            project_id = proj["id"]
            rec["project_id"] = project_id
            rec["project_name"] = project_name
            thread = api(
                base,
                "POST",
                "/v1/chat/threads",
                {
                    "scope_kind": "project",
                    "scope_entity_id": project_id,
                    "title": f"chat-eval-{scenario['id']}-{idx}",
                },
            )
        else:
            # Like `forge chat`, assistant-scope threads get a fresh entity id
            # per conversation so runs never share history.
            thread = api(
                base,
                "POST",
                "/v1/chat/threads",
                {
                    "scope_kind": "assistant",
                    "scope_entity_id": f"chat-eval-{scenario['id']}-{vname}-{idx}-{int(started)}",
                    "title": f"chat-eval-{scenario['id']}-{idx}",
                },
            )
        thread_id = thread["id"]
        rec["thread_id"] = thread_id
    except (urllib.error.URLError, KeyError) as exc:
        rec["error"] = f"setup failed: {exc}"
        return rec

    max_turn_s = float(scenario.get("max_turn_seconds", args.max_turn_seconds))
    listener = StreamListener(
        base.rstrip("/") + f"/v1/chat/threads/{thread_id}/stream",
        read_timeout=max_turn_s,
    ).start()
    sse_up = listener.connected.wait(timeout=10.0)

    # $RUN_TAG uniquifies artifact names the agent is asked to create, so
    # re-runs don't collide with a previous run's artifacts (MET-579).
    subs = {"$RUN_TAG": str(int(started))}
    if project_id:
        subs["$PROJECT_ID"] = project_id
    turns = substitute(expand_turns(scenario["turns"]), subs)
    for t_idx, turn in enumerate(turns):
        turn_started = time.time()

        # Runner-performed HTTP step between agent turns (no message posted).
        # Currently: "approve_run" — approve the gate a launched flow paused
        # at, so the next agent turn can report the post-approval state.
        if turn.get("gateway_action"):
            action_record: dict[str, Any] = {
                "index": t_idx,
                "say": f"[gateway action: {turn['gateway_action']}]",
                "tag": turn.get("tag"),
                "checks": turn.get("checks") or [],
                "reply": "",
                "reply_error": False,
                "steps": [],
                "context_stats": None,
                "sse": sse_up,
            }
            if turn["gateway_action"] == "approve_run":
                action_record.update(approve_run_action(base, rec["turns"], max_turn_s))
            else:
                action_record["action_ok"] = False
                action_record["action_detail"] = (
                    f"unknown gateway_action {turn['gateway_action']!r}"
                )
            action_record["duration_s"] = round(time.time() - turn_started, 1)
            rec["turns"].append(action_record)
            continue

        record: dict[str, Any] = {
            "index": t_idx,
            "say": turn["say"],
            "tag": turn.get("tag"),
            "checks": turn.get("checks") or [],
            "reply": "",
            "reply_error": False,
            "steps": [],
            "context_stats": None,
            "sse": sse_up,
        }
        mark = len(listener.events)
        try:
            body = {
                "content": turn["say"],
                "actor_id": "chat-eval",
                "actor_kind": "user",
                "provider": variant.get("provider"),
                "model": variant.get("model"),
            }
            msg = api(
                base,
                "POST",
                f"/v1/chat/threads/{thread_id}/messages",
                body,
                timeout=max_turn_s,
            )
            events = listener.wait_turn_events(mark) if sse_up else []
            record["steps"] = [
                step_payload(e["data"]) for e in events if e.get("event") == "agent.step"
            ]
            stats = [e["data"] for e in events if e.get("event") == "context.stats"]
            record["context_stats"] = stats[0] if stats else None

            full = api(base, "GET", f"/v1/chat/threads/{thread_id}")
            replies = agent_replies_after(full.get("messages", []), msg.get("id", ""))
            record["reply"] = "\n".join(str(m.get("content") or "") for m in replies)
            record["reply_error"] = not replies or any(m.get("status") == "error" for m in replies)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record["reply_error"] = True
            record["turn_exception"] = str(exc)[:300]
        record["duration_s"] = round(time.time() - turn_started, 1)
        rec["turns"].append(record)

    listener.stopped.set()

    # --- scoring -----------------------------------------------------------
    declared = evaluate_declared(rec["turns"])
    rec["declared_checks"] = declared
    all_checks = dict(declared)

    rubrics = scenario.get("rubric")
    rubrics = [rubrics] if isinstance(rubrics, str) else (rubrics or [])
    for name in rubrics:
        if name == "chat_memory":
            from chat_memory_rubric import score_chat_memory

            rec["chat_memory_rubric"] = score_chat_memory(rec["turns"])
        elif name == "chat_brief":
            from chat_brief_rubric import score_chat_brief

            rec["chat_brief_rubric"] = score_chat_brief(
                base, project_id or "", project_name or "", rec["turns"]
            )
        elif name == "chat_window":
            from chat_window_rubric import score_chat_window

            rec["chat_window_rubric"] = score_chat_window(rec["turns"])
        elif name == "chat_tooluse":
            from chat_tooluse_rubric import score_chat_tooluse

            rec["chat_tooluse_rubric"] = score_chat_tooluse(rec["turns"])
        rub = rec.get(f"{name}_rubric")
        if rub:
            all_checks.update(rub.get("checks", {}))

    expected = expected_for_variant(scenario.get("expected_today"), vname)
    rec["outcomes"] = resolve_outcomes(all_checks, expected)
    rec["duration_s"] = round(time.time() - started, 1)
    return rec


def _outcome_counts(outcomes: dict[str, str]) -> dict[str, int]:
    return {
        "checks_total": len(outcomes),
        "passed": sum(1 for o in outcomes.values() if o == "pass"),
        "failed_unexpected": sum(1 for o in outcomes.values() if o == "fail"),
        "xfail_confirmed": sum(1 for o in outcomes.values() if o == "xfail"),
        "xpass_improved": sum(1 for o in outcomes.values() if o == "xpass"),
    }


def summarize(records: list[dict]) -> dict:
    by: dict[str, dict[str, list[dict]]] = {}
    for r in records:
        by.setdefault(r["scenario"], {}).setdefault(r["variant"], []).append(r)
    summary: dict[str, Any] = {}
    for sid, variants in by.items():
        summary[sid] = {}
        for vname, rs in variants.items():
            counts = {
                "checks_total": 0,
                "passed": 0,
                "failed_unexpected": 0,
                "xfail_confirmed": 0,
                "xpass_improved": 0,
            }
            for r in rs:
                for key, val in _outcome_counts(r.get("outcomes", {})).items():
                    counts[key] += val
            n = len(rs)
            summary[sid][vname] = {
                "runs": n,
                **counts,
                "turns_errored": sum(
                    1 for r in rs for t in r.get("turns", []) if t.get("reply_error")
                ),
                "avg_duration_s": round(sum(r.get("duration_s", 0) for r in rs) / n, 1) if n else 0,
            }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Multi-turn chat context eval runner")
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--scenario", default=None, help="run a single scenario id (default: all)")
    ap.add_argument("--variant", default=None, help="run a single variant name (default: all)")
    ap.add_argument("--runs", type=int, default=1, help="runs per scenario x variant")
    ap.add_argument("--max-turn-seconds", type=float, default=300, help="per-turn POST timeout")
    ap.add_argument(
        "--force-path-label",
        default=None,
        help="label runs as this path (e.g. 'react' when METAFORGE_NATIVE_TOOLS=0 gateway-side)",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any unexpected check failure (threshold gate)",
    )
    ap.add_argument("--out", default=os.path.join(HERE, "chat_report.json"))
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenario)
    if not scenarios:
        print(f"no scenarios found (id={args.scenario})", file=sys.stderr)
        return 2

    print(
        f"chat eval: gateway={args.gateway}  scenarios={[s['id'] for s in scenarios]}  "
        f"runs={args.runs}",
        file=sys.stderr,
    )
    records = []
    for sc in scenarios:
        variants = sc.get("variants") or _DEFAULT_VARIANTS
        for variant in variants:
            if args.variant and variant.get("name") != args.variant:
                continue
            for i in range(1, args.runs + 1):
                print(
                    f"  ▶ {sc['id']} [{variant.get('name')}] run {i}/{args.runs} …",
                    file=sys.stderr,
                )
                rec = run_conversation(args.gateway, sc, variant, i, args)
                records.append(rec)
                counts = _outcome_counts(rec.get("outcomes", {}))
                print(
                    f"    checks={counts['checks_total']}  pass={counts['passed']}  "
                    f"fail={counts['failed_unexpected']}  xfail={counts['xfail_confirmed']}  "
                    f"xpass={counts['xpass_improved']}  {rec.get('duration_s', 0)}s",
                    file=sys.stderr,
                )
                if rec.get("error"):
                    print(f"    error: {rec['error']}", file=sys.stderr)
                for name in (
                    "chat_memory",
                    "chat_brief",
                    "chat_window",
                    "chat_tooluse",
                ):
                    rub = rec.get(f"{name}_rubric")
                    if rub:
                        failing = [k for k, v in rub.get("checks", {}).items() if not v]
                        print(
                            f"    {name} rubric: {rub.get('score')}  failing={failing or 'none'}",
                            file=sys.stderr,
                        )

    report = {
        "gateway": args.gateway,
        "suite": "chat_context_v1",
        "summary": summarize(records),
        "runs": records,
        # Carried through for the LLM-as-judge scorer (evals/judge.py), same
        # convention as run_scenarios.py.
        "definitions_of_done": {
            s["id"]: s["definition_of_done"] for s in scenarios if s.get("definition_of_done")
        },
    }
    if os.path.dirname(args.out):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\n=== summary ===\n{json.dumps(report['summary'], indent=2)}", file=sys.stderr)
    print(f"report → {args.out}", file=sys.stderr)

    if args.strict:
        unexpected = sum(
            s["failed_unexpected"] for sv in report["summary"].values() for s in sv.values()
        )
        if unexpected:
            print(f"strict: {unexpected} unexpected failure(s)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
