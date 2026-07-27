/**
 * Persistent prompt history for the chat input — shell-style recall across
 * sessions. Stored one entry per line at ~/.forge/history (override with
 * FORGE_HISTORY_FILE). Entries are single-line (the input is single-line), so
 * a newline-delimited file is safe. Best-effort: any I/O error is swallowed so
 * history never breaks the UI.
 */
import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const MAX = 500;

export const HISTORY_PATH =
  process.env.FORGE_HISTORY_FILE ?? join(homedir(), ".forge", "history");

/** Most-recent-last list of prior prompts (capped, blanks removed). */
export function loadHistory(): string[] {
  try {
    return readFileSync(HISTORY_PATH, "utf8")
      .split("\n")
      .map((s) => s.trimEnd())
      .filter(Boolean)
      .slice(-MAX);
  } catch {
    return [];
  }
}

/** Append one submitted prompt (skips blanks). */
export function appendHistory(entry: string): void {
  const e = entry.trim();
  if (!e) return;
  try {
    mkdirSync(dirname(HISTORY_PATH), { recursive: true });
    appendFileSync(HISTORY_PATH, `${e}\n`);
  } catch {
    // never let history break the UI
  }
}
