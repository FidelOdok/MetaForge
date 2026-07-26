# forge TUI — QA harness

QA-style end-to-end testing for the interactive `forge` TUI. Drives the **real
compiled binary** inside a **tmux** session — the most faithful driver available:
tmux is a real terminal emulator, so `send-keys` delivers real keystrokes and
`capture-pane` returns the **actual rendered screen** (cursor moves, clears and
redraws resolved), not a hand-stripped byte stream. The terminal analog of the
Playwright `dashboard-tester`.

Each scenario asserts on **both**:

- the real rendered screen (`capture-pane`) — what a human sees, and
- the session log (`chat.turn_done {chars, reason}`) — ground truth, immune to
  ANSI redraws and LLM wording variance.

## Requirements

- `tmux` (`tmux -V`) — the driver
- `python3` — stdlib only, no packages
- a built binary: `npm run bundle:bin` → `dist/forge` (or an installed `forge`)

## Run

```bash
# Hermetic — built-in stub gateway, no live services, deterministic (CI mode):
python3 tui/qa/tui_qa.py --stub

# Against a real gateway:
python3 tui/qa/tui_qa.py --gateway http://fidel-dev:8000

# Machine-readable report:
python3 tui/qa/tui_qa.py --stub --json
```

Exit code is non-zero if any scenario fails, so CI/release can gate on it.

## Interactive

Because it is a real tmux session, the run is watchable and attachable:

```bash
python3 tui/qa/tui_qa.py --stub --watch   # slow; prints a screen snapshot after each step
python3 tui/qa/tui_qa.py --stub --keep    # leave the session alive at the end
tmux attach -t forge-qa                    # (in another terminal) watch keystrokes land live
```

## Scenarios

| Scenario | Asserts |
|---|---|
| `launch_and_health` | status bar shows the gateway `healthy` + the `chat · runs · new · twin` bar |
| `chat_greeting_streams_reply` | a greeting yields a non-empty streamed reply (`chars>0, reason=null`) and no `(no reply)` on screen |
| `chat_second_turn_answers` | a second turn also answers — the SSE stream stays open across turns |
| `empty_turn_shows_cause` *(stub only)* | a deliberately malformed SSE stream surfaces the cause — `(no reply — …parse mismatch)` — instead of a silent blank (guards the #464 regression class deterministically) |
| `pane_navigation` | `C-r`→runs, `C-b`→twin, `C-t`→back to chat, all without the app dying |
| `quit_on_esc` | `Escape` ends the session cleanly |

## The stub gateway

`stub_gateway.py` implements only the endpoints the TUI touches (`/health`,
`/v1/projects`, `/v1/runs`, `/v1/twin/nodes`, and the chat thread/stream). Its
stream emits scripted enveloped SSE — normal deltas, or (on the `__malformed__`
sentinel) enveloped `message.delta` frames with **no `delta` field**, which
reproduce the exact "deltas arrived but 0 characters" payload/parse mismatch the
`(no reply)` bug was made of. Run it standalone with `python3 qa/stub_gateway.py`.
