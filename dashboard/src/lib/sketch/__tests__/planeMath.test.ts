import { describe, expect, it } from 'vitest';
import {
  cadToViewer,
  cross,
  displayFrame,
  displayScale,
  frameMatrix,
  intersectRayPlane,
  isValidFrame,
  localToWorld,
  placeStrokes,
  principalFrame,
  reassignPoints,
  screenToleranceToLocal,
  sketchToLocal,
  snapPoint,
  strokeEndpoints,
  viewHintFromDirection,
  viewerToCad,
  worldPerPixel,
  worldToLocal,
  type Principal,
  type Vec3,
} from '../planeMath';

const near = (a: readonly number[], b: readonly number[], eps = 1e-9) =>
  a.forEach((v, i) => expect(Math.abs(v - b[i]!)).toBeLessThan(eps));

describe('principal frames', () => {
  it.each(['XY', 'XZ', 'YZ'] as Principal[])('%s is orthonormal and right-handed', (p) => {
    const f = principalFrame(p);
    expect(isValidFrame(f)).toBe(true);
    near(cross(f.u, f.v), f.normal);
  });

  it('follows Front = XZ (normal -Y), Side = YZ (+X), Top = XY (+Z)', () => {
    expect(principalFrame('XZ').normal).toEqual([0, -1, 0]);
    expect(principalFrame('XZ').v).toEqual([0, 0, 1]);
    expect(principalFrame('YZ').normal).toEqual([1, 0, 0]);
    expect(principalFrame('XY').normal).toEqual([0, 0, 1]);
  });

  it('builds the same row-major transform as the gateway', () => {
    expect(frameMatrix(principalFrame('YZ', [5, 6, 7]))).toEqual([
      [0, 0, 1, 5],
      [1, 0, 0, 6],
      [0, 1, 0, 7],
      [0, 0, 0, 1],
    ]);
  });

  it('rejects a left-handed frame', () => {
    const f = principalFrame('XY');
    expect(isValidFrame({ ...f, normal: [0, 0, -1] })).toBe(false);
  });
});

describe('world and local', () => {
  it('round-trips through a frame', () => {
    const f = principalFrame('XZ', [1, 2, 3]);
    const w = localToWorld(0.5, 0.25, f);
    near(w, [1.5, 2, 3.25]);
    near(worldToLocal(w, f), [0.5, 0.25, 0]);
  });

  it('turns sketch space (y down) into local (v up)', () => {
    expect(sketchToLocal([0.2, 0.3])).toEqual([0.2, -0.3]);
  });

  it('maps CAD Z-up to the Y-up viewer and back', () => {
    const p: Vec3 = [1, 2, 3];
    expect(cadToViewer(p)).toEqual([1, 3, -2]);
    expect(viewerToCad(cadToViewer(p))).toEqual(p);
  });
});

describe('ray-plane intersection', () => {
  const top = principalFrame('XY');

  it('returns local (u, v) for a ray that hits the plane', () => {
    const hit = intersectRayPlane([0.2, 0.3, 10], [0, 0, -1], top);
    expect(hit.ok).toBe(true);
    if (hit.ok) {
      expect(hit.u).toBeCloseTo(0.2);
      expect(hit.v).toBeCloseTo(0.3);
      expect(hit.t).toBeCloseTo(10);
    }
  });

  it('reports a ray parallel to the plane', () => {
    expect(intersectRayPlane([0, 0, 1], [1, 0, 0], top)).toEqual({ ok: false, reason: 'parallel' });
  });

  it('reports a near-parallel ray below the threshold, and accepts one just above it', () => {
    expect(intersectRayPlane([0, 0, 1], [1, 0, -1e-8], top)).toEqual({ ok: false, reason: 'parallel' });
    const grazing = intersectRayPlane([0, 0, 1], [1, 0, -1e-3], top);
    expect(grazing.ok).toBe(true);
  });

  it('reports a plane behind the camera', () => {
    expect(intersectRayPlane([0, 0, 10], [0, 0, 1], top)).toEqual({ ok: false, reason: 'behind_camera' });
  });

  it('reports a miss for a degenerate ray or a hit beyond the far distance', () => {
    expect(intersectRayPlane([0, 0, 1], [0, 0, 0], top)).toEqual({ ok: false, reason: 'miss' });
    expect(intersectRayPlane([0, 0, 1e6], [0, 0, -1], top, { maxDistance: 1000 })).toEqual({ ok: false, reason: 'miss' });
  });

  it('works on a tilted Front plane with an offset origin', () => {
    const front = principalFrame('XZ', [0, 5, 0]);
    const hit = intersectRayPlane([0.1, -20, 0.4], [0, 1, 0], front);
    expect(hit.ok && hit.u).toBeCloseTo(0.1);
    expect(hit.ok && hit.v).toBeCloseTo(0.4);
  });

  it('projects well inside the 16 ms budget (micro-benchmark)', () => {
    const f = principalFrame('XZ', [1, 2, 3]);
    const n = 100_000;
    const start = performance.now();
    let acc = 0;
    for (let i = 0; i < n; i += 1) {
      const hit = intersectRayPlane([i % 7, -50, (i % 13) * 0.1], [0.01 * (i % 5), 1, 0.02], f);
      if (hit.ok) acc += hit.u;
    }
    const perCallMs = (performance.now() - start) / n;
    console.log(`[plane ray projection] ${(perCallMs * 1000).toFixed(3)} us per call over ${n} rays (target 16 ms)`);
    expect(acc).not.toBeNaN();
    // One projection per pointer event must leave the frame budget untouched.
    expect(perCallMs).toBeLessThan(16);
    expect(perCallMs).toBeLessThan(0.05);
  });
});

describe('reassignment', () => {
  const pts: [number, number][] = [
    [0.1, -0.2],
    [0.3, 0.4],
  ];

  it('preserve_local keeps coordinates', () => {
    const r = reassignPoints(pts, principalFrame('XY'), principalFrame('XZ'), 'preserve_local');
    expect(r.points).toEqual(pts);
    expect(r.exact).toBe(true);
  });

  it('preserve_world on a shifted coplanar frame is exact', () => {
    const r = reassignPoints(pts, principalFrame('XY'), principalFrame('XY', [0.1, 0.2, 0]), 'preserve_world');
    near(r.points[0]!, [0, 0]);
    expect(r.exact).toBe(true);
  });

  it('project_to_target drops the off-plane part and reports how far it moved', () => {
    const r = reassignPoints(pts, principalFrame('XY'), principalFrame('XZ'), 'project_to_target');
    near(r.points[0]!, [0.1, 0]);
    expect(r.maxOffPlane).toBeCloseTo(0.4);
    expect(r.exact).toBe(false);
  });
});

describe('snapping', () => {
  it('converts a screen tolerance to local units per zoom', () => {
    const wpp = worldPerPixel(100, 90, 1000); // 2*100*tan(45deg)/1000 = 0.2
    expect(wpp).toBeCloseTo(0.2);
    expect(screenToleranceToLocal(8, wpp)).toBeCloseTo(1.6);
    // Zoomed in 10x: the same 8 px is a tenth of the local distance.
    expect(screenToleranceToLocal(8, worldPerPixel(10, 90, 1000))).toBeCloseTo(0.16);
  });

  it('snaps to the exact candidate within tolerance and leaves others precise', () => {
    const cands = strokeEndpoints([{ points: [[0, 0], [1, 1]] }, { points: [[2, 2]] }]);
    expect(cands).toEqual([[0, 0], [1, 1], [2, 2]]);
    expect(snapPoint([1.01, 0.99], cands, 0.05)).toEqual({ point: [1, 1], snapped: true, index: 1 });
    const free = snapPoint([1.123456789, 0.5], cands, 0.05);
    expect(free).toEqual({ point: [1.123456789, 0.5], snapped: false, index: -1 });
  });
});

describe('view hints', () => {
  it('reads an orthographic view from the camera direction, or none', () => {
    expect(viewHintFromDirection([0, 0, -1])).toBe('top');
    expect(viewHintFromDirection([0, 1, 0])).toBe('front');
    expect(viewHintFromDirection([-1, 0, 0])).toBe('side');
    expect(viewHintFromDirection([-1, 1, -1])).toBeNull();
  });
});

describe('3D placement', () => {
  it('transforms local strokes by the plane frame into the Y-up viewer', () => {
    const f = principalFrame('XZ', [0, 0, 10]);
    const [s] = placeStrokes([{ id: 'a', points: [[0.5, -0.25]] }], f, 100);
    // sketch (0.5, -0.25) -> local (0.5, 0.25) -> CAD (50, 0, 35) -> viewer (50, 35, 0)
    near(s!.points[0]!, [50, 35, -0]);
  });

  it('uses a known scale as given and labels an assumed one', () => {
    expect(displayScale({ unit: 'in', scale: 2 }, 10)).toMatchObject({ scale: 50.8, frameScale: 25.4, assumed: false });
    const unknown = displayScale({ unit: 'unknown', scale: null }, 40);
    expect(unknown.assumed).toBe(true);
    expect(unknown.scale).toBe(60);
    expect(unknown.label).toMatch(/assumed size/);
    expect(displayFrame(principalFrame('XY', [1, 0, 0]), 25.4).origin).toEqual([25.4, 0, 0]);
  });
});
