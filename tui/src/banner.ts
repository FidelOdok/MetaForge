/**
 * Shared brand + onboarding copy for the `forge` CLI/TUI.
 *
 * One source of truth so the interactive welcome screen and the `--help` header
 * stay in sync. `WORDMARK`/`TAGLINE`/`MISSION` are rendered with colour by the
 * Ink `<Welcome/>` component and as plain text by the CLI usage.
 */

/** "FORGE" — ANSI Shadow. ~42 cols; the TUI falls back to a compact title below that. */
export const WORDMARK = [
  "███████╗ ██████╗ ██████╗  ██████╗ ███████╗",
  "██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝",
  "█████╗  ██║   ██║██████╔╝██║  ███╗█████╗  ",
  "██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  ",
  "██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗",
  "╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝",
];

export const TAGLINE = "MetaForge · intent → manufacturable hardware";

/** What MetaForge is, in a few wrapped lines (~74 cols). */
export const MISSION = [
  "A local-first control plane that turns human intent into reviewable,",
  "manufacturable hardware. Specialist AI agents drive real engineering tools",
  "(KiCad, FreeCAD, CalculiX, SPICE) to produce schematics, BOMs, CAD/FEA,",
  "firmware and manufacturing files — every artifact versioned in a Digital Twin.",
  "If it can't be versioned, reviewed, and built, MetaForge doesn't output it.",
];

/** Command → description, for the `--help` getting-started block. */
export const CLI_QUICKSTART: [string, string][] = [
  ["forge", "open the interactive workspace (chat · runs · twin)"],
  ['forge chat -m "…"', "one-shot question to the assistant"],
  ["forge runs list", "design-flow runs and their gates"],
  ["forge projects", "list your projects"],
  ["forge config set gateway_url <url>", "point at your gateway"],
];

/** Key/action hints shown in the interactive workspace. */
export const TUI_HINTS: [string, string][] = [
  ["type a message", "ask the assistant — it can call tools"],
  ["^R  ^B  ^N  ^T", "runs · twin · new run · chat"],
  ["/model <slug>", "switch model    ·    Esc  quit"],
];

/** Plain-text banner (wordmark + tagline) for non-Ink surfaces like `--help`. */
export function plainBanner(): string {
  return [...WORDMARK, "", TAGLINE].join("\n");
}
