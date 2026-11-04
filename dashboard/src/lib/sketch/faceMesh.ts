import type { CandidateFace } from '../../api/endpoints/sketchPlanes';

/**
 * Face picking on a converted GLB (CDE slice 9).
 *
 * `cadquery.describe_faces` says which triangles of the converted GLB each
 * planar face owns (`mesh.triangle_start` / `triangle_count`, per part). A
 * range is trusted only after it is checked against the face's plane: every
 * triangle in it must lie on that plane. If a range does not check out (a
 * different tessellation, an older converter), the face's triangles are found
 * by geometry instead: on its plane, facing its way, inside its bounding box.
 * Pure: no three.js, so it runs in tests and in a worker alike.
 */

export interface MeshLike {
  /** xyz per vertex. */
  positions: ArrayLike<number>;
  /** Three vertex indices per triangle; `null` for a non-indexed mesh. */
  index: ArrayLike<number> | null;
}

export function triangleCount(mesh: MeshLike): number {
  return Math.floor((mesh.index ? mesh.index.length : mesh.positions.length / 3) / 3);
}

type V3 = [number, number, number];

const at = (arr: ArrayLike<number>, i: number): number => Number(arr[i] ?? 0);

function vertexIndex(mesh: MeshLike, tri: number, corner: number): number {
  return mesh.index ? at(mesh.index, tri * 3 + corner) : tri * 3 + corner;
}

function vertex(mesh: MeshLike, tri: number, corner: number): V3 {
  const i = vertexIndex(mesh, tri, corner);
  return [at(mesh.positions, i * 3), at(mesh.positions, i * 3 + 1), at(mesh.positions, i * 3 + 2)];
}

function corners(mesh: MeshLike, tri: number): [V3, V3, V3] {
  return [vertex(mesh, tri, 0), vertex(mesh, tri, 1), vertex(mesh, tri, 2)];
}

function centreOf([a, b, c]: [V3, V3, V3]): V3 {
  return [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3];
}

/** A plane tolerance for a part: a ten-thousandth of its size, never under a micron. */
export function planeTolerance(partSize: number | null | undefined): number {
  return Math.max(1e-3, (partSize ?? 0) * 1e-4);
}

/** True when the triangle lies on the face's plane and faces the same way. */
export function triangleOnFace(mesh: MeshLike, tri: number, face: CandidateFace, tol: number): boolean {
  const [nx, ny, nz] = face.normal;
  const pts = corners(mesh, tri);
  for (const p of pts) {
    if (Math.abs(nx * p[0] + ny * p[1] + nz * p[2] - face.offset) > tol) return false;
  }
  const [a, b, c] = pts;
  const u: V3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v: V3 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n: V3 = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  const len = Math.hypot(n[0], n[1], n[2]);
  // A degenerate sliver says nothing about direction; its plane check is enough.
  if (len < 1e-12) return true;
  // Winding may be flipped by the converter; the plane is what matters.
  return Math.abs((n[0] * nx + n[1] * ny + n[2] * nz) / len) > 0.99;
}

/** The face's own triangle range, when it fits the mesh and checks out. */
export function verifiedRange(mesh: MeshLike, face: CandidateFace, tol: number): number[] | null {
  const range = face.mesh;
  if (!range || range.triangle_count <= 0) return null;
  const end = range.triangle_start + range.triangle_count;
  if (range.triangle_start < 0 || end > triangleCount(mesh)) return null;
  const out: number[] = [];
  for (let t = range.triangle_start; t < end; t += 1) {
    if (!triangleOnFace(mesh, t, face, tol)) return null;
    out.push(t);
  }
  return out;
}

function insideBox(p: V3, box: Record<string, number> | null, tol: number): boolean {
  if (!box) return true;
  const lo: V3 = [box.min_x ?? -Infinity, box.min_y ?? -Infinity, box.min_z ?? -Infinity];
  const hi: V3 = [box.max_x ?? Infinity, box.max_y ?? Infinity, box.max_z ?? Infinity];
  return [0, 1, 2].every((k) => {
    const i = k as 0 | 1 | 2;
    return p[i] >= lo[i] - tol && p[i] <= hi[i] + tol;
  });
}

/** The face's triangles by geometry alone (the fallback when its range does not check out). */
export function trianglesByGeometry(mesh: MeshLike, face: CandidateFace, tol: number): number[] {
  const out: number[] = [];
  const total = triangleCount(mesh);
  for (let t = 0; t < total; t += 1) {
    if (!triangleOnFace(mesh, t, face, tol)) continue;
    if (insideBox(centreOf(corners(mesh, t)), face.bounding_box, tol)) out.push(t);
  }
  return out;
}

/** Every triangle of the face: its checked range, else by geometry. */
export function faceTriangles(mesh: MeshLike, face: CandidateFace, tol: number): number[] {
  return verifiedRange(mesh, face, tol) ?? trianglesByGeometry(mesh, face, tol);
}

/**
 * The planar face under triangle `tri`, or `null` (a curved face, or two
 * faces that both claim it). Never guesses between candidates.
 */
export function pickFace(mesh: MeshLike, faces: CandidateFace[], tri: number, tol: number): CandidateFace | null {
  for (const face of faces) {
    const r = face.mesh;
    if (r && tri >= r.triangle_start && tri < r.triangle_start + r.triangle_count) {
      if (triangleOnFace(mesh, tri, face, tol)) return face;
      break;
    }
  }
  const centre = centreOf(corners(mesh, tri));
  const hits = faces.filter((f) => triangleOnFace(mesh, tri, f, tol) && insideBox(centre, f.bounding_box, tol));
  return hits.length === 1 ? (hits[0] ?? null) : null;
}

/** Vertex indices of `triangles`, for a highlight geometry sharing the mesh's positions. */
export function highlightIndices(mesh: MeshLike, triangles: number[]): number[] {
  const out: number[] = [];
  for (const t of triangles) {
    for (let k = 0; k < 3; k += 1) out.push(vertexIndex(mesh, t, k));
  }
  return out;
}

const DIRECTIONS: [number, number, number, string][] = [
  [0, 0, 1, 'top'],
  [0, 0, -1, 'bottom'],
  [0, -1, 0, 'front'],
  [0, 1, 0, 'back'],
  [1, 0, 0, 'right'],
  [-1, 0, 0, 'left'],
];

/** A short person-facing name for a face: its feature tag, else the way it faces. */
export function faceLabel(face: CandidateFace): string {
  if (face.semantic_tag) {
    const parts = face.semantic_tag.split(':');
    const role = parts[2] ?? '';
    const prim = parts[3] ? ` ${parts[3]}` : '';
    return `${role || 'face'}${prim} of ${parts[0]}`;
  }
  const [x, y, z] = face.normal;
  const hit = DIRECTIONS.find(([a, b, c]) => a * x + b * y + c * z > 0.999);
  return hit ? `${hit[3]} face` : 'slanted face';
}
