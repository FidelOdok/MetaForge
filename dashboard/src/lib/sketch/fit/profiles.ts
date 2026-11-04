/**
 * Closed-profile detection: which primitives bound a region.
 *
 * A circle, an ellipse or a closed spline is a profile on its own. Open
 * primitives (lines, arcs, open splines) form a profile when their ends meet
 * in pairs all the way round: every end within `gap` of exactly one other
 * end, and the chain connected.
 */

import { endpointKeys, isClosedPrimitive, pointOf, type ClosedProfile, type Primitive } from '../model';

export function findClosedProfiles(primitives: readonly Primitive[], gap: number): ClosedProfile[] {
  const out: ClosedProfile[] = [];
  const ends: { prim: number; x: number; y: number }[] = [];
  primitives.forEach((p, i) => {
    if (p.construction) return;
    if (isClosedPrimitive(p)) {
      out.push({ primitive_ids: [p.id] });
      return;
    }
    for (const key of endpointKeys(p)) {
      const pt = pointOf(p, key);
      if (pt) ends.push({ prim: i, x: pt[0], y: pt[1] });
    }
  });

  // Cluster ends within the gap.
  const parent = ends.map((_, i) => i);
  const find = (i: number): number => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]!]!;
      i = parent[i]!;
    }
    return i;
  };
  for (let i = 0; i < ends.length; i += 1)
    for (let j = i + 1; j < ends.length; j += 1) {
      const a = ends[i]!;
      const b = ends[j]!;
      if (Math.hypot(a.x - b.x, a.y - b.y) <= gap) parent[find(i)] = find(j);
    }
  const clusterSize = new Map<number, number>();
  ends.forEach((_, i) => clusterSize.set(find(i), (clusterSize.get(find(i)) ?? 0) + 1));

  // Primitives connected through shared clusters.
  const primParent = primitives.map((_, i) => i);
  const pfind = (i: number): number => {
    while (primParent[i] !== i) {
      primParent[i] = primParent[primParent[i]!]!;
      i = primParent[i]!;
    }
    return i;
  };
  const firstInCluster = new Map<number, number>();
  ends.forEach((e, i) => {
    const c = find(i);
    const first = firstInCluster.get(c);
    if (first === undefined) firstInCluster.set(c, e.prim);
    else primParent[pfind(e.prim)] = pfind(first);
  });

  const groups = new Map<number, number[]>();
  ends.forEach((e, i) => {
    const g = pfind(e.prim);
    const list = groups.get(g) ?? [];
    list.push(i);
    groups.set(g, list);
  });
  for (const endIdx of groups.values()) {
    // Closed when every end of the group meets exactly one other end.
    if (!endIdx.every((i) => clusterSize.get(find(i)) === 2)) continue;
    const ids = [...new Set(endIdx.map((i) => ends[i]!.prim))].sort((a, b) => a - b).map((i) => primitives[i]!.id);
    // One open primitive meeting itself (a stroke that closes) still counts.
    out.push({ primitive_ids: ids });
  }
  return out;
}
