import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { buildPreview, buildPreviewTimed } from '../buildPreview';
import { profileLoops } from '../profile';
import { amountOf, isDirty, withOverrides } from '../definition';
import { PLATE_WITH_HOLES, SLOT, definition } from './fixtures';

/**
 * The L2 preview mesh, built in the browser from the shared feature
 * definition (C3D slice 5, 3D-RC-004).
 */

function topFaceCentroids(g: THREE.BufferGeometry, z: number): [number, number][] {
  const pos = g.getAttribute('position');
  const out: [number, number][] = [];
  for (let i = 0; i < pos.count; i += 3) {
    const zs = [pos.getZ(i), pos.getZ(i + 1), pos.getZ(i + 2)];
    if (zs.every((v) => Math.abs(v - z) < 1e-6)) {
      out.push([(pos.getX(i) + pos.getX(i + 1) + pos.getX(i + 2)) / 3, (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3]);
    }
  }
  return out;
}

describe('buildPreview', () => {
  it('extrudes a profile by its depth, in the plane frame, in millimetres', () => {
    const b = buildPreview(definition());
    expect(b.box.min).toEqual([0, 0, 0]);
    expect(b.box.max).toEqual([40, 20, 5]);
    expect(b.volumeMm3).toBeCloseTo(40 * 20 * 5);
    expect(b.handles[0]).toMatchObject({ kind: 'extrude_depth', value: 5, position: [20, 10, 5], direction: [0, 0, 1] });
  });

  it('places the profile on the plane the B-Rep uses (CadQuery XZ: v along +Z, extrude along -Y)', () => {
    const b = buildPreview(
      definition({ frame: { origin: [0, -3, 0], x_dir: [1, 0, 0], y_dir: [0, 0, 1], normal: [0, -1, 0] } }),
    );
    expect(b.box.min[1]).toBeCloseTo(-8);
    expect(b.box.max[1]).toBeCloseTo(-3);
    expect(b.box.max[2]).toBeCloseTo(20);
  });

  it('cuts through-holes without any boolean: inner loops become shape holes', () => {
    const b = buildPreview(definition({ elements: PLATE_WITH_HOLES, depth: 4 }));
    expect(b.throughHoles).toBe(2);
    expect(b.profile.holes).toHaveLength(2);
    // No top-face triangle sits inside a hole.
    const inHole = topFaceCentroids(b.geometry, 4).filter(([x, y]) => Math.hypot(x - 15, y - 15) < 4.5 || Math.hypot(x - 45, y - 15) < 4.5);
    expect(inHole).toEqual([]);
    expect(b.volumeMm3).toBeCloseTo((60 * 30 - 2 * Math.PI * 25) * 4, -1);
  });

  it('chains lines and arcs into one closed profile (an arc-ended slot)', () => {
    const profile = profileLoops(SLOT);
    expect(profile.openChains).toBe(0);
    expect(profile.holes).toHaveLength(0);
    const b = buildPreview(definition({ elements: SLOT }));
    expect(b.box.min[0]).toBeCloseTo(-10);
    expect(b.box.max[0]).toBeCloseTo(50);
    expect(b.box.min[1]).toBeCloseTo(-20);
    // Chord error of 6 degree segments: within 0.3% of the exact slot volume.
    const exact = (40 * 20 + Math.PI * 100) * 5;
    expect(Math.abs(b.volumeMm3 - exact) / exact).toBeLessThan(0.003);
  });

  it('revolves a profile about the plane v axis (Pappus volume, caps on a part turn)', () => {
    const whole = buildPreview(
      definition({ op: 'revolve', depth: undefined, angle: 360, elements: [{ type: 'rectangle', origin: [10, 0], width: 5, height: 20 }] }),
    );
    expect(whole.volumeMm3).toBeCloseTo(Math.PI * (15 ** 2 - 10 ** 2) * 20);
    expect(whole.box.max[0]).toBeCloseTo(15);
    expect(whole.box.min[0]).toBeCloseTo(-15, 0);
    expect(whole.box.max[1]).toBeCloseTo(20);
    const half = buildPreview(
      definition({ op: 'revolve', depth: undefined, angle: 180, elements: [{ type: 'rectangle', origin: [10, 0], width: 5, height: 20 }] }),
    );
    expect(half.volumeMm3).toBeCloseTo(whole.volumeMm3 / 2);
    expect(half.triangles).toBeGreaterThan(0);
  });

  it('draws an approximate shell: a closed cavity inset by the thickness', () => {
    const solid = buildPreview(definition());
    const shelled = buildPreview(definition({ shell_thickness: 1 }));
    expect(shelled.volumeMm3).toBeCloseTo(40 * 20 * 5 - 38 * 18 * 3);
    expect(shelled.triangles).toBeGreaterThan(solid.triangles);
  });

  it('applies a sketch scale (inches drawn in millimetres)', () => {
    const b = buildPreview(definition({ unit: 'in', scale_to_mm: 25.4, elements: [{ type: 'rectangle', origin: [0, 0], width: 1, height: 1 }], depth: 1 }));
    for (const v of b.box.max) expect(v).toBeCloseTo(25.4, 5);
  });

  it('refuses a profile that never closes', () => {
    expect(() => buildPreview(definition({ elements: [{ type: 'line', start: [0, 0], end: [10, 0] }] }))).toThrow(/closed loop/);
  });
});

describe('parameter changes', () => {
  it('a new depth or radius updates the mesh from the definition alone', () => {
    const base = definition({
      elements: PLATE_WITH_HOLES,
      dimensions: [{ constraint_id: 'k1', type: 'radius', primitive_id: 'c1', value: 5, unit: 'mm', source: 'human_confirmed', center: [15, 15] }],
    });
    const deeper = buildPreview(withOverrides(base, { amount: 9 }));
    expect(deeper.box.max[2]).toBeCloseTo(9);
    const wider = buildPreview(withOverrides(base, { radius: { k1: 7 } }));
    expect(wider.volumeMm3).toBeLessThan(buildPreview(base).volumeMm3);
    expect(wider.handles.find((h) => h.kind === 'radius')?.value).toBe(7);
    expect(isDirty(base, { amount: 9 })).toBe(true);
    expect(isDirty(base, { amount: amountOf(base) })).toBe(false);
  });

  it('rebuilds a simple part well inside the 100 ms budget (micro-benchmark)', () => {
    // A plate with two holes, a slot and a revolve: the "simple parts" of 3D-RC-004.
    const cases = [
      definition({ elements: PLATE_WITH_HOLES, depth: 4 }),
      definition({ elements: SLOT, depth: 5 }),
      definition({ op: 'revolve', angle: 270, elements: [{ type: 'rectangle', origin: [10, 0], width: 5, height: 20 }] }),
    ];
    const samples: number[] = [];
    for (let i = 0; i < 30; i++) {
      for (const c of cases) {
        const { ms, build } = buildPreviewTimed(withOverrides(c, { amount: 3 + (i % 7) }));
        build.geometry.dispose();
        samples.push(ms);
      }
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)]!;
    const p95 = samples[Math.floor(samples.length * 0.95)]!;
    console.info(`L2 preview rebuild: median ${median.toFixed(2)} ms, p95 ${p95.toFixed(2)} ms over ${samples.length} builds`);
    expect(median).toBeLessThan(100);
    expect(p95).toBeLessThan(100);
  });
});
