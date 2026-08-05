#!/usr/bin/env python3
"""QA-style end-to-end tester for the interactive `forge` TUI.

Drives the *real* compiled binary the way a user would — inside a **tmux**
session, the most faithful driver available: tmux is a real terminal emulator,
so `send-keys` delivers real keystrokes and `capture-pane` returns the actual
rendered screen (cursor moves, clears, and redraws resolved), not a
hand-stripped byte stream. Each scenario asserts on both:

  * the real rendered screen (`capture-pane`) — user-observable truth, and
  * the session log (`chat.turn_done {chars, reason}`) — ground truth immune to
    ANSI redraws and LLM wording variance.

The terminal analog of the Playwright `dashboard-tester`.

    python3 tui/qa/tui_qa.py --stub            # hermetic (built-in stub gateway)
    python3 tui/qa/tui_qa.py --gateway URL     # against a real gateway
    python3 tui/qa/tui_qa.py --stub --watch     # observe it drive the TUI live
    python3 tui/qa/tui_qa.py --stub --keep       # leave the session up to attach

Interactive: because it is a real tmux session you can watch or take over —
`--watch` slows it down and prints a screen snapshot after each step, `--keep`
leaves the session alive at the end, and either way you can run
`tmux attach -t <session>` in another terminal to see the keystrokes land live.

Exit code is non-zero if any scenario fails, so CI/release can gate on it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

SESSION = "forge-qa"


class Tmux:
    """A forge TUI running inside a real tmux session."""

    def __init__(
        self,
        binary: str,
        env_extra: dict[str, str],
        cols: int,
        rows: int,
        watch: bool,
        args: list[str] | None = None,
    ):
        self.watch = watch
        subprocess.run(["tmux", "kill-session", "-t", SESSION], capture_output=True)
        cmd = ["tmux", "new-session", "-d", "-s", SESSION, "-x", str(cols), "-y", str(rows)]
        for k, v in env_extra.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [binary, *(args or [])]
        subprocess.run(cmd, check=True)

    def type(self, text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", SESSION, "-l", text], check=True)

    def key(self, *keys: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", SESSION, *keys], check=True)

    def capture(self) -> str:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", SESSION, "-p"], capture_output=True, text=True
        )
        return r.stdout

    def capture_full(self, scrollback: int = 120) -> str:
        """Capture the visible pane plus `scrollback` lines of history — needed to
        catch frames Ink may have orphaned above the viewport (the class of bug
        where a mid-transition layout strands a duplicate input box / footer)."""
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", SESSION, "-p", "-S", f"-{scrollback}"],
            capture_output=True,
            text=True,
        )
        return r.stdout

    def wait_for(self, needle: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if needle.lower() in self.capture().lower():
                return True
            time.sleep(0.4)
        return False

    def alive(self) -> bool:
        return (
            subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode
            == 0
        )

    def kill(self) -> None:
        subprocess.run(["tmux", "kill-session", "-t", SESSION], capture_output=True)

    def snap(self, label: str) -> None:
        if not self.watch:
            return
        print(f"\n----- {label} -----", file=sys.stderr)
        print(self.capture(), file=sys.stderr)
        time.sleep(1.2)


# --- log oracle ------------------------------------------------------------


def read_turns(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        return []
    turns = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("msg") == "chat.turn_done":
                turns.append(rec)
    return turns


def wait_for_new_turn(tui: Tmux, log_path: str, prev: int, timeout: float) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        turns = read_turns(log_path)
        if len(turns) > prev:
            return turns[-1]
        time.sleep(0.5)
    return None


def converse(
    tui: Tmux, log_path: str, prompts: list[str], timeout: float = 60
) -> list[dict | None]:
    """Send each prompt as a turn and collect its logged chat.turn_done record."""
    out: list[dict | None] = []
    for prompt in prompts:
        prev = len(read_turns(log_path))
        tui.type(prompt)
        tui.key("Enter")
        out.append(wait_for_new_turn(tui, log_path, prev, timeout))
    return out


# --- report ----------------------------------------------------------------


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.results.append((name, passed, detail))
        print(f"  [{'PASS' if passed else 'FAIL'}] {name} — {detail}", file=sys.stderr)

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)


def stub_threads(gateway: str) -> list[dict]:
    """Threads the client actually created, straight from the stub.

    The UI's claim about scope is exactly what regressed once (the footer showed
    a project the thread wasn't in), so QA asserts the *wire* scope too.
    """
    import urllib.request

    with urllib.request.urlopen(f"{gateway}/__threads", timeout=5) as r:
        return json.loads(r.read()).get("threads", [])


def _fmt(turn: dict | None) -> str:
    if not turn:
        return "none (no turn_done logged)"
    return f"chars={turn.get('chars')}, deltas={turn.get('deltas')}, reason={turn.get('reason')}"


# The idle input box always shows this placeholder when empty; the footer always
# shows the nav-key hint. Counting each across a scrollback-inclusive capture is
# how we detect a stranded/duplicated frame.
INPUT_PLACEHOLDER = "message  (/model"
FOOTER_FINGERPRINT = "^T/^R"


def max_blank_run(text: str) -> int:
    """Longest run of consecutive blank (whitespace-only) lines."""
    best = run = 0
    for line in text.splitlines():
        run = run + 1 if not line.strip() else 0
        best = max(best, run)
    return best


def input_row_fraction(pane: str) -> float | None:
    """Where the input box sits vertically in the visible pane, 0.0 (top) →
    1.0 (bottom). None if the placeholder isn't visible (input has text)."""
    lines = pane.splitlines()
    if not lines:
        return None
    for i, line in enumerate(lines):
        if INPUT_PLACEHOLDER in line:
            return i / max(1, len(lines) - 1)
    return None


# --- scenarios -------------------------------------------------------------


def run_scenarios(tui: Tmux, log_path: str, rep: Report, stub: bool, gateway: str) -> None:
    # 1. Launch + gateway health render in the status bar.
    healthy = tui.wait_for("healthy", timeout=30)
    scr = tui.capture()
    nav = "chat" in scr and "twin" in scr
    tui.snap("after launch")
    rep.add("launch_and_health", healthy and nav, f"healthy={healthy}, nav_bar={nav}")

    # Wait until the chat stream is connected (idle input placeholder shows) —
    # "healthy" in the footer can render before the SSE stream opens, and typing
    # into a still-"connecting…" input drops the send.
    ready = tui.wait_for(INPUT_PLACEHOLDER, timeout=20)

    # 1b. Launch layout: the input box is pinned to the BOTTOM of the terminal
    #     (a proper "app" launch screen), not clustered at the top. Checked
    #     before the first turn, while the empty-session full-height layout is up.
    launch_pane = tui.capture()
    frac = input_row_fraction(launch_pane)
    bottom_pinned = frac is not None and frac >= 0.6
    tui.snap("launch layout")
    frac_str = "None" if frac is None else round(frac, 2)
    rep.add(
        "launch_input_bottom_pinned",
        bottom_pinned and ready,
        f"ready={ready}, input_row_fraction={frac_str} (want >= 0.6)",
    )

    # 2. Greeting → a real streamed reply (screen + log oracle).
    prev = len(read_turns(log_path))
    tui.type("hello, answer in one short sentence")
    tui.key("Enter")
    turn = wait_for_new_turn(tui, log_path, prev, timeout=45)
    log_ok = bool(turn and turn.get("chars", 0) > 0 and not turn.get("reason"))
    screen_ok = not tui.wait_for("(no reply", timeout=1)
    tui.snap("after greeting")
    rep.add(
        "chat_greeting_streams_reply",
        log_ok and screen_ok,
        f"turn={_fmt(turn)}, no_reply_on_screen={not screen_ok}",
    )

    # 2b. Layout after the first turn: the launch→transcript switch must be clean.
    #     Regression guard for the App/Chat desync where a mid-transition frame
    #     stranded a DUPLICATE input box + footer into scrollback and opened a
    #     huge blank gap. Assert exactly one input box + one footer across the
    #     full scrollback, and no oversized blank gap in the visible frame.
    time.sleep(0.8)  # let the frame settle
    full = tui.capture_full()
    inputs = full.count(INPUT_PLACEHOLDER)
    footers = full.count(FOOTER_FINGERPRINT)
    gap = max_blank_run(tui.capture())  # visible pane: content-hug, so no spacer
    clean = inputs == 1 and footers == 1 and gap <= 6
    tui.snap("layout after first turn")
    rep.add(
        "chat_transition_no_gap_no_duplication",
        clean,
        f"input_boxes={inputs} (want 1), footers={footers} (want 1), "
        f"max_blank_gap={gap} (want <= 6)",
    )

    # 3. Second turn → confirms the stream stays open across turns.
    prev = len(read_turns(log_path))
    tui.type("How many projects exist?")
    tui.key("Enter")
    turn = wait_for_new_turn(tui, log_path, prev, timeout=60)
    rep.add(
        "chat_second_turn_answers",
        bool(turn and turn.get("chars", 0) > 0 and not turn.get("reason")),
        f"turn={_fmt(turn)}",
    )

    # 3b. A full multi-turn conversation stays healthy end to end.
    convo = [
        "Hi there.",
        "I'm designing a small quadruped robot.",
        "What class of actuators would suit the legs?",
        "And what should I consider for the battery?",
        "Thanks — briefly summarize what we discussed.",
    ]
    turns = converse(tui, log_path, convo)
    answered = sum(1 for t in turns if t and t.get("chars", 0) > 0 and not t.get("reason"))
    no_reply = tui.wait_for("(no reply", timeout=1)
    tui.snap("after full conversation")
    rep.add(
        "full_conversation",
        answered == len(convo) and not no_reply,
        f"{answered}/{len(convo)} turns answered, no_reply_seen={no_reply}",
    )

    # 3c. Context carries across turns (live only — the stub can't remember, it
    #     replies with fixed scripted text regardless of input).
    if not stub:
        converse(tui, log_path, ["Please remember: my name is Fidel and I'm building a quadruped."])
        converse(tui, log_path, ["What is my name and what am I building?"])
        scr = tui.capture().lower()
        carried = "fidel" in scr and "quadruped" in scr
        tui.snap("after context-carry")
        rep.add(
            "conversation_context_carry",
            carried,
            f"name_recalled={'fidel' in scr}, topic_recalled={'quadruped' in scr}",
        )

    # 4. Empty-turn cause is surfaced (stub only — deterministically malformed SSE).
    if stub:
        prev = len(read_turns(log_path))
        tui.type("__malformed__ trigger a broken stream")
        tui.key("Enter")
        turn = wait_for_new_turn(tui, log_path, prev, timeout=30)
        reason = (turn or {}).get("reason") or ""
        cause_logged = "parse mismatch" in reason
        cause_shown = tui.wait_for("no reply", timeout=3)
        tui.snap("after malformed")
        rep.add(
            "empty_turn_shows_cause",
            cause_logged and cause_shown,
            f"reason={reason!r}, shown_on_screen={cause_shown}",
        )

        # 4b. A completion event lost on reconnect must not wedge the chat. The
        #     stub streams a reply but never sends `agent.done`; a fixed client
        #     finalizes via the POST-resolve fallback (grace ~2.5s) — the reply
        #     lands and the turn goes idle instead of hanging on "thinking".
        prev = len(read_turns(log_path))
        tui.type("__drop_done__ say hello")
        tui.key("Enter")
        turn = wait_for_new_turn(tui, log_path, prev, timeout=20)
        recovered = not tui.wait_for("thinking", timeout=1)
        shows_reply = "stub reply" in tui.capture()
        tui.snap("after dropped agent.done")
        rep.add(
            "wedge_recovers_without_agent_done",
            bool(turn) and recovered and shows_reply,
            f"turn={_fmt(turn)}, not_thinking={recovered}, reply_shown={shows_reply}",
        )

    # 4c. Project scope: the footer must report the *thread's* scope, and
    #     `/project` must actually re-scope the thread server-side. Guards the
    #     bug where the footer showed whichever project the API listed first, so
    #     an unscoped session looked project-scoped (MET-578).
    if stub:
        unscoped = "no project" in tui.capture()
        before = len(stub_threads(gateway))

        tui.type("/project gimbal")  # unique substring of "QA Gimbal"
        tui.key("Enter")
        switched = tui.wait_for("QA Gimbal", timeout=15)
        time.sleep(0.8)
        threads = stub_threads(gateway)
        last = threads[-1] if threads else {}
        wire_ok = (
            len(threads) == before + 1
            and last.get("scope_kind") == "project"
            and last.get("scope_entity_id") == "p2"
        )
        tui.snap("after /project gimbal")
        rep.add(
            "project_scope_switch",
            unscoped and switched and wire_ok,
            f"launched_unscoped={unscoped}, footer_shows_project={switched}, "
            f"thread_scope={last.get('scope_kind')}/{last.get('scope_entity_id')} "
            f"(want project/p2)",
        )

        # An ambiguous name must be refused, not guessed — and must not spend a
        # thread doing it.
        before = len(stub_threads(gateway))
        tui.type("/project qa")  # substring of BOTH stub projects
        tui.key("Enter")
        refused = tui.wait_for("matches 2 projects", timeout=10)
        time.sleep(0.5)
        no_new_thread = len(stub_threads(gateway)) == before
        still_scoped = "QA Gimbal" in tui.capture()
        tui.snap("after ambiguous /project")
        rep.add(
            "project_ambiguous_refused",
            refused and no_new_thread and still_scoped,
            f"refused={refused}, thread_unchanged={no_new_thread}, scope_kept={still_scoped}",
        )

        # `/project none` detaches: back to an assistant-scoped thread.
        before = len(stub_threads(gateway))
        tui.type("/project none")
        tui.key("Enter")
        detached = tui.wait_for("no project", timeout=15)
        time.sleep(0.8)
        threads = stub_threads(gateway)
        last = threads[-1] if threads else {}
        wire_ok = len(threads) == before + 1 and last.get("scope_kind") == "assistant"
        tui.snap("after /project none")
        rep.add(
            "project_scope_detach",
            detached and wire_ok,
            f"footer_no_project={detached}, thread_scope={last.get('scope_kind')} (want assistant)",
        )

    # 5. Pane navigation: chat → runs (C-r) → twin (C-b) → chat (C-t).
    tui.key("C-r")
    time.sleep(0.8)
    runs_ok = tui.alive()
    tui.snap("runs pane")
    tui.key("C-b")
    time.sleep(0.8)
    twin_ok = tui.alive()
    tui.snap("twin pane")
    tui.key("C-t")
    back_ok = tui.wait_for("message", timeout=3)  # chat input placeholder returns
    tui.snap("back to chat")
    rep.add(
        "pane_navigation",
        runs_ok and twin_ok and back_ok,
        f"runs={runs_ok}, twin_survived={twin_ok}, back_to_chat={back_ok}",
    )

    # 6. Clean quit on Esc.
    tui.key("Escape")
    end = time.time() + 5
    while tui.alive() and time.time() < end:
        time.sleep(0.5)
    rep.add("quit_on_esc", not tui.alive(), f"session ended={not tui.alive()}")


def run_launch_scoped(
    binary: str,
    env_extra: dict[str, str],
    cols: int,
    rows: int,
    watch: bool,
    gateway: str,
    rep: Report,
) -> None:
    """`forge --project <name>` starts already scoped — a second session, since
    the flag is only read at launch."""
    before = len(stub_threads(gateway))
    tui = Tmux(binary, env_extra, cols, rows, watch, args=["--project", "QA Gimbal"])
    try:
        shown = tui.wait_for("QA Gimbal", timeout=25)
        threads = stub_threads(gateway)
        last = threads[-1] if threads else {}
        wire_ok = (
            len(threads) == before + 1
            and last.get("scope_kind") == "project"
            and last.get("scope_entity_id") == "p2"
        )
        # Only one thread: the flag must be resolved *before* the thread is
        # created, not after a throwaway unscoped one.
        tui.snap("launched with --project")
        rep.add(
            "launch_with_project_flag",
            shown and wire_ok,
            f"footer_shows_project={shown}, threads_created={len(threads) - before} (want 1), "
            f"thread_scope={last.get('scope_kind')}/{last.get('scope_entity_id')}",
        )

        # An unknown name is reported, and the session continues unscoped rather
        # than pretending it worked.
        tui.kill()
        tui = Tmux(binary, env_extra, cols, rows, watch, args=["--project", "nope"])
        reported = tui.wait_for("no project matches", timeout=25)
        unscoped = tui.wait_for("no project", timeout=10)
        tui.snap("launched with a bad --project")
        rep.add(
            "launch_with_bad_project_flag",
            reported and unscoped,
            f"error_shown={reported}, footer_no_project={unscoped}",
        )
    finally:
        tui.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description="QA-style E2E tester for the forge TUI (tmux)")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--binary", default=os.path.join(here, "..", "dist", "forge"))
    ap.add_argument(
        "--gateway", default=os.environ.get("FORGE_QA_GATEWAY", "http://fidel-dev:8000")
    )
    ap.add_argument("--stub", action="store_true", help="use the built-in hermetic stub gateway")
    ap.add_argument("--watch", action="store_true", help="print a screen snapshot after each step")
    ap.add_argument("--keep", action="store_true", help="leave the tmux session alive at the end")
    ap.add_argument("--json", action="store_true", help="emit a JSON report on stdout")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=34)
    args = ap.parse_args()

    if subprocess.run(["tmux", "-V"], capture_output=True).returncode != 0:
        print("tmux is required (apt install tmux)", file=sys.stderr)
        return 2

    binary = os.path.abspath(args.binary)
    if not os.path.exists(binary):
        alt = os.path.expanduser("~/.local/bin/forge")
        binary = alt if os.path.exists(alt) else binary
    if not os.path.exists(binary):
        print(f"forge binary not found: {binary}", file=sys.stderr)
        return 2

    server = None
    gateway = args.gateway
    if args.stub:
        from stub_gateway import start_stub

        server, gateway = start_stub()

    # Hermetic HOME so the run uses our config + log, not the user's.
    home = os.path.join(here, ".qa-home")
    os.makedirs(os.path.join(home, ".forge", "logs"), exist_ok=True)
    with open(os.path.join(home, ".forge", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "gateway_url": gateway,
                "provider": "openrouter",
                "model": "openai/gpt-4o",
                "mode": "ask",
            },
            fh,
        )
    log_path = os.path.join(home, ".forge", "logs", "session.log")
    if os.path.exists(log_path):
        os.remove(log_path)

    env_extra = {
        "HOME": home,
        "TERM": "xterm-256color",
        "PATH": os.environ.get("PATH", ""),
        "FORGE_LOG": "1",
        "FORGE_LOG_FILE": log_path,
    }

    print(f"QA (tmux): binary={binary}", file=sys.stderr)
    print(f"           gateway={gateway}  stub={args.stub}", file=sys.stderr)
    print(f"           attach live with:  tmux attach -t {SESSION}", file=sys.stderr)
    rep = Report()
    tui = Tmux(binary, env_extra, args.cols, args.rows, args.watch)
    try:
        run_scenarios(tui, log_path, rep, args.stub, gateway)
        if args.stub and not args.keep:
            # Needs its own session (the flag is launch-time), so run it after the
            # main session has quit on Esc.
            run_launch_scoped(binary, env_extra, args.cols, args.rows, args.watch, gateway, rep)
    finally:
        if args.keep:
            print(
                f"\nSession left alive — `tmux attach -t {SESSION}` "
                f"(kill: tmux kill-session -t {SESSION})",
                file=sys.stderr,
            )
        else:
            tui.kill()
        if server is not None:
            server.shutdown()

    total = len(rep.results)
    passed = total - rep.failed
    print(f"\nQA summary: {passed}/{total} scenarios passed", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "total": total,
                    "results": [{"name": n, "passed": ok, "detail": d} for n, ok, d in rep.results],
                },
                indent=2,
            )
        )
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
