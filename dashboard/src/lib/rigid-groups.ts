import type { ModelManifest, ResolvedRigidGroup } from '../types/viewer';

/**
 * Resolve a manifest's rigid groups into the *actual* GLB scene mesh names the
 * gizmo can drive (MET-519/522).
 *
 * `sceneMeshNames` is the ordered list of mesh names as they appear in the
 * loaded GLB scene (e.g. `["mesh_0", "mesh_1", ...]`). We resolve groups
 * against these, **by index**, rather than the manifest's part names — because
 * the OCCT converter names the manifest parts `Part_1..N` but the GLB meshes
 * `mesh_0..N-1`, so matching on the manifest name never finds the clicked mesh.
 * Index alignment holds because the converter emits parts and meshes in the
 * same order.
 *
 * - With declared `rigidGroups`, each group's `partIndices` map to the
 *   scene mesh at that index (out-of-range indices dropped; empty groups
 *   skipped).
 * - Without them, every scene mesh becomes its own single-part group, named
 *   after the manifest part at that index when available (else the mesh name) —
 *   so single parts are still selectable/draggable.
 *
 * Pure and side-effect free (unit-testable without a WebGL context).
 */
export function parseRigidGroups(
  manifest: ModelManifest,
  sceneMeshNames: string[],
): ResolvedRigidGroup[] {
  if (sceneMeshNames.length === 0) return [];

  if (manifest.rigidGroups && manifest.rigidGroups.length > 0) {
    const resolved: ResolvedRigidGroup[] = [];
    for (const group of manifest.rigidGroups) {
      const meshNames = (group.partIndices ?? [])
        .map((i) => sceneMeshNames[i])
        .filter((m): m is string => typeof m === 'string' && m.length > 0);
      if (meshNames.length > 0) {
        resolved.push({ name: group.name, meshNames });
      }
    }
    return resolved;
  }

  // Fallback: one single-part group per scene mesh, labelled from the manifest
  // part at the same index when present.
  const parts = manifest.parts ?? [];
  return sceneMeshNames.map((mesh, i) => ({
    name: `${parts[i]?.name ?? mesh}_group`,
    meshNames: [mesh],
  }));
}

/** Look up the resolved group that owns a given mesh name, or null. */
export function groupForMesh(
  groups: ResolvedRigidGroup[],
  meshName: string,
): ResolvedRigidGroup | null {
  return groups.find((g) => g.meshNames.includes(meshName)) ?? null;
}
