/**
 * Strategy A (spec section 8): a drag handle edits one known parameter
 * along one axis. The preview follows every move; the parameter is
 * committed once, on release, through the normal parametric path. The mesh
 * is never what gets stored (the section 8 safety rule).
 *
 * Pure, so the commit-once rule is tested without a WebGL canvas.
 */

export interface AxisDragOptions {
  /** The parameter's value when the drag started. */
  startValue: number;
  /** Screen pixels the handle moves per unit of the value. */
  pxPerUnit: number;
  /** The handle axis's direction on screen (pixels, y down), any length. */
  screenDir: [number, number];
  /** Smallest value allowed (a depth or radius stays above zero). */
  min?: number;
  max?: number;
  /** Values snap to this step (0.1 mm by default). */
  step?: number;
  onChange: (value: number) => void;
  onCommit: (value: number) => void;
}

export interface AxisDrag {
  /** The pointer moved by `(dx, dy)` pixels since the drag started. Returns the value. */
  move(dx: number, dy: number): number;
  /** The pointer was released: commits once, and only if the value changed. */
  release(): void;
  /** Escape: back to the start value, nothing committed. */
  cancel(): void;
  readonly value: number;
  readonly done: boolean;
}

export function createAxisDrag(o: AxisDragOptions): AxisDrag {
  const len = Math.hypot(o.screenDir[0], o.screenDir[1]) || 1;
  const dir: [number, number] = [o.screenDir[0] / len, o.screenDir[1] / len];
  const ppu = o.pxPerUnit > 1e-9 ? o.pxPerUnit : 1;
  const min = o.min ?? 0.01;
  const max = o.max ?? Number.POSITIVE_INFINITY;
  const step = o.step ?? 0.1;
  let value = o.startValue;
  let done = false;
  return {
    move(dx, dy) {
      if (done) return value;
      const along = dx * dir[0] + dy * dir[1];
      const raw = o.startValue + along / ppu;
      const snapped = step > 0 ? Math.round(raw / step) * step : raw;
      const next = Math.min(max, Math.max(min, Number(snapped.toFixed(6))));
      if (next !== value) {
        value = next;
        o.onChange(value);
      }
      return value;
    },
    release() {
      if (done) return;
      done = true;
      if (Math.abs(value - o.startValue) > 1e-9) o.onCommit(value);
    },
    cancel() {
      if (done) return;
      done = true;
      value = o.startValue;
      o.onChange(value);
    },
    get value() {
      return value;
    },
    get done() {
      return done;
    },
  };
}
