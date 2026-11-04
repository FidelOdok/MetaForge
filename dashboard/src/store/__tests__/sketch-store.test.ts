import { beforeEach, describe, expect, it } from 'vitest';
import { readDraft, useSketchStore, type SketchStroke } from '../sketch-store';
import { arcShape, circleShape, toScreen, toSketch, viewBoxFor } from '../../components/sketch/geometry';

const P = 'proj-sketch';

function add(points: [number, number][] = [[0, 0], [0.1, 0.1]]): SketchStroke {
  return useSketchStore.getState().addStroke(P, { tool: 'pen', points });
}

const doc = () => useSketchStore.getState().doc(P);

beforeEach(() => {
  window.localStorage.clear();
  useSketchStore.getState().reset();
});

describe('sketch store', () => {
  it('adds strokes with stable unique ids and the honesty fields', () => {
    const a = add();
    const b = add([[0.2, 0.2]]);
    expect(a.id).not.toEqual(b.id);
    expect(a.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(doc().strokes.map((s) => s.id)).toEqual([a.id, b.id]);
    // The ids do not change when later strokes are added or undone.
    useSketchStore.getState().undo(P);
    expect(doc().strokes.map((s) => s.id)).toEqual([a.id]);
    expect(a.source).toBe('human');
    expect(typeof a.created_at).toBe('string');
    expect(doc().plane).toEqual({ state: 'UNASSIGNED' });
    expect(doc().scale).toBe('unknown');
    expect(doc().space).toBe('sketch_unit');
  });

  it('keeps pressure only when there is one value per point', () => {
    const s = useSketchStore.getState();
    const withPressure = s.addStroke(P, { tool: 'pen', points: [[0, 0], [1, 1]], pressure: [0.2, 0.8] });
    const mismatched = s.addStroke(P, { tool: 'pen', points: [[0, 0], [1, 1]], pressure: [0.2] });
    expect(withPressure.pressure).toEqual([0.2, 0.8]);
    expect(mismatched.pressure).toBeUndefined();
  });

  it('erases, undoes and redoes', () => {
    const a = add();
    const b = add();
    const s = useSketchStore.getState();
    s.erase(P, [a.id]);
    expect(doc().strokes.map((x) => x.id)).toEqual([b.id]);
    s.undo(P);
    expect(doc().strokes.map((x) => x.id)).toEqual([a.id, b.id]);
    s.redo(P);
    expect(doc().strokes.map((x) => x.id)).toEqual([b.id]);
    s.undo(P);
    s.undo(P);
    s.undo(P);
    expect(doc().strokes).toEqual([]);
    expect(doc().undo).toHaveLength(0);
    // Undo with nothing left does nothing.
    s.undo(P);
    expect(doc().redo).toHaveLength(3);
    // A new edit clears redo.
    add();
    expect(doc().redo).toHaveLength(0);
  });

  it('erasing nothing is not an edit', () => {
    add();
    const before = doc().undo.length;
    useSketchStore.getState().erase(P, ['nope']);
    expect(doc().undo.length).toBe(before);
  });

  it('selects and deletes selected strokes, dropping erased ids from the selection', () => {
    const a = add();
    const b = add();
    const s = useSketchStore.getState();
    s.select(P, [a.id, b.id]);
    s.erase(P, [a.id]);
    expect(doc().selection).toEqual([b.id]);
    s.deleteSelected(P);
    expect(doc().strokes).toEqual([]);
  });

  it('restores the draft after a reload', () => {
    const a = add([[0.25, -0.25], [0.3, -0.2]]);
    useSketchStore.getState().setReference(P, 'node-ref');
    // A reload: the in-memory store is gone, localStorage is not.
    useSketchStore.getState().reset();
    const restored = doc();
    expect(restored.strokes.map((s) => s.id)).toEqual([a.id]);
    expect(restored.strokes[0]!.points).toEqual([[0.25, -0.25], [0.3, -0.2]]);
    expect(restored.referenceNodeId).toBe('node-ref');
    expect(restored.pendingSaveId).toBeTruthy();
    expect(readDraft('other-project')).toBeNull();
  });

  it('ignores a corrupt draft', () => {
    window.localStorage.setItem(`metaforge.sketch.v1.${P}`, '{not json');
    expect(doc().strokes).toEqual([]);
    window.localStorage.setItem(`metaforge.sketch.v1.${P}`, JSON.stringify({ strokes: [{ id: 1 }, { id: 'ok', tool: 'pen', points: [[0, 0]] }] }));
    useSketchStore.getState().reset();
    expect(doc().strokes.map((s) => s.id)).toEqual(['ok']);
  });

  it('keeps a save id until that save lands, and a new one for edits made meanwhile', () => {
    add();
    const s = useSketchStore.getState();
    const first = s.markSaving(P);
    expect(first).toBeTruthy();
    // Saving again before anything changes reuses nothing new: the same save is in flight.
    expect(doc().inflightSaveId).toBe(first);
    add(); // drawn while the save is on its way
    expect(doc().pendingSaveId).not.toBe(first);
    s.markSaved(P, first!, { itemKey: 'SKT-SKETCH', itemRef: 'SKT-SKETCH@1', nodeId: 'n1' });
    expect(doc().savedRevision).toBe('SKT-SKETCH@1');
    expect(doc().pendingSaveId).toBeTruthy(); // the later edit still needs saving
    const second = s.markSaving(P);
    s.markSaved(P, second!, { itemKey: 'SKT-SKETCH', itemRef: 'SKT-SKETCH@2', nodeId: 'n2' });
    expect(doc().pendingSaveId).toBeNull();
    expect(doc().saveState).toBe('saved');
    expect(s.markSaving(P)).toBeNull();
  });

  it('retries a failed save with the same save id', () => {
    add();
    const s = useSketchStore.getState();
    const id = s.markSaving(P);
    s.markFailed(P, id!, 'gateway down');
    expect(doc().saveState).toBe('failed');
    expect(s.markSaving(P)).toBe(id);
  });

  it('adopts the gateway copy only when this browser has nothing of its own', () => {
    const remote = {
      strokes: [{ id: 'r1', tool: 'pen' as const, points: [[0, 0]] as [number, number][], width: 2, created_at: 'x', source: 'human' as const }],
      itemKey: 'SKT-SKETCH',
      itemRef: 'SKT-SKETCH@4',
      nodeId: 'n4',
      createdAt: null,
    };
    useSketchStore.getState().loadRemote(P, remote);
    expect(doc().strokes.map((s) => s.id)).toEqual(['r1']);
    expect(doc().savedRevision).toBe('SKT-SKETCH@4');
    expect(doc().pendingSaveId).toBeNull();

    useSketchStore.getState().reset();
    window.localStorage.clear();
    add();
    useSketchStore.getState().loadRemote(P, remote);
    expect(doc().strokes.map((s) => s.id)).not.toContain('r1');
  });
});

describe('sketch space', () => {
  it('is independent of the viewport size', () => {
    // The same relative position on a large and a small viewport.
    expect(toSketch(600, 150, { width: 800, height: 600 })).toEqual(toSketch(300, 75, { width: 400, height: 300 }));
    // The centre is the origin and the shorter side spans one unit.
    expect(toSketch(400, 300, { width: 800, height: 600 })).toEqual([0, 0]);
    expect(toSketch(400, 0, { width: 800, height: 600 })).toEqual([0, -0.5]);
    // A wider window does not stretch the drawing.
    const p = toSketch(500, 300, { width: 1000, height: 600 });
    expect(toScreen(p, { width: 1000, height: 600 })).toEqual([500, 300]);
    expect(viewBoxFor({ width: 800, height: 400 })).toBe('-1 -0.5 2 1');
  });

  it('builds closed circles and arcs through the drag', () => {
    const c = circleShape([0, 0], [0.1, 0]);
    expect(c[0]![0]).toBeCloseTo(0.1);
    expect(c[c.length - 1]![0]).toBeCloseTo(0.1);
    const arc = arcShape([[-0.1, 0], [0, -0.1], [0.1, 0]]);
    expect(arc[0]).toEqual([-0.1, 0]);
    expect(arc[arc.length - 1]![0]).toBeCloseTo(0.1);
    // Passes through the top of the drag, not the bottom.
    expect(Math.min(...arc.map((p) => p[1]))).toBeCloseTo(-0.1);
    // A straight drag is a line.
    expect(arcShape([[0, 0], [0.05, 0], [0.1, 0]])).toHaveLength(2);
  });
});
