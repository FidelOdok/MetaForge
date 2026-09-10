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
  // explodeFactor is a 0-100 percentage (the slider's native range, stored
  // as-is in viewer-store) -- normalize to 0-1 before scaling. At 100% a
  // part ends up 3x its original distance from the assembly center
  // (original position + 2x offset). Previously this treated the raw 0-100
  // value as an already-normalized 0-1 fraction, producing a 100x-too-large
  // explode distance that pushed every part far outside the camera's fitted
  // view -- the model would completely vanish above roughly 1% explode,
  // and "Reset view" couldn't recover it since the camera fit is itself
  // based on the model's un-exploded bounding radius.
  const scale = (explodeFactor / 100) * 2;
  if (direction === 'axial') {
    return { x: 0, y: fromCenter.y * scale, z: 0 };
  }
  return { x: fromCenter.x * scale, y: fromCenter.y * scale, z: fromCenter.z * scale };
}
