import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import type { PreviewDefinition, Vec2, Vec3 } from './definition';
import { REVOLVE_SEGMENT_DEG, insetLoop, profileLoops, signedArea, type Profile } from './profile';

/**
 * The L2 parametric preview mesh, built in the browser from the shared
 * feature definition (spec section 3, L2; 3D-RC-004). Supported features:
 *
 * - **extrude**: the profile's outer loop as a three.js `Shape`, inner loops
 *   as its holes (so through-holes are cut without any boolean), islands as
 *   extra shapes, extruded by the depth;
 * - **revolve**: the profile turned about the plane's `v` axis by the angle
 *   (`LatheGeometry`), with flat caps when the turn is not whole;
 * - **shell** (approximate): an empty-selector shell is drawn as the outer
 *   solid plus an inward cavity, the outer loop inset by the thickness.
 *
 * Everything is in millimetres in the world frame the B-Rep uses, so the L3
 * tessellation can replace it in place. Nothing here calls the network.
 */

export interface PreviewHandle {
  kind: 'extrude_depth' | 'radius' | 'length' | 'revolve_angle';
  /** `depth` for the extrude handle, `angle` for the revolve one. */
  parameter?: string;
  constraintId?: string;
  /** The sketch element a dimension handle sizes. */
  primitiveId?: string;
  /** Where the handle sits, world millimetres. */
  position: Vec3;
  /** The unit direction a drag moves it along, world. */
  direction: Vec3;
  /** The value it edits, in the definition's units. */
  value: number;
  /** World millimetres per unit of the value (default: the definition's scale). Degrees for an angle. */
  mmPerUnit?: number;
  /** Snap step and limits of the value. */
  step?: number;
  min?: number;
  max?: number;
  /** The revolve gizmo: the arc it is drawn along, world millimetres. */
  arc?: Vec3[];
}

export interface PreviewBuild {
  /** World-space geometry, millimetres. */
  geometry: THREE.BufferGeometry;
  profile: Profile;
  /** Analytic volume, mm^3 (the same formula the gateway's comparison uses). */
  volumeMm3: number;
  throughHoles: number;
  box: { min: Vec3; max: Vec3 };
  handles: PreviewHandle[];
  triangles: number;
}

function v3(a: Vec3): THREE.Vector3 {
  return new THREE.Vector3(a[0], a[1], a[2]);
}

/** Plane coordinates (u, v, h) in millimetres to world. */
function frameMatrix(def: PreviewDefinition): THREE.Matrix4 {
  const { origin, x_dir, y_dir, normal } = def.frame;
  const m = new THREE.Matrix4().makeBasis(v3(x_dir), v3(y_dir), v3(normal));
  m.setPosition(v3(origin));
  return m;
}

function scaled(loop: Vec2[], s: number): THREE.Vector2[] {
  return loop.map(([u, v]) => new THREE.Vector2(u * s, v * s));
}

function shapeOf(outer: Vec2[], holes: Vec2[][], s: number): THREE.Shape {
  const shape = new THREE.Shape(scaled(outer, s));
  for (const h of holes) shape.holes.push(new THREE.Path(scaled(h, s)));
  return shape;
}

function nonIndexed(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const out = g.index ? g.toNonIndexed() : g;
  if (!out.getAttribute('normal')) out.computeVertexNormals();
  if (!out.getAttribute('uv')) {
    out.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array((out.getAttribute('position').count) * 2), 2));
  }
  return out;
}

/** Reverse every triangle (an inward-facing cavity wall). */
function flipped(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const out = nonIndexed(g.clone());
  for (const name of ['position', 'normal', 'uv']) {
    const attr = out.getAttribute(name) as THREE.BufferAttribute;
    const size = attr.itemSize;
    const arr = attr.array as Float32Array;
    for (let t = 0; t < attr.count; t += 3) {
      for (let k = 0; k < size; k++) {
        const a = (t + 1) * size + k;
        const b = (t + 2) * size + k;
        const tmp = arr[a]!;
        arr[a] = arr[b]!;
        arr[b] = tmp;
      }
    }
  }
  const normal = (out.getAttribute('normal') as THREE.BufferAttribute).array as Float32Array;
  for (let i = 0; i < normal.length; i++) normal[i] = -normal[i]!;
  return out;
}

function firstMomentU(loop: Vec2[]): number {
  let s = 0;
  for (let i = 0; i < loop.length; i++) {
    const [u1, v1] = loop[i]!;
    const [u2, v2] = loop[(i + 1) % loop.length]!;
    s += (u1 + u2) * (u1 * v2 - u2 * v1);
  }
  return s / 6;
}

function regionArea(p: Profile): number {
  return (
    Math.abs(signedArea(p.outer)) -
    p.holes.reduce((a, h) => a + Math.abs(signedArea(h)), 0) +
    p.islands.reduce((a, h) => a + Math.abs(signedArea(h)), 0)
  );
}

function regionMoment(p: Profile): number {
  const m = (lp: Vec2[]) => firstMomentU(lp) * (signedArea(lp) >= 0 ? 1 : -1);
  return m(p.outer) - p.holes.reduce((a, h) => a + m(h), 0) + p.islands.reduce((a, h) => a + m(h), 0);
}

function centroid(loop: Vec2[]): Vec2 {
  const n = loop.length || 1;
  return [loop.reduce((a, p) => a + p[0], 0) / n, loop.reduce((a, p) => a + p[1], 0) / n];
}

/**
 * A handle at the middle of each line with an accepted length dimension,
 * dragged along the line (it grows the line). `h` is the height above the
 * plane it sits at (the top face of an extrude).
 */
function lengthHandles(
  def: PreviewDefinition,
  s: number,
  h: number,
  worldPoint: (u: number, v: number, h: number) => Vec3,
  worldDir: (d: Vec3) => Vec3,
): PreviewHandle[] {
  const out: PreviewHandle[] = [];
  for (const d of def.dimensions ?? []) {
    if (d.type !== 'length') continue;
    const line = def.elements.find((e) => e.type === 'line' && e.id === d.primitive_id);
    if (!line || line.type !== 'line') continue;
    const du = line.end[0] - line.start[0];
    const dv = line.end[1] - line.start[1];
    const len = Math.hypot(du, dv);
    if (len < 1e-12) continue;
    out.push({
      kind: 'length',
      constraintId: d.constraint_id,
      primitiveId: d.primitive_id,
      position: worldPoint(((line.start[0] + line.end[0]) / 2) * s, ((line.start[1] + line.end[1]) / 2) * s, h),
      direction: worldDir([du / len, dv / len, 0]),
      value: len,
    });
  }
  return out;
}

/** Degrees between the revolve gizmo's arc points. */
const GIZMO_STEP_DEG = 5;

/**
 * The revolve angle gizmo: an arc just outside the part, about the revolve
 * axis, from the profile to where the turn ends, with the handle at its end.
 * A drag along the arc's end tangent changes the angle (degrees).
 */
function revolveHandle(profile: Profile, s: number, angle: number, lathe: THREE.Matrix4): PreviewHandle {
  const all = [...profile.outer, ...profile.holes.flat(), ...profile.islands.flat()];
  const reach = Math.max(1e-6, ...all.map(([u]) => Math.abs(u))) * s;
  const r = reach * 1.25;
  const vs = profile.outer.map(([, v]) => v * s);
  const vMid = vs.length ? (Math.min(...vs) + Math.max(...vs)) / 2 : 0;
  const at = (deg: number): Vec3 => {
    const phi = (deg * Math.PI) / 180;
    const p = new THREE.Vector3(r * Math.sin(phi), vMid, r * Math.cos(phi)).applyMatrix4(lathe);
    return [p.x, p.y, p.z];
  };
  const arc: Vec3[] = [];
  for (let deg = 0; deg < angle; deg += GIZMO_STEP_DEG) arc.push(at(deg));
  arc.push(at(angle));
  const phi = (angle * Math.PI) / 180;
  const tangent = new THREE.Vector3(Math.cos(phi), 0, -Math.sin(phi)).transformDirection(lathe);
  return {
    kind: 'revolve_angle',
    parameter: 'angle',
    position: at(angle),
    direction: [tangent.x, tangent.y, tangent.z],
    value: angle,
    // One degree along the arc, in millimetres.
    mmPerUnit: (r * Math.PI) / 180,
    step: 1,
    min: 1,
    max: 360,
    arc,
  };
}

/** Build the preview mesh of a definition. Throws when the profile has no closed loop. */
export function buildPreview(def: PreviewDefinition): PreviewBuild {
  const s = def.scale_to_mm || 1;
  const profile = profileLoops(def.elements);
  const toWorld = frameMatrix(def);
  const parts: THREE.BufferGeometry[] = [];
  let volume = 0;
  const handles: PreviewHandle[] = [];
  const worldPoint = (u: number, v: number, h: number): Vec3 => {
    const p = new THREE.Vector3(u, v, h).applyMatrix4(toWorld);
    return [p.x, p.y, p.z];
  };
  const worldDir = (d: Vec3): Vec3 => {
    const p = v3(d).transformDirection(toWorld);
    return [p.x, p.y, p.z];
  };

  if (def.op === 'extrude') {
    const depth = Number(def.depth ?? 0) * s;
    const shapes = [shapeOf(profile.outer, profile.holes, s), ...profile.islands.map((i) => shapeOf(i, [], s))];
    parts.push(nonIndexed(new THREE.ExtrudeGeometry(shapes, { depth, bevelEnabled: false, steps: 1, curveSegments: 1 })));
    volume = regionArea(profile) * s * s * depth;
    const t = Number(def.shell_thickness ?? 0) * s;
    if (t > 0 && depth > 2 * t) {
      // Approximate shell: the cavity is the outer loop inset by t, closed at both ends.
      const inner = insetLoop(profile.outer.map(([u, v]) => [u * s, v * s] as Vec2), t);
      const cavity = new THREE.ExtrudeGeometry(new THREE.Shape(inner.map(([u, v]) => new THREE.Vector2(u, v))), {
        depth: depth - 2 * t,
        bevelEnabled: false,
        steps: 1,
        curveSegments: 1,
      });
      cavity.translate(0, 0, t);
      parts.push(flipped(cavity));
      volume -= Math.abs(signedArea(inner)) * (depth - 2 * t);
    }
    const [cu, cv] = centroid(profile.outer);
    handles.push({
      kind: 'extrude_depth',
      parameter: 'depth',
      position: worldPoint(cu * s, cv * s, depth),
      direction: worldDir([0, 0, 1]),
      value: Number(def.depth ?? 0),
    });
    for (const d of def.dimensions ?? []) {
      if (d.type === 'length') continue;
      const circle = def.elements.find((e) => e.type === 'circle' && e.id === d.primitive_id);
      if (!circle || circle.type !== 'circle') continue;
      handles.push({
        kind: 'radius',
        constraintId: d.constraint_id,
        primitiveId: d.primitive_id,
        position: worldPoint((circle.center[0] + circle.radius) * s, circle.center[1] * s, depth),
        direction: worldDir([1, 0, 0]),
        value: circle.radius,
      });
    }
    handles.push(...lengthHandles(def, s, depth, worldPoint, worldDir));
  } else {
    const angle = Math.min(Number(def.angle ?? 360), 360);
    const segments = Math.max(3, Math.ceil(angle / REVOLVE_SEGMENT_DEG));
    const phi = (angle * Math.PI) / 180;
    // Lathe local: x = u sin(phi), y = v (the axis), z = u cos(phi). Mapped so
    // that phi turns X towards -N about +Y, as the kernel's revolve does.
    const { x_dir, y_dir, normal, origin } = def.frame;
    const lathe = new THREE.Matrix4().makeBasis(v3(normal).negate(), v3(y_dir), v3(x_dir));
    lathe.setPosition(v3(origin));
    for (const loop of [profile.outer, ...profile.holes, ...profile.islands]) {
      const pts = [...loop, loop[0]!].map(([u, v]) => new THREE.Vector2(Math.abs(u) * s, v * s));
      const g = nonIndexed(new THREE.LatheGeometry(pts, segments, 0, phi));
      g.applyMatrix4(lathe);
      parts.push(g);
    }
    if (angle < 360) {
      for (const at of [0, phi]) {
        const cap = nonIndexed(
          new THREE.ShapeGeometry(
            shapeOf(
              profile.outer.map(([u, v]) => [Math.abs(u), v] as Vec2),
              profile.holes.map((h) => h.map(([u, v]) => [Math.abs(u), v] as Vec2)),
              s,
            ),
          ),
        );
        const sx = new THREE.Vector3(Math.sin(at), 0, Math.cos(at));
        const sy = new THREE.Vector3(0, 1, 0);
        cap.applyMatrix4(new THREE.Matrix4().makeBasis(sx, sy, new THREE.Vector3().crossVectors(sx, sy)));
        cap.applyMatrix4(lathe);
        parts.push(cap);
      }
    }
    volume = Math.abs(regionMoment(profile)) * s * s * s * phi;
    handles.push(revolveHandle(profile, s, angle, lathe));
    handles.push(...lengthHandles(def, s, 0, worldPoint, worldDir));
  }

  const merged = parts.length === 1 ? parts[0] : mergeGeometries(parts, false);
  const geometry = merged ?? parts[0];
  if (!geometry) throw new Error('the preview has no geometry');
  if (def.op === 'extrude') geometry.applyMatrix4(toWorld);
  geometry.computeBoundingBox();
  const bb = geometry.boundingBox!;
  return {
    geometry,
    profile,
    volumeMm3: volume,
    throughHoles: def.op === 'extrude' ? profile.holes.length : 0,
    box: { min: [bb.min.x, bb.min.y, bb.min.z], max: [bb.max.x, bb.max.y, bb.max.z] },
    handles,
    triangles: geometry.getAttribute('position').count / 3,
  };
}

/** Build and time it. `now` is injectable for tests. */
export function buildPreviewTimed(def: PreviewDefinition, now: () => number = () => performance.now()): { build: PreviewBuild; ms: number } {
  const t0 = now();
  const build = buildPreview(def);
  return { build, ms: now() - t0 };
}
