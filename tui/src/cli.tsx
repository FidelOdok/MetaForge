#!/usr/bin/env node
import { render } from "ink";
import { App } from "./App.js";
import { runCommand } from "./commands.js";
import { decideInvocation } from "./lib/invocation.js";

/**
 * Unified `forge` entrypoint. Mode is chosen by invocation (see
 * lib/invocation.ts):
 *   - bare (or `ui`), optionally with --project/--debug + a TTY → the Ink TUI
 *   - a subcommand / --help / any other flag → run it non-interactively
 *   - bare + piped (no TTY) → print a hint (can't render a UI without a TTY)
 */
const rawArgv = process.argv.slice(2);
const { mode, initialProject, debug } = decideInvocation(rawArgv);
// `--debug` is a global flag, not a subcommand; log.ts reads it straight off
// argv, so all that's needed here is the env default.
if (debug) process.env.FORGE_LOG ??= "1";
const argv = rawArgv.filter((a) => a !== "--debug");

if (mode === "version") {
  void runCommand(["version"]).then((code) => process.exit(code));
} else if (mode === "tui") {
  if (process.stdout.isTTY && process.stdin.isTTY) {
    render(<App initialProject={initialProject} />);
  } else {
    process.stderr.write("forge: no TTY — the interactive UI needs a terminal.\n");
    process.stderr.write("Use a command instead, e.g. `forge runs list` or `forge --help`.\n");
    process.exit(0);
  }
} else {
  void runCommand(argv).then((code) => process.exit(code));
}
