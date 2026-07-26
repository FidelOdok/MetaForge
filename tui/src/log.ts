/**
 * Minimal file logger for the unified `forge` CLI/TUI.
 *
 * An Ink app owns the whole terminal, so logging to stdout/stderr would corrupt
 * the UI — there is nowhere on-screen to put diagnostics. Instead we append
 * JSONL to a file (`~/.forge/logs/session.log`) that outlives the session, so
 * "what went wrong" is answerable after the fact with `tail`/`jq`.
 *
 * info/warn/error always write (a few lines per session, cheap). `debug` — raw
 * SSE frames and other verbose traffic — is gated behind `FORGE_LOG=1` (or
 * `--debug`) so normal runs stay quiet. Logging never throws: a failed write is
 * swallowed rather than risk breaking the UI.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const TRUTHY = new Set(["1", "true", "on", "yes"]);

/** Verbose (raw-SSE) logging: `FORGE_LOG=1` or `--debug` anywhere in argv. */
export const DEBUG =
  TRUTHY.has((process.env.FORGE_LOG ?? "").trim().toLowerCase()) ||
  process.argv.includes("--debug");

/** Where session logs land. Override with `FORGE_LOG_FILE`. */
export const LOG_PATH =
  process.env.FORGE_LOG_FILE ?? join(homedir(), ".forge", "logs", "session.log");

let ready = false;
function ensureDir(): boolean {
  if (ready) return true;
  try {
    mkdirSync(dirname(LOG_PATH), { recursive: true });
    ready = true;
  } catch {
    ready = false;
  }
  return ready;
}

function write(level: string, msg: string, fields?: Record<string, unknown>): void {
  if (!ensureDir()) return;
  const rec = { ts: new Date().toISOString(), level, msg, ...(fields ?? {}) };
  try {
    appendFileSync(LOG_PATH, `${JSON.stringify(rec)}\n`);
  } catch {
    // never let logging break the UI
  }
}

export const log = {
  path: LOG_PATH,
  debugEnabled: DEBUG,
  info: (msg: string, f?: Record<string, unknown>) => write("info", msg, f),
  warn: (msg: string, f?: Record<string, unknown>) => write("warn", msg, f),
  error: (msg: string, f?: Record<string, unknown>) => write("error", msg, f),
  debug: (msg: string, f?: Record<string, unknown>) => {
    if (DEBUG) write("debug", msg, f);
  },
};
