/**
 * Sketch plane mathematics for the dashboard (FORGE-790).
 *
 * The same conventions as `twin_core/models/sketch_plane.py`:
 *
 * | Principal | View  | U  | V  | Normal |
 * |-----------|-------|----|----|--------|
 * | XY        | Top   | +X | +Y | +Z     |
 * | XZ        | Front | +X | +Z | -Y     |
 * | YZ        | Side  | +Y | +Z | +X     |
 *
 * Every frame is right-handed (normal = U x V). World is CAD's Z-up frame;
 * the three.js viewer turns it Y-up the same way it turns a model.
 *
 * Sketch points are stored in sketch space (x right, y down, as drawn). On a
 * plane, local `u = x` and `v = -y`. Everything here is pure and allocation
 * light so a pointer move can call it without touching the network: the
 * plane ray projection target is 16 ms, and one call is microseconds.
 */

export type Vec3 = [number, number, number];
export type Vec2 = [number, number];
export type Principal = 'XY' | 'XZ' | 'YZ';
export type ReassignMode = 'preserve_world' | 'preserve_local' | 'project_to_target';

export interface PlaneFrame {
  origin: Vec3;
  u: Vec3;
  v: Vec3;
  normal: Vec3;
}

export const VIEW_NAMES: Record<Principal, string> = { XZ: 'Front', YZ: 'Side', XY: 'Top' };

export const PRINCIPAL_AXES: Record<Principal, { u: Vec3; v: Vec3; normal: Vec3 }> = {
  XY: { u: [1, 0, 0], v: [0, 1, 0], normal: [0, 0, 1] },
  XZ: { u: [1, 0, 0], v: [0, 0, 1], normal: [0, -1, 0] },
  YZ: { u: [0, 1, 0], v: [0, 0, 1], normal: [1, 0, 0] },
};

/** |d . n| below this is a ray running along the plane. */
export const PARALLEL_EPS = 1e-6;

// ── vectors ───────────────────────────────────────────────────────────────

export const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
export const length = (a: Vec3): number => Math.sqrt(dot(a, a));

export function principalFrame(p: Principal, origin: Vec3 = [0, 0, 0]): PlaneFrame {
  const a = PRINCIPAL_AXES[p];
  return { origin, u: a.u, v: a.v, normal: a.normal };
}

/** True for an orthonormal, right-handed frame (within `tol`). */
export function isValidFrame(f: PlaneFrame, tol = 1e-6): boolean {
  const n = cross(f.u, f.v);
  return (
    Math.abs(length(f.u) - 1) <= tol &&
    Math.abs(length(f.v) - 1) <= tol &&
    Math.abs(dot(f.u, f.v)) <= tol &&
    Math.abs(n[0] - f.normal[0]) <= tol &&
    Math.abs(n[1] - f.normal[1]) <= tol &&
    Math.abs(n[2] - f.normal[2]) <= tol
  );
}

/** The 4x4 local-to-world transform, row-major (columns U, V, N, origin). Same as the gateway's. */
export function frameMatrix(f: PlaneFrame): number[][] {
  const { origin: o, u, v, normal: n } = f;
  return [
    [u[0], v[0], n[0], o[0]],
    [u[1], v[1], n[1], o[1]],
    [u[2], v[2], n[2], o[2]],
    [0, 0, 0, 1],
  ];
}

// ── sketch space, local and world ─────────────────────────────────────────

export const sketchToLocal = (p: Vec2): Vec2 => [p[0], -p[1]];
export const localToSketch = (p: Vec2): Vec2 => [p[0], -p[1]];

export function localToWorld(u: number, v: number, f: PlaneFrame, w = 0): Vec3 {
  const { origin: o, u: a, v: b, normal: n } = f;
  return [
    o[0] + u * a[0] + v * b[0] + w * n[0],
    o[1] + u * a[1] + v * b[1] + w * n[1],
    o[2] + u * a[2] + v * b[2] + w * n[2],
  ];
}

/** World to local `(u, v, w)`; `w` is the signed distance off the plane. */
export function worldToLocal(p: Vec3, f: PlaneFrame): Vec3 {
  const d = sub(p, f.origin);
  return [dot(d, f.u), dot(d, f.v), dot(d, f.normal)];
}

/** A sketch-space point on a plane, in world units: `scale` world units per sketch unit. */
export function sketchToWorld(p: Vec2, f: PlaneFrame, scale = 1): Vec3 {
  const [u, v] = sketchToLocal(p);
  return localToWorld(u * scale, v * scale, f);
}

/** CAD Z-up world to the viewer's Y-up three.js world: (x, y, z) -> (x, z, -y). */
export const cadToViewer = (p: Vec3): Vec3 => [p[0], p[2], -p[1]];
export const viewerToCad = (p: Vec3): Vec3 => [p[0], -p[2], p[1]];

// ── 3D placement (RC-04) ──────────────────────────────────────────────────

export interface DisplayScale {
  /** Viewer units (mm) per sketch unit. */
  scale: number;
  /** Viewer units per unit of the plane frame (its origin). */
  frameScale: number;
  /** True when the size is a display assumption, not a known scale. */
  assumed: boolean;
  label: string;
}

const MM_PER_UNIT: Record<string, number> = { mm: 1, in: 25.4 };

/**
 * How big to draw a sketch in the viewer. A known scale and unit are used as
 * given (inches shown in millimetres, explicitly). Anything unknown is drawn
 * at an assumed size, sized to the model, and labelled so: a display choice,
 * never a measurement (PLN-013).
 */
export function displayScale(plane: { unit: string; scale: number | null }, modelRadius?: number | null): DisplayScale {
  const unitMm = MM_PER_UNIT[plane.unit];
  if (unitMm !== undefined && plane.scale) {
    return { scale: plane.scale * unitMm, frameScale: unitMm, assumed: false, label: `1 sketch unit = ${round3(plane.scale)} ${plane.unit}` };
  }
  const assumed = Math.max(1, (modelRadius ?? 36) * 1.5);
  return {
    scale: assumed,
    frameScale: unitMm ?? assumed,
    assumed: true,
    label: 'Scale unknown · shown at an assumed size (display only)',
  };
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}

export interface PlacedStroke {
  id: string;
  /** Points in the viewer's Y-up world. */
  points: Vec3[];
}

/** A plane's frame as the viewer draws it: origin scaled to viewer units. */
export function displayFrame(f: PlaneFrame, frameScale: number): PlaneFrame {
  return { ...f, origin: [f.origin[0] * frameScale, f.origin[1] * frameScale, f.origin[2] * frameScale] };
}

/** Local strokes transformed by the plane frame into the viewer's world. */
export function placeStrokes(
  strokes: readonly { id: string; points: readonly Vec2[] }[],
  f: PlaneFrame,
  scale: number,
): PlacedStroke[] {
  return strokes.map((s) => ({ id: s.id, points: s.points.map((p) => cadToViewer(sketchToWorld(p, f, scale))) }));
}

// ── ray projection (PLN-005) ──────────────────────────────────────────────

export type RayFailure = 'parallel' | 'behind_camera' | 'miss';
export type RayHit =
  | { ok: true; u: number; v: number; t: number; point: Vec3 }
  | { ok: false; reason: RayFailure };

export interface RayOptions {
  /** |d . n| (with d normalised) below this is `parallel`. */
  eps?: number;
  /** Hits farther than this along the ray are a `miss` (beyond the far plane). */
  maxDistance?: number;
}

/**
 * Intersect a camera ray with a plane and return local `(u, v)`.
 *
 * Fails, typed, rather than returning a wild point: `parallel` when the ray
 * runs along the plane (|d . n| < eps), `behind_camera` when the plane is
 * behind the ray origin (t < 0), and `miss` for a degenerate ray or a hit
 * beyond `maxDistance`.
 */
export function intersectRayPlane(origin: Vec3, direction: Vec3, f: PlaneFrame, opts: RayOptions = {}): RayHit {
  const eps = opts.eps ?? PARALLEL_EPS;
  const len = length(direction);
  if (!(len > 0) || !Number.isFinite(len) || !origin.every(Number.isFinite)) return { ok: false, reason: 'miss' };
  const d: Vec3 = [direction[0] / len, direction[1] / len, direction[2] / len];
  const denom = dot(d, f.normal);
  if (Math.abs(denom) < eps) return { ok: false, reason: 'parallel' };
  const t = dot(sub(f.origin, origin), f.normal) / denom;
  if (t < 0) return { ok: false, reason: 'behind_camera' };
  if (opts.maxDistance !== undefined && t > opts.maxDistance) return { ok: false, reason: 'miss' };
  const point: Vec3 = [origin[0] + d[0] * t, origin[1] + d[1] * t, origin[2] + d[2] * t];
  const local = worldToLocal(point, f);
  return { ok: true, u: local[0], v: local[1], t, point };
}

// ── reassignment (PLN-008) ────────────────────────────────────────────────

export interface Reassigned {
  points: Vec2[];
  /** The farthest a point is (or moved) from the target plane. */
  maxOffPlane: number;
  /** Every point keeps its meaning exactly. */
  exact: boolean;
}

/** Where sketch-space points land on `to` in `mode`. Mirrors the gateway's preview. */
export function reassignPoints(points: readonly Vec2[], from: PlaneFrame, to: PlaneFrame, mode: ReassignMode, tol = 1e-6): Reassigned {
  if (mode === 'preserve_local') return { points: points.map((p) => [p[0], p[1]]), maxOffPlane: 0, exact: true };
  let worst = 0;
  const out = points.map((p): Vec2 => {
    const [u, v] = sketchToLocal(p);
    const [lu, lv, lw] = worldToLocal(localToWorld(u, v, from), to);
    worst = Math.max(worst, Math.abs(lw));
    return localToSketch([lu, lv]);
  });
  return { points: out, maxOffPlane: worst, exact: worst <= tol };
}

// ── snapping (PLN-007) ────────────────────────────────────────────────────

/** World units one screen pixel spans at `distance` from a perspective camera. */
export function worldPerPixel(distance: number, fovDeg: number, viewportHeightPx: number): number {
  return (2 * distance * Math.tan((fovDeg * Math.PI) / 360)) / Math.max(1, viewportHeightPx);
}

/** A screen-space tolerance in local plane units at the current zoom. */
export function screenToleranceToLocal(tolerancePx: number, worldPerPx: number, worldPerLocalUnit = 1): number {
  return (tolerancePx * worldPerPx) / Math.max(1e-12, worldPerLocalUnit);
}

export interface Snap {
  point: Vec2;
  snapped: boolean;
  /** Index of the candidate snapped to, or -1. */
  index: number;
}

/**
 * Snap `p` to the nearest candidate within `tolerance` (local units). The
 * stored point is the candidate's exact coordinate, or `p` unchanged: never a
 * pixel-rounded value, so precision does not depend on the zoom.
 */
export function snapPoint(p: Vec2, candidates: readonly Vec2[], tolerance: number): Snap {
  let best = -1;
  let bestD = tolerance * tolerance;
  for (let i = 0; i < candidates.length; i += 1) {
    const c = candidates[i]!;
    const dx = c[0] - p[0];
    const dy = c[1] - p[1];
    const d = dx * dx + dy * dy;
    if (d <= bestD) {
      bestD = d;
      best = i;
    }
  }
  return best < 0 ? { point: p, snapped: false, index: -1 } : { point: candidates[best]!, snapped: true, index: best };
}

/** The endpoints of strokes, as snap candidates. */
export function strokeEndpoints(strokes: readonly { points: readonly Vec2[] }[]): Vec2[] {
  const out: Vec2[] = [];
  for (const s of strokes) {
    const a = s.points[0];
    const b = s.points[s.points.length - 1];
    if (a) out.push(a);
    if (b && s.points.length > 1) out.push(b);
  }
  return out;
}

// ── views ─────────────────────────────────────────────────────────────────

/**
 * The orthographic view a CAD-frame camera *view direction* (from the camera
 * toward the target) is looking along, within `toleranceDeg`; `null`
 * otherwise. A hint for a proposal only: it never changes a plane.
 */
export function viewHintFromDirection(dir: Vec3, toleranceDeg = 10): 'front' | 'side' | 'top' | null {
  const len = length(dir);
  if (!(len > 0)) return null;
  const d: Vec3 = [dir[0] / len, dir[1] / len, dir[2] / len];
  const cos = Math.cos((toleranceDeg * Math.PI) / 180);
  // Looking along -normal means the camera sits on the normal side.
  for (const [hint, p] of [['top', 'XY'], ['front', 'XZ'], ['side', 'YZ']] as const) {
    if (-dot(d, PRINCIPAL_AXES[p].normal) >= cos) return hint;
  }
  return null;
}
