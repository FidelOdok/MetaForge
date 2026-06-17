import type { ModelManifest, ResolvedRigidGroup } from '../types/viewer';

/**
 * Resolve a manifest's rigid groups into scene-graph mesh names the gizmo can
 * drive (MET-519).
 *
 * - When the manifest declares `rigidGroups`, each group's `partIndices` are
 *   resolved to the corresponding part mesh names (out-of-range indices are
 *   dropped; empty groups are skipped).
 * - When it doesn't, every top-level part becomes its own single-part group
 *   named `${part.name}_group`, so single parts are still selectable/draggable
 *   (the edge case called out in the spec).
 *
 * Pure and side-effect free so it can be unit-tested without a WebGL context.
 */
export function parseRigidGroups(manifest: ModelManifest): ResolvedRigidGroup[] {
  const parts = manifest.parts ?? [];

  if (manifest.rigidGroups && manifest.rigidGroups.length > 0) {
    const resolved: ResolvedRigidGroup[] = [];
    for (const group of manifest.rigidGroups) {
      const meshNames = (group.partIndices ?? [])
        .map((i) => parts[i]?.meshName)
        .filter((m): m is string => typeof m === 'string' && m.length > 0);
      if (meshNames.length > 0) {
        resolved.push({ name: group.name, meshNames });
      }
    }
    return resolved;
  }

  // Fallback: one single-part group per top-level part.
  return parts
    .filter((p) => typeof p.meshName === 'string' && p.meshName.length > 0)
    .map((p) => ({ name: `${p.name}_group`, meshNames: [p.meshName] }));
}

/** Look up the resolved group that owns a given mesh name, or null. */
export function groupForMesh(
  groups: ResolvedRigidGroup[],
  meshName: string,
): ResolvedRigidGroup | null {
  return groups.find((g) => g.meshNames.includes(meshName)) ?? null;
}
