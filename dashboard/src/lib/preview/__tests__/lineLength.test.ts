import { describe, expect, it } from 'vitest';
import { definition } from './fixtures';
import { boxDefinition } from './boxFixture';
import { NO_LENGTH_EDITS, applyLength, geometryPayload, solveLength, withSolvedElements } from '../lineLength';
import { buildPreview } from '../buildPreview';
import { withOverrides } from '../definition';

const len = (p: { start: number[]; end: number[] }) => Math.hypot(p.end[0]! - p.start[0]!, p.end[1]! - p.start[1]!);

describe('line-length re-solve', () => {
  it('re-solves the accepted rules: the joined lines follow, every rule still holds', () => {
    const def = boxDefinition();
    const r = solveLength(def, 'L1', 50);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const top = r.solved.top as { start: number[]; end: number[] };
    expect(len(top)).toBeCloseTo(50, 5);
    // The right side moved with the top's end and stayed vertical, 20 long.
    const right = r.solved.right as { start: number[]; end: number[] };
    expect(right.start[0]).toBeCloseTo(top.end[0]!, 5);
    expect(right.end[0]).toBeCloseTo(right.start[0]!, 5);
    expect(len(right)).toBeCloseTo(20, 5);
  });

  it('draws the preview from the solved geometry, on the plane (v up)', () => {
    const def = boxDefinition();
    const edits = applyLength(def, NO_LENGTH_EDITS, 'L1', 50);
    const drawn = withOverrides(def, { length: edits.length, solved: edits.solved });
    const b = buildPreview(drawn);
    expect(b.box.max[0] - b.box.min[0]).toBeCloseTo(50, 4);
    expect(b.box.max[1] - b.box.min[1]).toBeCloseTo(20, 4);
    // The handle follows the line: it sits on the middle of the solved top line.
    const h = b.handles.find((x) => x.kind === 'length' && x.constraintId === 'L1')!;
    expect(h.value).toBeCloseTo(50, 5);
    expect(withSolvedElements(def.elements, {})).toBe(def.elements);
  });

  it('sends only the geometric fields of the moved elements', () => {
    const def = boxDefinition();
    const edits = applyLength(def, NO_LENGTH_EDITS, 'L1', 46);
    const payload = geometryPayload(edits.solved);
    expect(Object.keys(payload).sort()).toEqual(expect.arrayContaining(['top']));
    for (const fields of Object.values(payload)) expect(Object.keys(fields).sort()).toEqual(['end', 'start']);
  });

  it('refuses a length the other accepted rules cannot hold, in plain words, keeping the last good geometry', () => {
    const def = boxDefinition({ bottomLength: 40 });
    const good = applyLength(def, NO_LENGTH_EDITS, 'L1', 40);
    expect(good.conflict).toBeNull();
    const bad = applyLength(def, good, 'L1', 50);
    expect(bad.conflict).toMatch(/^A length of 50 mm cannot hold with the other accepted rules\. /);
    expect(bad.conflict).toMatch(/conflict/);
    expect(bad.conflict).toMatch(/Nothing was changed\.$/);
    expect(bad.solved).toBe(good.solved);
    expect(bad.length).toBe(good.length);
  });

  it('refuses when there is no sketch to solve, or a length at or below zero', () => {
    expect(solveLength(definition(), 'L1', 5).ok).toBe(false);
    expect(solveLength(boxDefinition(), 'L1', 0).ok).toBe(false);
  });
});
