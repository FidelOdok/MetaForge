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
