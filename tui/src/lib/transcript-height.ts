/**
 * Transcript height estimation (MET-607) — pure, unit-tested.
 *
 * Ink's <Static> commits finished turns to the terminal's scrollback, outside
 * the layout tree, so Ink cannot measure them — yet pinning the input to the
 * bottom of the screen needs exactly that height. This mirrors the Turn
 * renderer's chrome (margins, headers, step rows, wrap width) closely enough
 * to size a bottom-pin spacer; the consumer clamps at 0, so estimation drift
 * is cosmetic (a slightly short gap), never an overflow, and the estimate
 * stops mattering entirely once the transcript exceeds one screen.
 */

import type { ChatMessage } from "../hooks/useChat.js";

/** Rendered rows for `text` at `width` columns (newline-aware, min 1/line). */
export function wrappedLines(text: string, width: number): number {
  const w = Math.max(1, width);
  return text
    .split("\n")
    .reduce((n, line) => n + Math.max(1, Math.ceil(line.length / w)), 0);
}

/** Rows one finalized message occupies, mirroring <Turn>'s chrome. */
export function messageHeight(m: ChatMessage, cols: number): number {
  const wrapW = Math.max(1, cols - 2); // Turn Box has paddingX={1}
  if (m.role === "system") return 1 + wrappedLines(m.text, wrapW); // marginTop + text
  if (m.role === "user") return 1 + 1 + wrappedLines(m.text, wrapW); // margin + "❯ you"
  // assistant: margin + optional steps block + "◆ assistant" + text/no-reply
  let rows = 1 + 1;
  const steps = m.steps ?? [];
  if (steps.length) {
    rows += 1; // "· thinking"
    for (let i = 0; i < steps.length; i++) {
      // Step lines are truncated by StepTrace (~110 chars worst case) but can
      // still wrap on narrow terminals.
      rows += Math.max(1, Math.ceil(110 / Math.max(1, wrapW - 1)));
    }
  }
  rows += m.text ? wrappedLines(m.text, wrapW) : 1; // "(no reply — …)"
  return rows;
}

export function transcriptHeight(messages: ChatMessage[], cols: number): number {
  return messages.reduce((n, m) => n + messageHeight(m, cols), 0);
}

/** Shape of `useChat`'s `pending` (kept local so this stays a pure, dependency-free lib). */
export interface PendingLike {
  text: string;
  steps: { tool?: string; thought?: string }[];
  thinking?: string;
  startedAction?: string;
}

/**
 * Rows the live in-flight turn occupies right now, mirroring <Chat>'s live
 * region JSX (MET-641): the "thinking" block (steps + thinking preview +
 * started-action line) plus either the streamed answer or the idle spinner.
 *
 * Without this, the bottom-pin spacer in the transcript layout is sized only
 * from *finalized* messages, so as a turn's tool trace grows the live region
 * silently outgrows its reserved space — the frame's total height jumps at
 * the moment growing content first exceeds the stale reservation. Feeding
 * this estimate into that calculation keeps the reservation shrinking in
 * step with `pending`'s growth instead.
 *
 * MET-617 bounds what the live region actually renders to a viewport-sized
 * tail (see `lib/live-tail.ts`) — callers pass the already-clipped `steps`/
 * `text` (what's really on screen), not the raw unbounded turn, and
 * `extraTraceLines` for MET-617's own "… N earlier steps" marker row so this
 * stays an exact match for what's rendered instead of a stale overestimate.
 */
export function pendingHeight(
  pending: PendingLike | null,
  cols: number,
  busy: boolean,
  extraTraceLines = 0,
): number {
  if (!pending) return 0;
  const wrapW = Math.max(1, cols - 2); // Box has paddingX={1}
  let rows = 1; // marginTop
  const hasTrace = pending.steps.length > 0 || !!pending.thinking || !!pending.startedAction;
  if (hasTrace) {
    rows += 1; // "· thinking [~N tok]"
    rows += extraTraceLines;
    for (let i = 0; i < pending.steps.length; i++) {
      rows += Math.max(1, Math.ceil(110 / Math.max(1, wrapW - 1))); // marginLeft={1}
    }
    if (pending.thinking) rows += wrappedLines(pending.thinking.slice(-600), wrapW - 1);
    if (pending.startedAction) rows += 1;
  }
  if (pending.text) {
    rows += 1; // "◆ assistant"
    rows += wrappedLines(pending.text, wrapW);
  } else if (busy) {
    rows += 1; // spinner line
  }
  return rows;
}
