#!/usr/bin/env node
import { render } from "ink";
import { App } from "./App.js";
import { runCommand } from "./commands.js";

/**
 * Unified `forge` entrypoint. Mode is chosen by invocation:
 *   - bare + a TTY            → launch the interactive Ink TUI
 *   - a subcommand / --help   → run it non-interactively (print + exit)
 *   - bare + piped (no TTY)   → print a hint (can't render a UI without a TTY)
 */
// `--debug` (verbose file logging) is a global flag, not a subcommand — pull it
// out before mode detection so `forge --debug` still launches the TUI. log.ts
// reads it straight off argv, so no wiring beyond keeping it out of the way.
const rawArgv = process.argv.slice(2);
if (rawArgv.includes("--debug")) process.env.FORGE_LOG ??= "1";
const argv = rawArgv.filter((a) => a !== "--debug");
const first = argv[0];

const wantsTui = first === undefined || first === "ui";
const wantsVersion = first === "--version" || first === "-v";

if (wantsVersion) {
  void runCommand(["version"]).then((code) => process.exit(code));
} else if (wantsTui) {
  if (process.stdout.isTTY && process.stdin.isTTY) {
    render(<App />);
  } else {
    process.stderr.write("forge: no TTY — the interactive UI needs a terminal.\n");
    process.stderr.write("Use a command instead, e.g. `forge runs list` or `forge --help`.\n");
    process.exit(0);
  }
} else {
  void runCommand(argv).then((code) => process.exit(code));
}
