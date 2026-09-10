/**
 * Selection-following list window (MET-606) — pure, unit-tested.
 *
 * Keeps the selected row visible inside a fixed-size window over a longer
 * list: the window scrolls only when the selection would leave it, so
 * neighbouring context stays stable while arrowing through.
 */

export interface Window {
  start: number; // inclusive
  end: number; // exclusive
}

export function visibleWindow(total: number, sel: number, pageSize: number, prevStart = 0): Window {
  const size = Math.max(1, pageSize);
  if (total <= size) return { start: 0, end: total };
  let start = Math.min(Math.max(0, prevStart), total - size);
  if (sel < start) start = sel;
  else if (sel >= start + size) start = sel - size + 1;
  return { start, end: start + size };
}

/** "runs 3–12 of 87" header fragment (1-based, only when windowed). */
export function windowLabel(kind: string, w: Window, total: number): string {
  if (total <= w.end - w.start) return `${kind} (${total})`;
  return `${kind} ${w.start + 1}–${w.end} of ${total}`;
}

/** PageUp/PageDown selection jumps, clamped. */
export function pageJump(sel: number, total: number, pageSize: number, dir: 1 | -1): number {
  return Math.min(Math.max(0, sel + dir * Math.max(1, pageSize)), Math.max(0, total - 1));
}
