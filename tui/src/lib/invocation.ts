/**
 * How a `forge` invocation is routed: interactive workspace, one command, or
 * `--version`.
 *
 * Extracted from cli.tsx so it can be tested — the failure mode here is silent
 * (`forge --help` rendering a UI instead of printing help, or a flag the TUI
 * ignores looking like it was honoured), and the parse is easy to break while
 * adding a flag.
 */
import { parseArgs } from "../commands.js";

/** Flags the interactive workspace understands. Anything else is a command. */
const TUI_FLAGS = new Set(["project", "p", "debug"]);

export interface Invocation {
  mode: "tui" | "command" | "version";
  /** `--project <id|name>` for the workspace (tui mode only). */
  initialProject?: string;
  /** Verbose file logging was requested (`--debug`). */
  debug: boolean;
}

export function decideInvocation(argv: string[]): Invocation {
  const debug = argv.includes("--debug");
  const rest = argv.filter((a) => a !== "--debug");
  if (rest[0] === "--version" || rest[0] === "-v") return { mode: "version", debug };

  // Parse with the command parser so a flag *value* is never mistaken for a
  // subcommand: `forge --project "Monitor Build"` has no positional at all.
  const { _, flags } = parseArgs(rest);
  const positional = _[0];
  const foreignFlag = Object.keys(flags).some((k) => !TUI_FLAGS.has(k));
  // A flag the workspace can't act on (`--help`, `--gateway`, …) routes to the
  // command layer rather than launching a UI that would quietly drop it.
  if (foreignFlag) return { mode: "command", debug };
  if (positional !== undefined && positional !== "ui") return { mode: "command", debug };

  const project = flags.project ?? flags.p;
  return {
    mode: "tui",
    debug,
    ...(typeof project === "string" ? { initialProject: project } : {}),
  };
}
