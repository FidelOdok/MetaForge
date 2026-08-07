import type { ExplodeDirection } from '../types/viewer';

/**
 * Compute a part's exploded-view offset from its position relative to the
 * assembly center.
 *
 * "radial" spreads each part outward along its own direction from the
 * assembly center (the full 3D vector). "axial" instead spreads parts apart
 * along a single stacking axis (Y) only, keeping X/Z fixed — the classic
 * CAD exploded-view look for stacked assemblies.
 *
 * Extracted as a pure function (rather than inlined in SceneContents, which
 * needs a full react-three-fiber Canvas + GLTF scene to render at all) so the
 * direction-dependent math itself is unit-testable.
 */
export function computeExplodeOffset(
  fromCenter: { x: number; y: number; z: number },
  direction: ExplodeDirection,
  explodeFactor: number,
): { x: number; y: number; z: number } {
  const scale = explodeFactor * 2;
  if (direction === 'axial') {
    return { x: 0, y: fromCenter.y * scale, z: 0 };
  }
  return { x: fromCenter.x * scale, y: fromCenter.y * scale, z: fromCenter.z * scale };
}
