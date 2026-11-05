import { describe, expect, it, vi } from 'vitest';
import { createAxisDrag } from '../dragAxis';

/** Strategy A: the preview follows the drag; the parameter is committed once, on release. */
describe('createAxisDrag', () => {
  it('follows the pointer along the handle axis and commits once on release', () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    // The axis points straight up the screen, 4 px per millimetre.
    const drag = createAxisDrag({ startValue: 5, pxPerUnit: 4, screenDir: [0, -10], onChange, onCommit });
    expect(drag.move(0, -8)).toBeCloseTo(7);
    expect(drag.move(3, -12)).toBeCloseTo(8); // sideways motion is ignored
    drag.move(0, -20);
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(onCommit).not.toHaveBeenCalled();
    drag.release();
    drag.release();
    drag.move(0, -40);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(10);
    expect(drag.done).toBe(true);
  });

  it('commits nothing when the value did not change, and never goes to zero', () => {
    const onCommit = vi.fn();
    const drag = createAxisDrag({ startValue: 2, pxPerUnit: 1, screenDir: [1, 0], onChange: vi.fn(), onCommit, min: 0.5 });
    expect(drag.move(-100, 0)).toBe(0.5);
    drag.move(0, 0);
    drag.release();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('Escape puts the start value back and commits nothing', () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    const drag = createAxisDrag({ startValue: 3, pxPerUnit: 2, screenDir: [1, 0], onChange, onCommit });
    drag.move(10, 0);
    drag.cancel();
    drag.release();
    expect(onChange).toHaveBeenLastCalledWith(3);
    expect(onCommit).not.toHaveBeenCalled();
  });
});
