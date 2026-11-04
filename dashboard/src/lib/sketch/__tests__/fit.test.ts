import { describe, expect, it } from 'vitest';
import { fitSketch } from '../fit';
import { fitCircle, fitEllipse, fitLine } from '../fit/primitives';
import { arcSweep, type ArcPrim, type CirclePrim, type EllipsePrim, type LinePrim, type Vec } from '../model';
import { arcPts, circlePts, ellipsePts, linePts, noisy, polyline } from './synthetic';

const SIGMA = 0.002;

describe('primitive fits', () => {
  it('fits a noisy line by total least squares', () => {
    const fit = fitLine(noisy(linePts([-0.3, -0.1], [0.4, 0.25], 80), SIGMA, 3));
    expect(fit.rms).toBeLessThan(3 * SIGMA);
    expect(fit.start[0]).toBeCloseTo(-0.3, 1);
    expect(fit.end[1]).toBeCloseTo(0.25, 1);
    const angle = Math.atan2(fit.end[1] - fit.start[1], fit.end[0] - fit.start[0]);
    expect(Math.abs(angle - Math.atan2(0.35, 0.7))).toBeLessThan(0.02);
  });

  it('fits a noisy circle (algebraic, then geometric refinement)', () => {
    const fit = fitCircle(noisy(circlePts([0.1, -0.05], 0.2, 100), SIGMA, 4))!;
    expect(fit.center[0]).toBeCloseTo(0.1, 2);
    expect(fit.center[1]).toBeCloseTo(-0.05, 2);
    expect(fit.radius).toBeCloseTo(0.2, 2);
  });

  it('fits a short noisy arc', () => {
    const fit = fitCircle(noisy(arcPts([0, 0], 0.3, 0.2, 1.4, 60), SIGMA, 5))!;
    expect(Math.abs(fit.radius - 0.3)).toBeLessThan(0.02);
  });

  it('fits a noisy rotated ellipse', () => {
    const fit = fitEllipse(noisy(ellipsePts([0.05, 0.02], 0.3, 0.15, 0.5, 200), SIGMA, 6))!;
    expect(fit.major).toBeCloseTo(0.3, 1);
    expect(fit.minor).toBeCloseTo(0.15, 1);
    expect(Math.abs(fit.rotation - 0.5)).toBeLessThan(0.05);
    expect(fit.center[0]).toBeCloseTo(0.05, 2);
  });
});

describe('fitSketch', () => {
  it('turns a straight stroke into one line with its source range', () => {
    const pts = noisy(linePts([-0.4, 0], [0.4, 0.02], 60), SIGMA, 7);
    const { primitives } = fitSketch([{ id: 's1', points: pts }]);
    expect(primitives).toHaveLength(1);
    const line = primitives[0] as LinePrim;
    expect(line.type).toBe('line');
    expect(line.id).toBe('p-s1-0');
    expect(line.estimated).toBe(true);
    expect(line.source_stroke_ids).toEqual(['s1']);
    expect(line.source_ranges[0]).toEqual({ stroke_id: 's1', start: 0, end: 59 });
    expect(line.confidence).toBeGreaterThan(0.6);
  });

  it('cuts a polyline at its corners', () => {
    const corners: Vec[] = [[-0.4, -0.3], [0.3, -0.3], [0.3, 0.2], [-0.1, 0.35]];
    const { primitives } = fitSketch([{ id: 'zig', points: noisy(polyline(corners, 40), SIGMA, 8) }]);
    expect(primitives.map((p) => p.type)).toEqual(['line', 'line', 'line']);
    const [a, b] = primitives as LinePrim[];
    // Neighbouring lines meet exactly at the snapped corner.
    expect(a!.end[0]).toBeCloseTo(b!.start[0], 9);
    expect(Math.hypot(a!.end[0] - 0.3, a!.end[1] + 0.3)).toBeLessThan(0.02);
    // Ranges are contiguous through the stroke.
    expect(primitives[1]!.source_ranges[0]!.start).toBe(primitives[0]!.source_ranges[0]!.end);
  });

  it('fits a noisy open arc', () => {
    const { primitives } = fitSketch([{ id: 'a', points: noisy(arcPts([0, 0], 0.3, Math.PI, 0, 80), SIGMA, 9) }]);
    expect(primitives).toHaveLength(1);
    const arc = primitives[0] as ArcPrim;
    expect(arc.type).toBe('arc');
    expect(arc.radius).toBeCloseTo(0.3, 1);
    expect(arcSweep(arc)).toBeGreaterThan(170);
    expect(arcSweep(arc)).toBeLessThan(190);
  });

  it('recognises closed circles and ellipses as closed profiles', () => {
    const { primitives, profiles } = fitSketch([
      { id: 'c', points: noisy(circlePts([-0.2, 0], 0.15, 100), SIGMA, 10) },
      { id: 'e', points: noisy(ellipsePts([0.3, 0], 0.18, 0.09, 0.3, 140), SIGMA, 11) },
    ]);
    const circle = primitives.find((p) => p.type === 'circle') as CirclePrim;
    const ellipse = primitives.find((p) => p.type === 'ellipse') as EllipsePrim;
    expect(circle.radius).toBeCloseTo(0.15, 2);
    expect(ellipse.major_radius).toBeCloseTo(0.18, 1);
    expect(ellipse.minor_radius).toBeCloseTo(0.09, 1);
    expect(profiles.map((p) => p.primitive_ids)).toEqual([[circle.id], [ellipse.id]]);
  });

  it('closes a rectangle drawn as one stroke into four lines and one profile', () => {
    const corners: Vec[] = [[-0.3, -0.2], [0.3, -0.2], [0.3, 0.2], [-0.3, 0.2], [-0.3, -0.2]];
    const { primitives, profiles } = fitSketch([{ id: 'r', points: noisy(polyline(corners, 40), SIGMA, 12) }]);
    expect(primitives.map((p) => p.type)).toEqual(['line', 'line', 'line', 'line']);
    expect(profiles).toHaveLength(1);
    expect(profiles[0]!.primitive_ids).toHaveLength(4);
  });

  it('detects a profile closed across separate strokes, and not an open one', () => {
    const tri = [
      { id: 't1', points: linePts([0, 0], [0.4, 0]) },
      { id: 't2', points: linePts([0.4, 0], [0.2, 0.3]) },
      { id: 't3', points: linePts([0.2, 0.3], [0.003, 0.002]) },
    ];
    expect(fitSketch(tri).profiles).toHaveLength(1);
    expect(fitSketch(tri.slice(0, 2)).profiles).toHaveLength(0);
  });

  it('splits a line-arc-line stroke into its parts', () => {
    const pts = [
      ...linePts([-0.3, 0.2], [-0.3, 0], 30),
      ...arcPts([0, 0], 0.3, Math.PI, 0, 60).slice(1),
      ...linePts([0.3, 0], [0.3, 0.2], 30).slice(1),
    ];
    const { primitives } = fitSketch([{ id: 'u', points: noisy(pts, SIGMA * 0.5, 13) }]);
    expect(primitives.map((p) => p.type)).toEqual(['line', 'arc', 'line']);
  });

  it('falls back to a closed spline for a free blob, marked uncertain', () => {
    const blob: Vec[] = Array.from({ length: 241 }, (_, i) => {
      const t = (2 * Math.PI * i) / 240;
      const r = 0.2 + 0.03 * Math.sin(7 * t);
      return [r * Math.cos(t), r * Math.sin(t)];
    });
    const { primitives, profiles } = fitSketch([{ id: 'b', points: noisy(blob, SIGMA * 0.5, 15) }]);
    expect(primitives).toHaveLength(1);
    expect(primitives[0]!.type).toBe('spline');
    expect(primitives[0]!.confidence).toBeLessThan(0.6);
    expect(profiles).toHaveLength(1);
  });

  it('skips a tap and scales its tolerance with the sketch and the screen', () => {
    const big = fitSketch([{ id: 'a', points: linePts([-0.4, 0], [0.4, 0]) }, { id: 'dot', points: [[0, 0]] }], { unitPx: 600 });
    expect(big.report.skipped_stroke_ids).toEqual(['dot']);
    expect(big.report.tolerance).toBeCloseTo(0.01 * 0.8, 6);
    // On a small screen the pixel floor wins.
    const small = fitSketch([{ id: 'a', points: linePts([-0.01, 0], [0.01, 0]) }], { unitPx: 100 });
    expect(small.report.tolerance).toBeCloseTo(3 / 100, 6);
  });

  it('gives the same ids for the same strokes', () => {
    const strokes = [{ id: 'k', points: noisy(polyline([[0, 0], [0.4, 0], [0.4, 0.3]], 30), SIGMA, 14) }];
    expect(fitSketch(strokes).primitives.map((p) => p.id)).toEqual(fitSketch(strokes).primitives.map((p) => p.id));
  });
});
