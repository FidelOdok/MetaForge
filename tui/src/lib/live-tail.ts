/**
 * Live-region tail clipping — pure, unit-tested.
 *
 * Ink repaints the dynamic (non-<Static>) frame in place, which only works
 * while that frame fits the terminal: once it is taller than the viewport Ink
 * cannot erase the previous paint, and every repaint strands a copy of the
 * overflow into scrollback — the "glitching while thinking" bug. The in-flight
 * turn (streamed answer + step trace) is the only unbounded dynamic content,
 * so the live region must render just a viewport-sized TAIL of it; the full
 * text and full step trace land in <Static> scrollback when the turn
 * finalizes, so nothing is lost.
 */

/** Trailing portion of `text` that renders within `maxRows` wrapped rows at
 * `width` columns. Newline-aware; wrapping is approximated by character count
 * (same approach as transcript-height, so drift is a row or two, absorbed by
 * the caller's chrome margin). A clipped result starts with "…". */
export function tailLines(text: string, width: number, maxRows: number): string {
  if (!text) return text;
  const w = Math.max(1, width);
  const budget = Math.max(1, maxRows);
  const lines = text.split("\n");
  const out: string[] = [];
  let used = 0;
  let i = lines.length - 1;
  for (; i >= 0; i--) {
    const line = lines[i];
    const need = Math.max(1, Math.ceil(line.length / w));
    if (used + need > budget) {
      // Partial line: keep only its trailing rows, "…"-prefixed.
      const keep = Math.max(0, (budget - used) * w - 1);
      if (keep > 0 || used === 0) out.unshift(`…${line.slice(-keep)}`);
      else out[0] = `…${(out[0] ?? "").replace(/^…/, "")}`;
      return out.join("\n");
    }
    out.unshift(line);
    used += need;
  }
  return out.join("\n");
}

/** How many rows one step line occupies at `cols` terminal columns. StepTrace
 * truncates a step to ~110 chars, indented by 1 inside a paddingX={1} box. */
export function stepRows(cols: number): number {
  return Math.max(1, Math.ceil(110 / Math.max(1, cols - 3)));
}
